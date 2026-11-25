"""Kebab-case identifiers shared by note ids, areas and note types."""

import re

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def is_slug(value: str) -> bool:
    """Return whether ``value`` is lowercase ASCII kebab-case (``^[a-z0-9]+(-[a-z0-9]+)*$``)."""
    return _SLUG.fullmatch(value) is not None
