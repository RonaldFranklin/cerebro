"""Calibration of the ``possible_duplicate`` warning on realistic notes, with the fake and the SQLite index."""

from dataclasses import replace
from pathlib import Path

import pytest

from cerebro.adapters.outbound.sqlite.search import SqliteSearchIndex
from cerebro.application.agent_write import AgentWriteRequest, WriteFinding
from cerebro.application.ports.notes import NoteChange
from cerebro.application.ports.search import SearchIndex
from cerebro.application.use_cases.agent_write import AgentWrite
from cerebro.domain.access import Scope
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import NoteId
from tests.fakes.search import InMemorySearchIndex
from tests.fakes.write_attempts import FakeWriteAttemptStore
from tests.unit.application.world import MESSAGE
from tests.unit.application.write_kit import Kit, agent_ctx
from tests.unit.domain.builders import VOCABULARY

WRITER = frozenset({Scope.READ, Scope.WRITE})
TITLE = "Nightly PostgreSQL backups with pgBackRest"
SUMMARY = (
    "How the home server takes a nightly PostgreSQL backup with pgBackRest, keeps two weeks of history "
    "and verifies that a restore works."
)
# A long, varied body: it must not dilute the similarity of the title and summary.
BODY = "".join(f"Step {n}: check stanza{n}, repository{n} and the timer{n} output.\n" for n in range(120))


@pytest.fixture(params=["fake", "sqlite"])
def index(request: pytest.FixtureRequest, tmp_path: Path) -> SearchIndex:
    if request.param == "fake":
        return InMemorySearchIndex()
    return SqliteSearchIndex(tmp_path / "index" / "index.sqlite")


def create(
    index: SearchIndex,
    kit: Kit,
    request_id: str,
    title: str = TITLE,
    summary: str = SUMMARY,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    scopes: frozenset[Scope] = WRITER,
) -> tuple[NoteId, tuple[WriteFinding, ...]]:
    """Create a note as an agent whose ceiling is ``sensitivity``; returns its id and the duplicate warnings."""
    w = kit.world
    use_case = AgentWrite(w.notes, index, w.graph, FakeWriteAttemptStore(), kit.clock, kit.ids, VOCABULARY, 60)
    result = use_case.execute(
        AgentWriteRequest(
            operation="create",
            request_id=request_id,
            body=BODY,
            frontmatter={
                "title": title,
                "summary": summary,
                "type": "guide",
                "area": "infrastructure",
                "priority": "P2",
                "sensitivity": sensitivity.value,
                "hub": "infrastructure",
            },
        ),
        replace(agent_ctx(sensitivity, scopes), token_id="token-one"),
    )
    return result.note_id, tuple(f for f in result.findings if f.code == "possible_duplicate")


@pytest.mark.parametrize(
    ("title", "summary"),
    [
        (TITLE, SUMMARY),
        ("Nightly PostgreSQL backup using pgBackRest", SUMMARY),
        (
            TITLE,
            "How the home server runs a nightly PostgreSQL backup with pgBackRest, retains two weeks of history "
            "and checks that restoring works.",
        ),
    ],
    ids=["identical", "retitled", "reworded"],
)
def test_near_identical_notes_warn_and_name_the_similar_note(index: SearchIndex, title: str, summary: str) -> None:
    kit = Kit()
    original, none = create(index, kit, "one")
    assert none == ()

    _, (warning,) = create(index, kit, "two", title, summary)

    assert warning.field == "title"
    assert f"`{original.value}`" in warning.hint


@pytest.mark.parametrize(
    ("title", "summary"),
    [
        (
            "Weekly meal plan for the family",
            "How the weekly dinners are chosen, which groceries are bought and what to cook when the week gets busy.",
        ),
        (
            "Zigbee network layout in the house",
            "Where the Zigbee routers are placed, which channel the coordinator uses and how to pair a new sensor.",
        ),
        (
            "Restoring a laptop from the backup disk",
            "What to do when the work laptop is lost: reinstall the system, then restore documents from the disk.",
        ),
    ],
    ids=["unrelated", "same-area", "same-theme"],
)
def test_unrelated_notes_do_not_warn(index: SearchIndex, title: str, summary: str) -> None:
    kit = Kit()
    create(index, kit, "one")

    assert create(index, kit, "two", title, summary)[1] == ()


def test_a_token_without_read_is_warned_without_an_id(index: SearchIndex) -> None:
    kit = Kit()
    original, _ = create(index, kit, "one")

    _, (warning,) = create(index, kit, "two", scopes=frozenset({Scope.WRITE}))

    assert original.value not in warning.hint
    assert "read" not in warning.hint.casefold()


def test_a_hidden_near_duplicate_neither_warns_nor_is_named(index: SearchIndex) -> None:
    kit = Kit()
    hidden, _ = create(index, kit, "one", sensitivity=Sensitivity.RESTRICTED)

    visible, warnings = create(index, kit, "two")
    assert warnings == ()

    _, (warning,) = create(index, kit, "three")
    assert f"`{visible.value}`" in warning.hint
    assert hidden.value not in warning.hint


def test_a_stale_index_entry_above_the_ceiling_is_not_named(index: SearchIndex) -> None:
    kit = Kit()
    original, _ = create(index, kit, "one")
    stored = kit.world.notes.get(original)
    assert stored is not None
    # The index still says internal; the repository, which decides visibility, says restricted.
    raised = replace(stored, sensitivity=Sensitivity.RESTRICTED)
    kit.world.notes.apply([NoteChange(note=raised, expected_hash=content_hash(stored))], MESSAGE)

    assert create(index, kit, "two")[1] == ()
