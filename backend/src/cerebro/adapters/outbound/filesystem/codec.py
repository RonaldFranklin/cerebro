"""Text form of a note file: ``---`` frontmatter ``---`` body.

Canonical form (what ``render_note`` writes)::

    ---\\n
    <YAML from note_to_raw, canonical key order, block style, ends with \\n>
    ---\\n
    <body, exactly the text of ``Note.body``: no newline added, none removed>

so ``parse_note_bytes(render_note(note)) == note`` and the content hash (``domain.content_hash``, computed from
the ``Note``, not from the bytes) is the same before and after a write. ``render_note`` proves this by parsing its
own output and refuses (``NoteFormatError``) any note that would not read back identically.

Reading is tolerant only where it cannot change the note: a leading UTF-8 byte order mark is dropped, and the two
delimiter lines and the YAML lines may end in ``\\r\\n``. The body is never touched: every ``\\r`` in it is kept.
The first line after the opening delimiter that is exactly ``---`` closes the frontmatter. A missing delimiter,
an unterminated frontmatter, invalid UTF-8, an oversized file or invalid frontmatter raise ``NoteFormatError``
with a fixed reason (never the content).
"""

from typing import Final

from cerebro.adapters.outbound.filesystem.yaml_io import YamlRejectedError, dump_mapping, load_mapping
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.frontmatter import note_to_raw, parse_note
from cerebro.domain.note import Note
from cerebro.domain.report import ValidationReport

MAX_NOTE_BYTES: Final = 256 * 1024
"""Largest plaintext note file, frontmatter and body together."""

_DELIMITER: Final = "---"


class NoteFormatError(ValueError):
    """A note text could not be read or written. ``reason`` is a fixed text, never note content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def split_frontmatter(text: str) -> tuple[str, str]:
    """``(yaml_text, body)`` of a note file text. See the module docstring for the rules."""
    text = text.removeprefix("﻿")
    if text.startswith(_DELIMITER + "\r\n"):
        start = len(_DELIMITER) + 2
    elif text.startswith(_DELIMITER + "\n"):
        start = len(_DELIMITER) + 1
    else:
        raise NoteFormatError("the file does not start with a '---' frontmatter delimiter")
    position = start
    while True:
        newline = text.find("\n", position)
        end = newline if newline != -1 else len(text)
        if text[position:end] in (_DELIMITER, _DELIMITER + "\r"):
            return text[start:position], text[newline + 1 :] if newline != -1 else ""
        if newline == -1:
            raise NoteFormatError("the frontmatter has no closing '---' delimiter")
        position = newline + 1


def parse_note_bytes(data: bytes) -> Note:
    """The note stored in ``data`` (a plaintext note file)."""
    if len(data) > MAX_NOTE_BYTES:
        raise NoteFormatError(f"note file is larger than {MAX_NOTE_BYTES // 1024} KiB")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise NoteFormatError("note file is not valid UTF-8") from None
    yaml_text, body = split_frontmatter(text)
    try:
        raw = load_mapping(yaml_text)
    except YamlRejectedError as exc:
        raise NoteFormatError(f"invalid frontmatter: {exc}") from None
    try:
        result = parse_note(raw, body)
    except InvalidValueError:
        raise NoteFormatError("invalid frontmatter: a value is not valid") from None
    if isinstance(result, ValidationReport):
        # Field names and violation codes only: the report's messages never quote values, but stay minimal.
        problems = sorted({f"{v.field or 'frontmatter'} ({v.code.value})" for v in result.violations})
        raise NoteFormatError("invalid frontmatter: " + ", ".join(problems[:10]))
    return result


def render_note(note: Note) -> bytes:
    """The canonical file bytes of ``note`` (plaintext). Raises ``NoteFormatError`` if it would not read back."""
    try:
        text = "---\n" + dump_mapping(note_to_raw(note)) + "---\n" + note.body
        data = text.encode("utf-8")
    except UnicodeEncodeError, YamlRejectedError:
        raise NoteFormatError("note text cannot be written (invalid characters)") from None
    if len(data) > MAX_NOTE_BYTES:
        raise NoteFormatError(f"note file would be larger than {MAX_NOTE_BYTES // 1024} KiB")
    try:
        same = parse_note_bytes(data) == note
    except NoteFormatError:
        same = False
    if not same:
        raise NoteFormatError("note would not read back identically (unsupported characters)")
    return data
