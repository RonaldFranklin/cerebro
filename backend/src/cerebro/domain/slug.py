"""Kebab-case identifiers shared by note ids, areas and note types."""

import re

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def is_slug(value: object) -> bool:
    """Return whether ``value`` is a lowercase ASCII kebab-case string (``^[a-z0-9]+(-[a-z0-9]+)*$``)."""
    return isinstance(value, str) and _SLUG.fullmatch(value) is not None
