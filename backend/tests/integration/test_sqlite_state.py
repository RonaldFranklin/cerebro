"""Integration tests of ``state.sqlite``: ``SqliteProposalStore`` (sealed payloads) and ``SqliteTokenStore``."""

import hashlib
import sqlite3
import stat
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from cerebro.adapters.outbound.crypto import FernetCipher, generate_key
from cerebro.adapters.outbound.sqlite import (
    SqliteProposalStore,
    SqliteTokenStore,
    StateCorruptionError,
    StateFileError,
    StateSchemaError,
)
from cerebro.application.dto import SubmitProposalRequest
from cerebro.application.errors import DecryptionError, ProposalConflict
from cerebro.application.ports import ProposalStore, TokenStore
from cerebro.domain.access import AccessCeiling, Scope
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.decision import Decision
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.note import NoteId
from cerebro.domain.proposal import Proposal, ProposalKind, ProposalStatus, ProposedContent
from cerebro.domain.report import Severity, ValidationReport, Violation, ViolationCode
from cerebro.domain.token_hash import TokenHash
from cerebro.domain.vocabulary import Area, NoteType
from tests.contract.stores_contract import ProposalStoreContract, TokenStoreContract, make_proposal, make_token
from tests.unit.application.write_kit import Kit, new_content
from tests.unit.domain.test_proposal import BAD, HASH, NOTE, OK, T0, WARN, at, make_update
from tests.unit.domain.test_proposal import make_proposal as build_proposal

MARKER = "zz-marker-7f3a"
S = ProposalStatus


@pytest.fixture
def cipher() -> FernetCipher:
    return FernetCipher([generate_key()])


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "state.sqlite"


class PlainCipher:
    """Identity 'cipher': only for tests that must look at what would be on disk without encryption."""

    def encrypt(self, plaintext: bytes) -> bytes:
        return plaintext

    def decrypt(self, ciphertext: bytes) -> bytes:
        return ciphertext


def raw_bytes(path: Path) -> bytes:
    return b"".join(p.read_bytes() for p in (path, Path(f"{path}-wal")) if p.exists())


# -- shared contracts ----------------------------------------------------------------------------------------------


class TestSqliteProposalStoreContract(ProposalStoreContract):
    @pytest.fixture
    def store(self, state_path: Path, cipher: FernetCipher) -> ProposalStore:
        return SqliteProposalStore(state_path, cipher)


class TestSqliteTokenStoreContract(TokenStoreContract):
    @pytest.fixture
    def store(self, state_path: Path) -> TokenStore:
        return SqliteTokenStore(state_path)


# -- exact round trip ----------------------------------------------------------------------------------------------


def rich_content() -> ProposedContent:
    return ProposedContent(
        title="Título com acentuação e emoji \U0001f9e0",
        body='# H\n\nLine "quoted" \\ backslash\n\u2028 weird \x00 nul\n',
        area=Area("infrastructure"),
        type=NoteType("runbook"),
        priority=Priority.P1,
        sensitivity=Sensitivity.INTERNAL,
        rationale="because\nreasons",
        related=(NoteId("a-note"), NoteId("b-note")),
        superseded_by=None,
        summary="A summary of reasonable length for this proposal content.",
        hub=NoteId("infrastructure"),
    )


def shapes() -> dict[str, Proposal]:
    detailed = ValidationReport(
        (
            Violation(
                code=ViolationCode.SECRET_DETECTED,
                field="body",
                message="m",
                severity=Severity.ERROR,
                detector="bearer token",
                line=3,
            ),
            Violation(code=ViolationCode.POSSIBLE_DUPLICATE, field="title", message="d", severity=Severity.WARNING),
        )
    )
    conflicted = at(S.APPROVED, update=True).mark_needs_owner(T0, conflict=True)
    shapes: dict[str, Proposal] = {f"{s.value}-create": at(s) for s in S}
    shapes |= {f"{s.value}-update": at(s, update=True) for s in S}
    shapes |= {
        "rich": build_proposal(content=rich_content(), source_session="sess-1", source_context="ctx é"),
        "empty-body": build_proposal(content=ProposedContent(title="t", body="")),
        "link": make_update(kind=ProposalKind.LINK, content=ProposedContent(related=(NoteId("x-note"),))),
        "deprecate": make_update(kind=ProposalKind.DEPRECATE, content=ProposedContent(superseded_by=NOTE)),
        "deprecate-bare": make_update(kind=ProposalKind.DEPRECATE, content=ProposedContent()),
        "detailed-report": build_proposal().reject_invalid(detailed, T0),
        "warnings-only": build_proposal().mark_validated(WARN, T0),
        "empty-report": build_proposal().mark_validated(OK, T0),
        "bad-report": build_proposal().reject_invalid(BAD, T0),
        "conflicted": conflicted,
        "rebased": conflicted.rebase("c" * 64, T0),
        "overridden": at(S.CURATED).override_decision(Decision.needs_owner("why", NOTE), T0),
        "non-utc": build_proposal(
            created_at=datetime(2026, 11, 25, 9, 30, 15, 123456, tzinfo=timezone(timedelta(hours=-3))),
            updated_at=datetime(2026, 11, 25, 9, 31, tzinfo=timezone(timedelta(hours=-3))),
        ),
        "with-hash": make_update(base_hash=HASH),
    }
    return shapes


