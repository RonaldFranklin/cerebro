"""Size of typical MCP responses as sent on the wire, against budgets derived from ``docs/vision.md``.

Vision: "a question like 'how do I access the server?' is answered with at most 3 tool calls and about 2-3k tokens",
and that cost stays flat as the knowledge base grows. Tokens are estimated as 4 UTF-8 bytes each.

Every tool result carries its payload twice: as ``content`` text and as ``structuredContent``. The token budget
applies to the text copy (one copy is what a client hands to the model); the wire budgets cap the bytes of the whole
HTTP response body, so both copies, JSON escaping and the envelope are counted.
"""

import json
from dataclasses import dataclass
from itertools import cycle, islice
from pathlib import Path

import pytest

from cerebro.application.dto import IssueAgentTokenRequest
from cerebro.bootstrap.container import build_container
from cerebro.domain.access import AccessCeiling, AccessContext, Scope
from cerebro.domain.budgets import word_count
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.note import Note, NoteId
from cerebro.domain.vocabulary import CORE, HUB, Area, NoteType
from tests.integration.content_kit import make_sample_repo
from tests.integration.test_cli_bootstrap import SAMPLE
from tests.integration.test_mcp_transports import call, free_port, post, serving
from tests.unit.domain.builders import make_note

BYTES_PER_TOKEN = 4
LOOKUP_TOKENS = 3000
"""docs/vision.md: the upper end of "about 2-3k tokens", for the whole lookup of at most three calls."""

# Bytes of one HTTP response body: the measured size of the fixture below plus roughly 25 percent of headroom.
WIRE_BUDGETS = {
    "tools/list": 4_700,  # once per session
    "overview": 14_100,  # grows with the core and the hubs only
    "search": 3_700,  # five summaries (the default limit)
    "read": 9_200,  # one runbook of about 450 words
    "write_guide": 8_100,  # once per writing session
}

_AREAS = ("infrastructure", "security")
_TARGET = NoteId("server-ssh-access")
_PARAGRAPH = (
    "Connect from the workstation with the dedicated key and confirm the host key fingerprint before accepting it. "
    "The service account has no password login; elevation goes through sudo and is recorded in the journal. If the "
    "connection is refused, check that the firewall rule for the management network is loaded and that the daemon "
    "restarted after the last configuration change."
)

type Responses = dict[str, "Measured"]


def _steps(count: int) -> str:
    return "".join(f"## Step {n}\n\n{_PARAGRAPH}\n\n```sh\nssh admin@server.example\n```\n\n" for n in range(count))


def _knowledge_base(other_notes: int) -> list[Note]:
    """An 800-word core, one hub per area, a runbook of about 450 words and ``other_notes`` more runbooks."""
    common: dict[str, object] = {"related": (), "review_by": None, "sensitivity": Sensitivity.INTERNAL}
    core = make_note(
        **common,
        id=NoteId("core-rules"),
        title="Core rules",
        type=CORE,
        priority=Priority.P0,
        hub=None,
        summary="Global rules every agent follows before changing anything on the owner's systems.",
        body=" ".join(islice(cycle(_PARAGRAPH.split()), 800)),
    )
    hubs = [
        make_note(
            **common,
            id=NoteId(f"{area}-hub"),
            title=f"{area.title()} hub",
            type=HUB,
            area=Area(area),
            priority=Priority.P1,
            hub=None,
            summary=f"Entry point to the {area} notes: what exists, where to start and which rules apply there.",
            body=_PARAGRAPH,
        )
        for area in _AREAS
    ]
    runbook: dict[str, object] = {**common, "type": NoteType("runbook"), "hub": NoteId("infrastructure-hub")}
    target = make_note(
        **runbook,
        id=_TARGET,
        title="Server SSH access",
        summary="How to reach the home server over SSH from the workstation and verify its host key.",
        body=_steps(7),
    )
    others = [
        make_note(
            **runbook,
            id=NoteId(f"server-maintenance-{number:03d}"),
            title=f"Server maintenance procedure number {number:03d}",
            summary="Routine maintenance of one server service: when to run it, what to check and how to undo it.",
            body=_steps(4),
        )
        for number in range(other_notes)
    ]
    return [core, *hubs, target, *others]


@dataclass(frozen=True)
class Measured:
    wire: int
    """Bytes of the HTTP response body."""
    text: str
    """The copy a model reads."""

    @property
    def tokens(self) -> int:
        return len(self.text.encode()) // BYTES_PER_TOKEN


def _measure(port: int, secret: str, payload: bytes) -> Measured:
    status, body = post(port, secret, payload)
    assert status == 200
    result = json.loads(body)["result"]
    if "tools" in result:
        return Measured(len(body), json.dumps(result["tools"], ensure_ascii=False))
    assert not result.get("isError"), result
    return Measured(len(body), result["content"][0]["text"])


def _responses(directory: Path, other_notes: int) -> Responses:
    """What the real server (git repository, encryption, SQLite index, HTTP listener) answers to a writing agent."""
    directory.mkdir()
    sample = make_sample_repo(directory, notes=_knowledge_base(other_notes))
    container = build_container(sample.settings)
    config = directory / "instance.toml"
    config.write_text(SAMPLE.replace("keys/fernet.key", "secrets/cerebro.key"))
    secret = container.use_cases.issue_agent_token.execute(
        IssueAgentTokenRequest(
            agent="fixture", scopes=frozenset({Scope.READ, Scope.WRITE}), ceiling=AccessCeiling(Sensitivity.INTERNAL)
        ),
        AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED)),
    ).secret
    payloads = {
        "tools/list": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode(),
        "overview": call("cerebro_overview", {}),
        "search": call("cerebro_search", {"query": "how do I access the server over SSH"}),
        "read": call("cerebro_read", {"id": _TARGET.value}),
        "write_guide": call("cerebro_write_guide", {}),
    }
    port = free_port()
    with serving(config, port):
        return {name: _measure(port, secret, payload) for name, payload in payloads.items()}


@pytest.fixture(scope="module")
def bases(tmp_path_factory: pytest.TempPathFactory) -> tuple[Responses, Responses]:
    """The same questions asked of a small knowledge base and of one six times larger."""
    root = tmp_path_factory.mktemp("budgets")
    return _responses(root / "small", 20), _responses(root / "large", 140)


def test_the_fixture_is_realistic() -> None:
    words = {note.id: word_count(note.body) for note in _knowledge_base(0)}
    assert words[NoteId("core-rules")] == 800
    assert words[_TARGET] >= 400


@pytest.mark.parametrize("name", WIRE_BUDGETS)
def test_each_response_fits_its_wire_budget(bases: tuple[Responses, Responses], name: str) -> None:
    for responses in bases:
        assert responses[name].wire <= WIRE_BUDGETS[name], f"{name}: {responses[name].wire} bytes on the wire"


def test_a_lookup_of_three_calls_fits_the_token_budget(bases: tuple[Responses, Responses]) -> None:
    for responses in bases:
        assert len(json.loads(responses["search"].text)["results"]) == 5
        assert _TARGET.value in responses["search"].text  # the second call finds what the third one reads
        assert word_count(json.loads(responses["overview"].text)["core"][0]["body"]) == 800
        spent = sum(responses[name].tokens for name in ("overview", "search", "read"))
        assert spent <= LOOKUP_TOKENS, f"a lookup costs about {spent} tokens"


def test_response_sizes_stay_flat_as_notes_are_added(bases: tuple[Responses, Responses]) -> None:
    small, large = bases
    for name in WIRE_BUDGETS:
        assert abs(large[name].wire - small[name].wire) <= 200, name
