"""Note content hash and the optimistic-concurrency check built on it.

``content_hash(note)`` is the SHA-256 hex digest of the UTF-8 bytes of this canonical JSON document::

    {"frontmatter": <note_to_raw(note)>, "body": <note.body>}

serialized with ``json.dumps(..., ensure_ascii=False, separators=(",", ":"))`` and **no** key sorting: key order
is the canonical order of ``note_to_raw`` (stable by construction). Dates are written as ISO ``YYYY-MM-DD``.
Empty optional fields are omitted (as ``note_to_raw`` does), so ``hub=None`` and an absent hub hash the same.
The body is hashed byte for byte (no newline or whitespace normalization): any change to any frontmatter field
or to the body changes the hash. Changing this format invalidates every stored ``base_hash``.
"""

import hashlib
import json
import re
from datetime import date
from typing import Final

from cerebro.domain.errors import ContentConflict
from cerebro.domain.frontmatter import note_to_raw
from cerebro.domain.note import Note

_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")


def is_content_hash(value: object) -> bool:
    return isinstance(value, str) and _SHA256_HEX.fullmatch(value) is not None


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")  # pragma: no cover - note_to_raw has only dates


def content_hash(note: Note) -> str:
    document = {"frontmatter": note_to_raw(note), "body": note.body}
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_unchanged(expected_hash: str, current: Note) -> None:
    """Raise ``ContentConflict`` unless ``current`` still has ``expected_hash``.

    Used by ``ApplyProposal`` (``base_hash`` of an update proposal) and ``OwnerEditNote`` (hash the editor loaded).
    """
    actual = content_hash(current)
    if actual != expected_hash:
        raise ContentConflict(expected_hash, actual)