@pytest.mark.parametrize("name", sorted(shapes()))
def test_every_proposal_shape_roundtrips_exactly(name: str, state_path: Path, cipher: FernetCipher) -> None:
    proposal = shapes()[name]
    store = SqliteProposalStore(state_path, cipher)
    store.add(replace(proposal, id="p-1"))
    stored = store.get("p-1")
    assert stored == replace(proposal, id="p-1")
    assert stored is not None
    assert stored.created_at.utcoffset() == proposal.created_at.utcoffset()  # same instant and same offset
    assert store.find() == (stored,)
    saved = store.save(stored, expected_revision=0)
    assert store.get("p-1") == saved == replace(proposal, id="p-1", revision=1)


def test_the_order_is_chronological_across_utc_offsets(state_path: Path, cipher: FernetCipher) -> None:
    store = SqliteProposalStore(state_path, cipher)
    zone = timezone(timedelta(hours=-3))
    store.add(make_proposal("p-a", at=datetime(2026, 11, 25, 10, 0, tzinfo=zone)))  # 13:00Z
    store.add(make_proposal("p-b", at=datetime(2026, 11, 25, 12, 0, tzinfo=UTC)))  # 12:00Z
    assert [p.id for p in store.find()] == ["p-b", "p-a"]


# -- persistence and concurrency -----------------------------------------------------------------------------------


def test_state_persists_across_reopen(state_path: Path, cipher: FernetCipher) -> None:
    SqliteProposalStore(state_path, cipher).add(make_proposal())
    SqliteTokenStore(state_path).add(make_token())
    assert SqliteProposalStore(state_path, cipher).get("proposal-0001") == make_proposal()
    assert SqliteTokenStore(state_path).get("token-0001") == make_token()


def test_two_writers_racing_from_the_same_revision_only_one_wins(state_path: Path, cipher: FernetCipher) -> None:
    first, second = SqliteProposalStore(state_path, cipher), SqliteProposalStore(state_path, cipher)
    first.add(make_proposal())
    loaded = first.get("proposal-0001")
    assert loaded is not None
    candidates = [loaded.mark_validated(OK, T0 + timedelta(minutes=n)) for n in (1, 2)]

    def attempt(args: tuple[SqliteProposalStore, Proposal]) -> bool:
        store, proposal = args
        try:
            store.save(proposal, expected_revision=0)
        except ProposalConflict:
            return False
        return True

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, [(first, candidates[0]), (second, candidates[1])]))
    assert sorted(results) == [False, True]
    stored = first.get("proposal-0001")
    assert stored is not None
    assert stored.revision == 1
    assert stored.updated_at in {c.updated_at for c in candidates}


