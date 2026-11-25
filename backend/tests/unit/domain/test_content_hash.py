from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from cerebro.domain.content_hash import content_hash, ensure_unchanged, is_content_hash
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import ContentConflict
from cerebro.domain.note import NoteId
from cerebro.domain.vocabulary import Area, NoteType
from tests.unit.domain.builders import make_note

CHANGES: dict[str, Any] = {
    "id": NoteId("other-id"),
    "title": "Another title",
    "type": NoteType("decision"),
    "area": Area("projects"),
    "priority": Priority.P3,
    "sensitivity": Sensitivity.PUBLIC,
    "status": Status.CURRENT,
    "summary": "A different summary.",
    "hub": None,
    "related": (),
    "superseded_by": NoteId("newer-note"),
    "lang": "pt-BR",
    "created": date(2026, 1, 1),
    "updated": date(2026, 12, 1),
    "review_by": date(2027, 1, 1),
    "body": "# Different\n",
}


def test_hash_is_stable_sha256_hex() -> None:
    h = content_hash(make_note())
    assert is_content_hash(h)
    assert h == content_hash(make_note())


@pytest.mark.parametrize("field", sorted(CHANGES))
def test_hash_changes_with_every_field(field: str) -> None:
    note = make_note()
    changed = replace(note, **{field: CHANGES[field]})
    assert content_hash(changed) != content_hash(note)


def test_body_whitespace_is_significant() -> None:
    assert content_hash(make_note(body="a\n")) != content_hash(make_note(body="a"))
    assert content_hash(make_note(body="a\n")) != content_hash(make_note(body="a\r\n"))


def test_unicode_body_hashes() -> None:
    assert is_content_hash(content_hash(make_note(body="Configuração ✓")))


def test_is_content_hash() -> None:
    assert not is_content_hash("A" * 64)
    assert not is_content_hash("a" * 63)
    assert not is_content_hash(None)


def test_ensure_unchanged() -> None:
    note = make_note()
    ensure_unchanged(content_hash(note), note)
    with pytest.raises(ContentConflict) as exc:
        ensure_unchanged(content_hash(note), make_note(body="edited"))
    assert exc.value.expected == content_hash(note)
    assert exc.value.actual == content_hash(make_note(body="edited"))


def test_lone_surrogates_hash_without_raising() -> None:
    assert is_content_hash(content_hash(make_note(body="a\ud800")))
