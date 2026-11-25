"""Section extraction by Markdown (ATX) heading, for ``ReadNote``."""

import re
from dataclasses import dataclass

from cerebro.domain.markdown import fenced_flags

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")


@dataclass(frozen=True, slots=True, kw_only=True)
class Heading:
    level: int
    title: str
    line: int
    """Zero-based line index in the body."""


def _normalize(title: str) -> str:
    return " ".join(title.split()).casefold()


def extract_headings(body: str) -> tuple[Heading, ...]:
    """ATX headings (``# Title``) in order; headings inside fenced code blocks are ignored."""
    lines = body.splitlines()
    headings: list[Heading] = []
    for index, (line, fenced) in enumerate(zip(lines, fenced_flags(lines), strict=True)):
        match = None if fenced else _HEADING.match(line)
        if match:
            headings.append(Heading(level=len(match.group(1)), title=match.group(2).strip(), line=index))
    return tuple(headings)


def extract_section(body: str, heading: str) -> str | None:
    """The section under ``heading``, heading line included, or ``None`` when no such heading exists.

    Matching ignores case and collapses whitespace; the first matching heading wins. The section runs until
    the next heading of the same or a higher level (so sub-sections are included) or the end of the body.
    """
    wanted = _normalize(heading)
    headings = extract_headings(body)
    lines = body.splitlines()
    for position, found in enumerate(headings):
        if _normalize(found.title) != wanted:
            continue
        end = len(lines)
        for later in headings[position + 1 :]:
            if later.level <= found.level:
                end = later.line
                break
        return "\n".join(lines[found.line : end]).rstrip("\n") + "\n"
    return None
