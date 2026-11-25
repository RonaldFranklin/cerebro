"""YAML hardening and YAML 1.1 round trips of the filesystem adapter."""

import random
import string

import pytest

from cerebro.adapters.outbound.filesystem.codec import NoteFormatError, parse_note_bytes, split_frontmatter
from cerebro.adapters.outbound.filesystem.yaml_io import MAX_YAML_CHARS, YamlRejectedError, dump_mapping, load_mapping

YAML_1_1_SPECIALS = [
    "y", "Y", "n", "N", "yes", "Yes", "YES", "no", "No", "NO", "true", "True", "TRUE", "false", "False", "FALSE",
    "on", "On", "ON", "off", "Off", "OFF", "null", "Null", "NULL", "~", "=", "<<", "",
    "0", "1", "42", "-7", "+7", "0o17", "0x1f", "0b101", "017", "1_000", "1,000", "1e3", "1E3", "1.5", "-.5", ".5",
    "1:30", "190:20:30", ".inf", "-.inf", ".nan", ".NaN", "2026-11-24", "2026-1-4", "2001-12-14t21:59:43.10-05:00",
    "2001-12-14 21:59:43.10 -5", "2026-11-24T10:00:00Z",
]  # fmt: skip

HOSTILE_DOCUMENTS = {
    "alias": "a: &x value\nb: *x\n",
    "anchor_only": "a: &x value\n",
    "billion_laughs": (
        "a: &a [x, x, x, x, x, x, x, x, x]\n"
        "b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
        "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b]\n"
        "d: &d [*c, *c, *c, *c, *c, *c, *c, *c, *c]\n"
        "e: &e [*d, *d, *d, *d, *d, *d, *d, *d, *d]\n"
        "f: &f [*e, *e, *e, *e, *e, *e, *e, *e, *e]\n"
    ),
    "python_object": "a: !!python/object/apply:os.system ['echo pwned']\n",
    "python_name": "a: !!python/name:os.system\n",
    "custom_tag": "a: !custom value\n",
    "explicit_str_tag": "a: !!str value\n",
    "merge_key": "base: &b {x: y}\nother:\n  <<: *b\n",
    "duplicate_key": "title: one\ntitle: two\n",
    "complex_key": "? [a, b]\n: value\n",
    "list_document": "- a\n- b\n",
    "scalar_document": "just text\n",
    "empty_document": "",
    "two_documents": "a: b\n---\nc: d\n",
    "deep_nesting": "a: " + "[" * 50 + "x" + "]" * 50 + "\n",
    "huge_scalar": "a: " + "x" * (MAX_YAML_CHARS + 1) + "\n",
    "many_nodes": "a: [" + ", ".join(["x"] * 6000) + "]\n",
    "control_characters": "a: \x00\x01\n",
    "broken": "a: [unclosed\n",
}


@pytest.mark.parametrize("text", YAML_1_1_SPECIALS)
def test_yaml_1_1_special_strings_round_trip_as_strings(text: str) -> None:
    dumped = dump_mapping({"title": text, "items": [text, "plain"]})
    assert load_mapping(dumped) == {"title": text, "items": [text, "plain"]}


@pytest.mark.parametrize("text", [s for s in YAML_1_1_SPECIALS if s])
def test_unquoted_specials_in_a_file_are_read_as_text(text: str) -> None:
    if text in {"null", "Null", "NULL", "~"}:
        assert load_mapping(f"k: {text}\n") == {"k": None}
    else:
        assert load_mapping(f"k: {text}\n") == {"k": text}


def test_null_and_empty_values_stay_none() -> None:
    assert load_mapping("a: null\nb: ~\nc:\n") == {"a": None, "b": None, "c": None}


def test_randomised_strings_round_trip() -> None:
    rng = random.Random(1234)
    alphabet = (
        string.printable
        + "äöü日本語✓é"
        + "".join(map(chr, (0xA0, 0x85, 0x2028, 0x2029, 0xFEFF)))
        + "'\"\\:#-?[]{}&*!|>%@`,"
    )
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        mapping = {"a": text, "b": [text, text[::-1]]}
        assert load_mapping(dump_mapping(mapping)) == mapping, repr(text)


def test_dump_keeps_key_order_uses_block_style_and_no_aliases() -> None:
    shared = ["x", "z"]
    text = dump_mapping({"zeta": shared, "alpha": shared, "mid": "é"})
    assert text == "zeta:\n- x\n- z\nalpha:\n- x\n- z\nmid: é\n"
    assert "&" not in text
    assert "*" not in text


@pytest.mark.parametrize("name", sorted(HOSTILE_DOCUMENTS))
def test_hostile_yaml_is_rejected(name: str) -> None:
    with pytest.raises(YamlRejectedError):
        load_mapping(HOSTILE_DOCUMENTS[name])


def test_rejection_messages_never_quote_the_input() -> None:
    with pytest.raises(YamlRejectedError) as info:
        load_mapping("secret-marker: &x value\nb: *x\n")
    assert "secret-marker" not in str(info.value)


def test_loading_does_not_execute_anything(tmp_path: object) -> None:
    import os

    marker = os.path.join(str(tmp_path), "pwned")
    with pytest.raises(YamlRejectedError):
        load_mapping(f"a: !!python/object/apply:os.mkdir ['{marker}']\n")
    assert not os.path.exists(marker)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("no delimiter\n", "does not start"),
        ("---\ntitle: x\n", "no closing"),
        ("--- \ntitle: x\n---\n", "does not start"),
    ],
)
def test_split_frontmatter_is_strict_about_delimiters(text: str, reason: str) -> None:
    with pytest.raises(NoteFormatError, match=reason):
        split_frontmatter(text)


def test_split_frontmatter_handles_bom_crlf_and_keeps_the_body_exact() -> None:
    assert split_frontmatter("﻿---\r\na: b\r\n---\r\nbody\r\nmore") == ("a: b\r\n", "body\r\nmore")
    assert split_frontmatter("---\na: b\n---") == ("a: b\n", "")
    assert split_frontmatter("---\na: b\n---\n") == ("a: b\n", "")
    assert split_frontmatter("---\na: b\n---\n---\nx\n") == ("a: b\n", "---\nx\n")


def test_parse_note_bytes_rejects_bad_files() -> None:
    with pytest.raises(NoteFormatError, match="UTF-8"):
        parse_note_bytes(b"---\n\xff\n---\n")
    with pytest.raises(NoteFormatError, match="larger"):
        parse_note_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(NoteFormatError, match="invalid frontmatter"):
        parse_note_bytes(b"---\ntitle: only\n---\nbody\n")
    with pytest.raises(NoteFormatError, match="invalid frontmatter"):
        parse_note_bytes(b"---\na: &x 1\nb: *x\n---\n")
