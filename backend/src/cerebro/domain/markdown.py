"""Minimal Markdown helpers shared by link and section extraction (stdlib only, no full parser)."""

import re

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")


def fenced_flags(lines: list[str]) -> list[bool]:
    """For each line, whether it is part of a fenced code block (fence lines included)."""
    flags: list[bool] = []
    fence: str | None = None
    for line in lines:
        match = _FENCE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
                flags.append(True)
            else:
                flags.append(False)
        else:
            flags.append(True)
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
    return flags


def code_free_lines(lines: list[str]) -> list[str]:
    """``lines``, one for one, with fenced code blocks and inline code spans blanked out."""
    return [
        "" if fenced else _INLINE_CODE.sub("", line) for line, fenced in zip(lines, fenced_flags(lines), strict=True)
    ]


def strip_code(text: str) -> str:
    """``text`` with fenced code blocks and inline code spans blanked out."""
    return "\n".join(code_free_lines(text.splitlines()))
