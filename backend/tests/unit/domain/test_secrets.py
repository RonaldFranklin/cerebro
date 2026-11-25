"""Secret scanner tests. Fixtures are assembled at runtime so no live-looking secret sits in the source."""

import random
import string

import pytest

from cerebro.domain.enums import Priority
from cerebro.domain.note import NoteId
from cerebro.domain.report import OWNER_EDIT_POLICY, PROPOSAL_POLICY, Finding, Severity, ViolationCode
from cerebro.domain.secrets import scan_note, scan_text, shannon_entropy
from cerebro.domain.validation import validate_note
from tests.unit.domain.builders import VOCABULARY, make_note

_RNG = random.Random(1234)
_ALNUM = string.ascii_letters + string.digits


def rand(length: int, alphabet: str = _ALNUM) -> str:
    return "".join(_RNG.choice(alphabet) for _ in range(length))


def pem_header(kind: str = "RSA ") -> str:
    return "-----" + "BEGIN " + kind + "PRIVATE KEY" + "-----"


def jwt() -> str:
    return "ey" + "J" + rand(20) + ".ey" + "J" + rand(20) + "." + rand(24)


TOKENS = {
    "private key": pem_header(),
    "private key (openssh)": pem_header("OPENSSH "),
    "private key (pgp)": "-----" + "BEGIN PGP PRIVATE KEY BLOCK" + "-----",
    "aws": "AK" + "IA" + rand(16, string.ascii_uppercase + string.digits),
    "github classic": "gh" + "p_" + rand(36),
    "github oauth": "gh" + "o_" + rand(36),
    "github fine grained": "github" + "_pat_" + rand(40),
    "gitlab": "gl" + "pat-" + rand(20),
    "slack": "xo" + "xb-" + rand(12, string.digits) + "-" + rand(24),
    "stripe": "sk" + "_live_" + rand(24),
    "google": "AI" + "za" + rand(35),
    "anthropic": "sk-" + "ant-" + rand(40),
    "openai": "sk-" + "proj-" + rand(40),
    "npm": "np" + "m_" + rand(36),
    "sendgrid": "S" + "G." + rand(22) + "." + rand(43),
    "jwt": jwt(),
}


def messages(findings: tuple[Finding, ...]) -> list[str]:
    return [f.message for f in findings]


@pytest.mark.parametrize("secret", list(TOKENS.values()), ids=list(TOKENS))
def test_token_formats_are_detected(secret: str) -> None:
    findings = scan_text(f"Use this: {secret} for access", field="body")
    assert [f.code for f in findings] == [ViolationCode.SECRET_DETECTED]
    assert findings[0].field == "body"
    assert "line 1" in findings[0].message


def test_bearer_token_is_detected() -> None:
    value = rand(10) + "1" + rand(20)
    assert scan_text("Authorization: Bearer " + value, field="body")


def test_bearer_prose_is_not_detected() -> None:
    assert scan_text("Bearer authentication-scheme is described in the RFC", field="body") == ()


def test_url_credentials_are_detected_but_placeholders_are_not() -> None:
    assert scan_text("postgres://admin:" + rand(14) + "@db.internal/app", field="body")
    assert scan_text("postgres://user:<password>@db/app", field="body") == ()
    assert scan_text("postgres://user:$DB_PASSWORD@db/app", field="body") == ()
    assert scan_text("https://example.org/path/to/page?x=1", field="body") == ()


@pytest.mark.parametrize(
    "line",
    [
        "password=" + "Tr0ub4dor" + "&3",
        "password: " + "hunter2" + "hunter2",
        "api_key = " + rand(20),
        "API-KEY: " + rand(20),
        "secret: " + "Aa" + rand(10),
        'client_secret = "' + "correct horse battery" + '"',
        '"password": "' + "plainlongvalue" + '"',
        "access_token=" + rand(24),
        "export DB_PASSWORD=" + "s3cr3t" + "value1",
    ],
)
def test_credential_assignments_are_detected(line: str) -> None:
    assert [f.code for f in scan_text(line, field="body")] == [ViolationCode.SECRET_DETECTED]


@pytest.mark.parametrize(
    "line",
    [
        "password: stored in the password manager",
        "password: <your password>",
        "password=${DB_PASSWORD}",
        "password=changeme",
        "password: xxxxxxxxxxxx",
        "secret: vaultwarden-item-name",
        "password=short",
        "token: 1500",
        "api_key=$API_KEY",
        'password = os.environ["DB_PASSWORD"]',
        "The password is in Vaultwarden, item server-ssh.",
        "tokens=abcdefghijk1234",
        "secret: example-value-123",
    ],
)
def test_placeholders_and_references_are_not_credentials(line: str) -> None:
    assert scan_text(line, field="body") == ()


def test_high_entropy_string_is_detected() -> None:
    assert scan_text("key " + rand(40), field="body")


def test_entropy_helper() -> None:
    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaa") == 0.0
    assert shannon_entropy("abab") == pytest.approx(1.0)


