"""Best-effort secret scanner for note text.

Notes may say where a secret lives, never its value. ``scan_note`` looks at every text field of a note
(title, summary, ids, ... and the body, fenced code blocks included) and returns one ``Finding`` per hit with the
code ``SECRET_DETECTED``. Findings never contain the secret: only the kind of detector, the field, the line and
a tiny redacted preview (see ``PREVIEW_CHARS``). The scanner is a heuristic, not a guarantee: see
``docs/domain-model.md`` section 6 for its known limits.
"""

import math
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from cerebro.domain.note import Note
from cerebro.domain.report import Finding, ViolationCode

ENTROPY_MIN_LENGTH: Final = 32
"""Shortest token (characters) considered by the entropy detector."""

ENTROPY_THRESHOLD: Final = 4.3
"""Shannon entropy (bits per character) at or above which a long mixed token is flagged."""

ASSIGNMENT_MIN_LENGTH: Final = 8
"""Shortest value of a ``password=``-style assignment that is considered a real secret."""

PREVIEW_MIN_LENGTH: Final = 16
PREVIEW_CHARS: Final = 2
"""A preview (first ``PREVIEW_CHARS`` characters) is shown only for secrets of at least ``PREVIEW_MIN_LENGTH``."""

# (kind, pattern): the whole match is the secret.
_TOKEN_DETECTORS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("private key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("SendGrid API key", re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
)

# group 1 is the secret.
_BEARER: Final = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{20,})")
_URL_CREDENTIALS: Final = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]+:([^\s/@]{3,})@")
_ASSIGNMENT: Final = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9])(?:password|passwd|pwd|passphrase|secret|(?:api|access|secret|private|auth|client)[_-]?(?:key|secret|token)
        |apikey|token)(?![A-Za-z0-9])
    ["']?\s*[:=]\s*
    (?:(?P<quote>["'])(?P<quoted>[^"'\n]{1,200}?)(?P=quote)|(?P<bare>[^\s"',;]+))
    """
)

_PLACEHOLDER_MARKERS: Final = (
    "<",
    ">",
    "{{",
    "${",
    "$(",
    "%(",
    "xxx",
    "***",
    "...",
    "…",
    "example",
    "changeme",
    "change-me",
    "placeholder",
    "redacted",
    "your",
    "dummy",
    "sample",
    "password-manager",
    "vault",
    "todo",
    "environment",
    "env.",
    "os.environ",
    "getenv",
)

_ENTROPY_CANDIDATE: Final = re.compile(rf"[A-Za-z0-9+/_=-]{{{ENTROPY_MIN_LENGTH},}}")
_HEX_ONLY: Final = re.compile(r"[0-9a-fA-F]+")
_UUID: Final = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_WIKILINK: Final = re.compile(r"\[\[[^\[\]\n]*\]\]")
_SIMPLE_PUNCTUATION: Final = frozenset("-_./ ")


@dataclass(frozen=True, slots=True, kw_only=True)
class _Hit:
    kind: str
    start: int
    end: int


def scan_note(note: Note) -> tuple[Finding, ...]:
    """Findings (code ``SECRET_DETECTED``) for every text field of ``note``; never contain the secret."""
    fields: tuple[tuple[str, str], ...] = (
        ("id", str(note.id)),
        ("title", note.title),
        ("type", str(note.type)),
        ("area", str(note.area)),
        ("summary", note.summary),
        ("lang", note.lang),
        ("hub", "" if note.hub is None else str(note.hub)),
        ("related", "\n".join(str(i) for i in note.related)),
        ("superseded_by", "" if note.superseded_by is None else str(note.superseded_by)),
        ("body", note.body),
    )
    return tuple(f for name, text in fields for f in scan_text(text, field=name))


def scan_text(text: str, *, field: str) -> tuple[Finding, ...]:
    """Scan one text field line by line. Line numbers are 1-based."""
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for hit in _scan_line(line):
            findings.append(
                Finding(
                    code=ViolationCode.SECRET_DETECTED,
                    field=field,
                    message=_message(hit.kind, number, line[hit.start : hit.end]),
                )
            )
    return tuple(findings)


def _message(kind: str, line_number: int, secret: str) -> str:
    preview = f", starts with {secret[:PREVIEW_CHARS]!r}" if len(secret) >= PREVIEW_MIN_LENGTH else ""
    return f"possible {kind} on line {line_number}{preview}; notes must say where a secret lives, never its value"


def _scan_line(line: str) -> Iterator[_Hit]:
    taken: list[_Hit] = []

    def free(start: int, end: int) -> bool:
        return all(end <= h.start or start >= h.end for h in taken)

    def candidates() -> Iterator[_Hit]:
        for kind, pattern in _TOKEN_DETECTORS:
            for m in pattern.finditer(line):
                yield _Hit(kind=kind, start=m.start(), end=m.end())
        for m in _BEARER.finditer(line):
            if any(c.isdigit() for c in m.group(1)):
                yield _Hit(kind="bearer token", start=m.start(1), end=m.end(1))
        for m in _URL_CREDENTIALS.finditer(line):
            if not _is_placeholder(m.group(1)):
                yield _Hit(kind="password in URL", start=m.start(1), end=m.end(1))
        for m in _ASSIGNMENT.finditer(line):
            if m.group("quoted") is not None:
                value, group, quoted = m.group("quoted"), "quoted", True
            else:
                value, group, quoted = m.group("bare"), "bare", False
            if _is_assigned_secret(value, quoted=quoted):
                yield _Hit(kind="credential assignment", start=m.start(group), end=m.end(group))
        masked = _WIKILINK.sub(lambda m: " " * len(m.group()), line)
        for m in _ENTROPY_CANDIDATE.finditer(masked):
            if _is_high_entropy(m.group()):
                yield _Hit(kind="high-entropy string", start=m.start(), end=m.end())

    for hit in candidates():
        if free(hit.start, hit.end):
            taken.append(hit)
            yield hit


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return value.startswith("$") or any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _is_assigned_secret(value: str, *, quoted: bool) -> bool:
    if len(value) < ASSIGNMENT_MIN_LENGTH or _is_placeholder(value):
        return False
    if quoted:
        return True
    # Unquoted values must look like a credential, not a word or a slug (e.g. ``secret: vaultwarden-item``).
    has_digit = any(c.isdigit() for c in value)
    mixed_case = any(c.islower() for c in value) and any(c.isupper() for c in value)
    has_symbol = any(not c.isalnum() and c not in _SIMPLE_PUNCTUATION for c in value)
    return has_digit or mixed_case or has_symbol


def shannon_entropy(text: str) -> float:
    """Bits per character."""
    if not text:
        return 0.0
    total = len(text)
    return -sum((n / total) * math.log2(n / total) for n in Counter(text).values())


def _is_high_entropy(token: str) -> bool:
    if _HEX_ONLY.fullmatch(token) or _UUID.fullmatch(token):
        return False  # commit hashes, digests and UUIDs are public identifiers, not secrets
    has_digit = any(c.isdigit() for c in token)
    has_letter = any(c.isalpha() for c in token)
    return has_digit and has_letter and shannon_entropy(token) >= ENTROPY_THRESHOLD