# -- ciphertext at rest --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("sensitivity", [Sensitivity.PUBLIC, Sensitivity.RESTRICTED])
def test_nothing_readable_is_on_disk_even_for_public_content(
    state_path: Path, cipher: FernetCipher, sensitivity: Sensitivity
) -> None:
    store = SqliteProposalStore(state_path, cipher)
    proposal = build_proposal(
        content=ProposedContent(
            title=f"title {MARKER}", body=f"body {MARKER}", rationale=f"why {MARKER}", sensitivity=sensitivity
        ),
        source_context=f"context {MARKER}",
        target=None,
    )
    store.add(proposal)
    curated = at(S.CURATED).mark_needs_owner(T0)
    store.add(replace(curated, id="p-2", decision=Decision.needs_owner(f"reason {MARKER}", NoteId("target-note"))))
    with closing(sqlite3.connect(state_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    data = raw_bytes(state_path)
    assert b"SQLite format 3" in data
    for needle in (MARKER.encode(), b"target-note", b"SSH access"):
        assert needle not in data


def test_wrong_key_fails_with_decryption_error(state_path: Path, cipher: FernetCipher) -> None:
    SqliteProposalStore(state_path, cipher).add(make_proposal())
    other = SqliteProposalStore(state_path, FernetCipher([generate_key()]))
    with pytest.raises(DecryptionError):
        other.get("proposal-0001")
    with pytest.raises(DecryptionError):
        other.find()  # never silently skipped


def _payload_of(path: Path, proposal_id: str) -> bytes:
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT payload FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    return bytes(row[0])


def _sql(path: Path, statement: str, *params: object) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def test_a_flipped_byte_is_detected(state_path: Path, cipher: FernetCipher) -> None:
    store = SqliteProposalStore(state_path, cipher)
    store.add(make_proposal())
    payload = bytearray(_payload_of(state_path, "proposal-0001"))
    payload[len(payload) // 2] ^= 0x01
    _sql(state_path, "UPDATE proposals SET payload = ? WHERE id = ?", bytes(payload), "proposal-0001")
    with pytest.raises(DecryptionError):
        store.get("proposal-0001")


def test_a_payload_swapped_between_rows_is_detected(state_path: Path, cipher: FernetCipher) -> None:
    store = SqliteProposalStore(state_path, cipher)
    store.add(make_proposal("p-a"))
    store.add(make_proposal("p-b"))
    _sql(state_path, "UPDATE proposals SET payload = ? WHERE id = 'p-b'", _payload_of(state_path, "p-a"))
    assert store.get("p-a") is not None
    with pytest.raises(DecryptionError):
        store.get("p-b")


def test_clear_columns_that_disagree_with_the_sealed_payload_are_corruption(
    state_path: Path, cipher: FernetCipher
) -> None:
    store = SqliteProposalStore(state_path, cipher)
    store.add(make_proposal())
    _sql(state_path, "UPDATE proposals SET status = 'applied'")
    with pytest.raises(StateCorruptionError):
        store.get("proposal-0001")
    with pytest.raises(StateCorruptionError):
        store.find(status=S.APPLIED)  # never silently skipped


# -- file safety ---------------------------------------------------------------------------------------------------


def test_file_and_directory_modes(state_path: Path, cipher: FernetCipher) -> None:
    SqliteProposalStore(state_path, cipher).add(make_proposal())
    SqliteTokenStore(state_path)
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
    for sidecar in (Path(f"{state_path}-wal"), Path(f"{state_path}-shm")):
        if sidecar.exists():
            assert not stat.S_IMODE(sidecar.stat().st_mode) & 0o077


def test_a_symlink_in_place_of_the_file_is_refused(tmp_path: Path, cipher: FernetCipher) -> None:
    real = tmp_path / "real.sqlite"
    real.touch(mode=0o600)
    link = tmp_path / "state.sqlite"
    link.symlink_to(real)
    with pytest.raises(StateFileError):
        SqliteProposalStore(link, cipher)


def test_a_file_more_open_than_0600_is_refused(state_path: Path, cipher: FernetCipher) -> None:
    SqliteProposalStore(state_path, cipher)
    state_path.chmod(0o644)
    with pytest.raises(StateFileError):
        SqliteTokenStore(state_path)


def test_wal_and_foreign_keys_and_secure_delete(state_path: Path, cipher: FernetCipher) -> None:
    SqliteProposalStore(state_path, cipher)
    with closing(sqlite3.connect(state_path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_an_unknown_schema_version_is_refused_and_the_file_is_kept(state_path: Path, cipher: FernetCipher) -> None:
    SqliteProposalStore(state_path, cipher).add(make_proposal())
    _sql(state_path, "PRAGMA user_version = 99")
    with pytest.raises(StateSchemaError):
        SqliteProposalStore(state_path, cipher)
    _sql(state_path, "PRAGMA user_version = 1")
    assert SqliteProposalStore(state_path, cipher).get("proposal-0001") is not None


def test_a_damaged_file_is_reported_and_never_deleted(state_path: Path, cipher: FernetCipher) -> None:
    state_path.parent.mkdir(mode=0o700)
    state_path.write_bytes(b"this is not a database" * 100)
    state_path.chmod(0o600)
    with pytest.raises(StateCorruptionError):
        SqliteProposalStore(state_path, cipher)
    assert state_path.read_bytes().startswith(b"this is not a database")


def test_a_foreign_database_without_a_version_is_not_touched(state_path: Path, cipher: FernetCipher) -> None:
    state_path.parent.mkdir(mode=0o700)
    state_path.touch(mode=0o600)
    _sql(state_path, "CREATE TABLE other (x)")
    with pytest.raises(StateSchemaError):
        SqliteProposalStore(state_path, cipher)


# -- tokens --------------------------------------------------------------------------------------------------------


def test_token_hash_is_not_recoverable_from_the_store_repr(state_path: Path) -> None:
    store = SqliteTokenStore(state_path)
    token = make_token()
    store.add(token)
    digest = token.token_hash.hexdigest
    stored = store.get(token.id)
    assert stored is not None
    for text in (repr(store), repr(stored), str(stored), f"{stored}"):
        assert digest not in text


def test_token_roundtrip_with_every_field(state_path: Path) -> None:
    store = SqliteTokenStore(state_path)
    token = AgentToken(
        id="t-1",
        agent="codex@workstation",
        ceiling=AccessCeiling(Sensitivity.RESTRICTED),
        scopes=frozenset({Scope.READ, Scope.PROPOSE}),
        token_hash=TokenHash(hashlib.sha256(b"x").hexdigest()),
        created_at=T0,
        expires_at=T0 + timedelta(days=30),
    ).revoke(T0 + timedelta(days=1))
    store.add(token)
    assert store.get("t-1") == token
    assert store.find_by_hash(token.token_hash) == token
    assert store.all() == (token,)


def test_a_damaged_token_row_is_reported(state_path: Path) -> None:
    store = SqliteTokenStore(state_path)
    store.add(make_token())
    _sql(state_path, "UPDATE tokens SET ceiling = 'galaxy'")
    with pytest.raises(StateCorruptionError):
        store.get("token-0001")


# -- rejected raw payloads are not kept ----------------------------------------------------------------------------

PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmU\n-----END OPENSSH PRIVATE KEY-----\n"
)


def _kit(store: ProposalStore) -> Kit:
    return Kit(proposals=store)


@pytest.mark.parametrize("cipher_factory", [PlainCipher, lambda: FernetCipher([generate_key()])])
def test_a_rejected_proposal_leaves_nothing_of_its_payload(
    state_path: Path, cipher_factory: Callable[[], FernetCipher | PlainCipher]
) -> None:
    kit = _kit(SqliteProposalStore(state_path, cipher_factory()))
    pid = kit.submit_create()
    leaked = replace(
        kit.stored(pid),
        content=new_content(title=f"title {MARKER}", body=f"{MARKER} body\n{PRIVATE_KEY}", rationale=f"why {MARKER}"),
        source_context=f"ctx {MARKER}",
    )
    kit.overwrite(leaked)
    outcome = kit.validate(pid)
    assert outcome.status is S.REJECTED
    stored = kit.stored(pid)
    assert ViolationCode.SECRET_DETECTED in stored.validation.codes  # type: ignore[union-attr]
    assert MARKER not in repr(stored)
    with closing(sqlite3.connect(state_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    data = raw_bytes(state_path)
    for needle in (MARKER.encode(), b"PRIVATE KEY", b"b3BlbnNzaC1r"):
        assert needle not in data


def test_a_rejected_proposal_without_a_secret_keeps_findings_only(state_path: Path) -> None:
    kit = _kit(SqliteProposalStore(state_path, PlainCipher()))
    pid = kit.submit_create(title=f"title {MARKER}", body=f"{MARKER} body\n", area=Area("nowhere"))
    assert kit.validate(pid).status is S.REJECTED
    with closing(sqlite3.connect(state_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert MARKER.encode() not in raw_bytes(state_path)
    stored = kit.stored(pid)
    assert stored.validation is not None
    assert ViolationCode.UNKNOWN_AREA in stored.validation.codes
    assert stored.kind is ProposalKind.CREATE


def test_submit_and_validate_work_end_to_end_on_sqlite(state_path: Path, cipher: FernetCipher) -> None:
    kit = _kit(SqliteProposalStore(state_path, cipher))
    outcome = kit.submit(SubmitProposalRequest(kind=ProposalKind.CREATE, content=new_content()))
    assert kit.validate(outcome.proposal_id).status is S.VALIDATED
    assert kit.stored(outcome.proposal_id).revision == 1