def test_entropy_helper_orders_random_above_prose() -> None:
    assert shannon_entropy(rand(40)) > shannon_entropy("server-docker-conventions-for-the-home-lab")


@pytest.mark.parametrize(
    "text",
    [
        "Commit 83dc8a7 fixed it",
        "Commit 83dc8a7f1e2d3c4b5a69788796a5b4c3d2e1f001 fixed it",
        "sha256 " + "ab12" * 16,
        "id 123e4567-e89b-12d3-a456-426614174000 is a uuid",
        "Plain prose about the home server and how to reach it over SSH from the workstation.",
        "See https://docs.example.org/guides/getting-started/installing-the-server-on-linux-2026 for details",
        "See [[server-docker-conventions-and-compose-file-layout-2026]] and [[a#Section-name]]",
        "note id server-docker-conventions-and-compose-layout-2026 and snake_case_identifier_with_many_parts_123",
        "def compute_total_with_tax_for_each_line_item_in_order_2(order): ...",
        "ThisIsAVeryLongCamelCaseIdentifierNameWithoutDigits",
        "/home/user/projects/cerebro/backend/src/cerebro/domain/secrets.py",
        "",
    ],
)
def test_ordinary_text_is_not_flagged(text: str) -> None:
    assert scan_text(text, field="body") == ()


def test_line_numbers_are_one_based_and_per_line() -> None:
    body = "first\n\nsecond\n" + TOKENS["github classic"] + "\n"
    (finding,) = scan_text(body, field="body")
    assert "line 4" in finding.message


def test_overlapping_detectors_report_once() -> None:
    assert len(scan_text("api_key=" + TOKENS["github classic"], field="body")) == 1


def test_secrets_in_fenced_code_blocks_are_detected() -> None:
    body = "# Setup\n\n```bash\nexport TOKEN=" + TOKENS["github classic"] + "\n```\n"
    assert scan_note(make_note(body=body))


@pytest.mark.parametrize("field", ["title", "summary"])
def test_secrets_in_frontmatter_text_are_detected(field: str) -> None:
    text = "Server " + TOKENS["aws"] + " access notes, kept here for the runbook of the server"
    findings = scan_note(make_note(**{field: text}))
    assert [(f.code, f.field) for f in findings] == [(ViolationCode.SECRET_DETECTED, field)]


@pytest.mark.parametrize("field", ["id", "hub", "superseded_by"])
def test_high_entropy_note_ids_are_detected(field: str) -> None:
    ident = NoteId(rand(40, string.ascii_lowercase + string.digits) + "x1")
    findings = scan_note(make_note(**{field: ident}))
    assert [f.field for f in findings] == [field]


def test_related_and_lang_are_scanned() -> None:
    assert [f.field for f in scan_note(make_note(lang=TOKENS["aws"]))] == ["lang"]
    ident = NoteId(rand(40, string.ascii_lowercase + string.digits) + "x1")
    assert [f.field for f in scan_note(make_note(related=(ident,)))] == ["related"]


def test_clean_note_has_no_findings() -> None:
    assert scan_note(make_note()) == ()


@pytest.mark.parametrize("secret", list(TOKENS.values()), ids=list(TOKENS))
def test_findings_never_contain_the_secret(secret: str) -> None:
    note = make_note(title=f"t {secret}", summary=f"s {secret}" + "x" * 40, body=f"line\npassword={secret}\n{secret}\n")
    findings = scan_note(note)
    assert findings
    for finding in findings:
        for text in (finding.message, repr(finding), str(finding), finding.field or ""):
            assert secret not in text
            assert secret[4:] not in text  # not even most of it
    report = validate_note(note, VOCABULARY, PROPOSAL_POLICY)
    assert secret not in repr(report)
    assert secret[4:] not in repr(report)


def test_preview_is_at_most_a_few_characters_and_only_for_long_secrets() -> None:
    (long_finding,) = scan_text(TOKENS["github classic"], field="body")
    assert "starts with 'gh'" in long_finding.message
    (short_finding,) = scan_text("password=" + "Ab1" + "defgh", field="body")
    assert "starts with" not in short_finding.message


@pytest.mark.parametrize("policy", [PROPOSAL_POLICY, OWNER_EDIT_POLICY])
def test_secret_is_an_error_under_both_policies(policy: object) -> None:
    from cerebro.domain.report import SeverityPolicy

    assert isinstance(policy, SeverityPolicy)
    assert policy.severity(ViolationCode.SECRET_DETECTED) is Severity.ERROR
    report = validate_note(make_note(body=TOKENS["github classic"]), VOCABULARY, policy)
    assert not report.ok
    assert ViolationCode.SECRET_DETECTED in report.codes


def test_validate_note_reports_secrets_together_with_other_problems() -> None:
    report = validate_note(make_note(priority=Priority.P0, body=TOKENS["aws"]), VOCABULARY, PROPOSAL_POLICY)
    assert {ViolationCode.SECRET_DETECTED, ViolationCode.PRIORITY_TYPE_MISMATCH} <= report.codes
