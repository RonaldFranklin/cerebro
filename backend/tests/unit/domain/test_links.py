import pytest

from cerebro.domain.links import (
    REDACTED_LINK,
    Link,
    LinkKind,
    WikiLink,
    extract_wikilinks,
    loose_link_targets,
    note_links,
    redact_wikilinks,
)
from cerebro.domain.note import NoteId
from tests.unit.domain.builders import make_note


def nid(value: str) -> NoteId:
    return NoteId(value)


def test_extracts_plain_and_section_links_in_order() -> None:
    body = "See [[alpha]] and [[beta#Setup steps]], then [[alpha#Intro]]."
    assert extract_wikilinks(body) == (
        WikiLink(target=nid("alpha")),
        WikiLink(target=nid("beta"), section="Setup steps"),
        WikiLink(target=nid("alpha"), section="Intro"),
    )


def test_duplicates_are_removed() -> None:
    assert extract_wikilinks("[[a]] [[a]] [[a#x]] [[a#x]]") == (
        WikiLink(target=nid("a")),
        WikiLink(target=nid("a"), section="x"),
    )


def test_whitespace_is_trimmed_and_empty_section_ignored() -> None:
    assert extract_wikilinks("[[ alpha # Part ]] [[beta#]]") == (
        WikiLink(target=nid("alpha"), section="Part"),
        WikiLink(target=nid("beta")),
    )


@pytest.mark.parametrize(
    "body",
    [
        "[[Not A Slug]]",
        "[[]]",
        "[[#only-section]]",
        "[[a|alias]]",
        "[[a]",
        "[a]",
        "[[a\nb]]",
        "[[[[nested]]]]x",
    ],
)
def test_malformed_or_non_id_links_are_ignored(body: str) -> None:
    assert [link.target.value for link in extract_wikilinks(body)] in ([], ["nested"])


def test_links_in_code_are_ignored() -> None:
    body = "```\n[[fenced]]\n```\nreal [[real]] and `[[inline]]` and ``[[double]]``\n~~~\n[[tilde]]\n~~~\n"
    assert extract_wikilinks(body) == (WikiLink(target=nid("real")),)


def test_note_links_cover_body_and_references() -> None:
    note = make_note(
        body="see [[alpha#Part]]",
        hub=nid("infrastructure"),
        related=(nid("r1"), nid("r2")),
        superseded_by=nid("newer"),
    )
    me = note.id
    assert note_links(note) == (
        Link(source=me, target=nid("alpha"), kind=LinkKind.BODY, section="Part"),
        Link(source=me, target=nid("infrastructure"), kind=LinkKind.HUB),
        Link(source=me, target=nid("r1"), kind=LinkKind.RELATED),
        Link(source=me, target=nid("r2"), kind=LinkKind.RELATED),
        Link(source=me, target=nid("newer"), kind=LinkKind.SUPERSEDED_BY),
    )


def test_note_links_without_references() -> None:
    note = make_note(body="nothing", hub=None, related=())
    assert note_links(note) == ()


def test_redact_wikilinks_replaces_links_the_reader_may_not_follow() -> None:
    body = "a [[open]] b [[secret]] c [[secret#Part]] d [[ secret | alias ]] e [[Not A Slug]] f [[open#X]]"
    out = redact_wikilinks(body, lambda target: target.value == "open")
    assert out == f"a [[open]] b {REDACTED_LINK} c {REDACTED_LINK} d {REDACTED_LINK} e [[Not A Slug]] f [[open#X]]"
    assert "secret" not in out


def test_redact_wikilinks_also_covers_code() -> None:
    out = redact_wikilinks("`[[secret]]`\n```\n[[secret]]\n```\n", lambda _: False)
    assert "secret" not in out


def test_loose_link_targets_cover_code_and_alias_forms() -> None:
    body = "[[a]] `[[b]]` ```\n[[c#x]]\n``` [[ d | alias ]] [[Not A Slug]] [[a]]"
    assert [t.value for t in loose_link_targets(body)] == ["a", "b", "c", "d"]
