"""Loads the controlled vocabulary of a content repository from ``schema/areas.yaml`` and ``schema/types.yaml``.

Both files are one YAML mapping with exactly one key holding a list of kebab-case names::

    # schema/areas.yaml          # schema/types.yaml
    areas:                       types:
      - infrastructure             - runbook
      - finance                    - guide

Strict: a missing or symlinked file, extra keys, duplicates, a non-list or an invalid name raise
``VocabularyError``. Both lists may be empty (``core`` and ``hub`` are built in).
The repository adapter does not enforce the vocabulary: validation is a use-case concern, and notes that predate a
vocabulary change must still load so they can be reported.
"""

from pathlib import Path

from cerebro.adapters.outbound.filesystem.errors import VocabularyError
from cerebro.adapters.outbound.filesystem.safeio import read_regular_file
from cerebro.adapters.outbound.filesystem.yaml_io import YamlRejectedError, load_mapping
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.vocabulary import Area, NoteType, Vocabulary

MAX_SCHEMA_BYTES = 64 * 1024


def _names(root: Path, file_name: str, key: str) -> list[str]:
    relative = f"schema/{file_name}"
    try:
        data = read_regular_file(root, relative, MAX_SCHEMA_BYTES)
    except FileNotFoundError:
        raise VocabularyError(f"{relative} is missing") from None
    except OSError:
        raise VocabularyError(f"{relative} cannot be read (symlink, not a regular file or too large)") from None
    try:
        mapping = load_mapping(data.decode("utf-8"))
    except UnicodeDecodeError, YamlRejectedError:
        raise VocabularyError(f"{relative} is not valid YAML") from None
    if set(mapping) != {key}:
        raise VocabularyError(f"{relative} must contain exactly the key '{key}'")
    items = mapping[key]
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise VocabularyError(f"{relative}: '{key}' must be a list of names")
    if len(set(items)) != len(items):
        raise VocabularyError(f"{relative}: duplicate names")
    return items


def load_vocabulary(root: Path) -> Vocabulary:
    """The vocabulary of the content repository at ``root``."""
    areas = _names(root, "areas.yaml", "areas")
    types = _names(root, "types.yaml", "types")
    try:
        return Vocabulary(areas=frozenset(Area(a) for a in areas), types=frozenset(NoteType(t) for t in types))
    except InvalidValueError:
        raise VocabularyError("schema files contain a name that is not kebab-case") from None
