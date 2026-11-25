"""Restricted YAML reading and writing for note frontmatter and schema files.

Reading (``load_mapping``) uses a ``yaml.SafeLoader`` subclass that is stricter than ``safe_load``:

* **Everything is text except ``null``.** The YAML 1.1 implicit resolvers for booleans, integers, floats,
  timestamps, ``=`` and ``<<`` are removed, so ``no``, ``on``, ``2026``, ``0x1f``, ``1e3`` and ``2026-11-24``
  all arrive as ``str``. The domain (``parse_note``) turns the date fields into ``date`` itself. Only ``null``,
  ``~`` and an empty value stay ``None`` ("missing", as the domain model says).
* **No anchors, aliases, explicit tags** (``!!python/object``, ``!!str``, custom ones), **merge keys,
  duplicate keys, non-scalar keys, multiple documents.** Nothing but plain mappings, lists and strings is ever
  constructed, so nothing can be executed and there is no alias amplification.
* Size, node count and nesting depth are capped.

Writing (``dump_mapping``) uses a ``SafeDumper`` subclass: block style, canonical key order kept
(``sort_keys=False``), no aliases, unicode allowed, and every string that a YAML 1.1 reader could take for a
boolean, number, null or timestamp is quoted, so ``write -> read`` is exact and other tools agree.
"""

import re
from collections.abc import Mapping
from typing import Any, Final

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode

MAX_YAML_CHARS: Final = 128 * 1024
MAX_NODES: Final = 5_000
MAX_DEPTH: Final = 8


class YamlRejectedError(ValueError):
    """The YAML text was refused. The message is fixed text; it never quotes the input."""


class _StrictLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = {}  # noqa: RUF012 - deliberately not shared with the parent's table

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._nodes = 0
        self._depth = 0

    def compose_node(self, parent: Node | None, index: object) -> Node | None:
        if self.check_event(yaml.AliasEvent):
            raise YamlRejectedError("YAML aliases are not allowed")
        event = self.peek_event()  # type: ignore[no-untyped-call]
        if getattr(event, "anchor", None) is not None:
            raise YamlRejectedError("YAML anchors are not allowed")
        if getattr(event, "tag", None) is not None:
            raise YamlRejectedError("explicit YAML tags are not allowed")
        self._nodes += 1
        if self._nodes > MAX_NODES:
            raise YamlRejectedError("YAML document has too many nodes")
        self._depth += 1
        try:
            if self._depth > MAX_DEPTH:
                raise YamlRejectedError("YAML document is nested too deeply")
            return super().compose_node(parent, index)  # type: ignore[arg-type]
        finally:
            self._depth -= 1

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[str] = set()
        for key_node, _ in node.value:
            if not isinstance(key_node, ScalarNode):
                raise YamlRejectedError("YAML mapping keys must be plain text")
            if key_node.value in seen:
                raise YamlRejectedError("duplicate YAML key")
            seen.add(key_node.value)
        return super().construct_mapping(node, deep=deep)


_StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:null", re.compile(r"^(?:~|null|Null|NULL|)$"), ["~", "n", "N", ""]
)


def load_mapping(text: str) -> dict[str, object]:
    """Parse ``text`` as one YAML mapping with string keys. Raises ``YamlRejectedError`` for anything else."""
    if len(text) > MAX_YAML_CHARS:
        raise YamlRejectedError("YAML text is too large")
    try:
        loader = _StrictLoader(text)  # validates the characters (``ReaderError``) while constructing
    except yaml.YAMLError:
        raise YamlRejectedError("not valid YAML") from None
    try:
        data = loader.get_single_data()
    except YamlRejectedError:
        raise
    except yaml.YAMLError:
        raise YamlRejectedError("not valid YAML") from None
    except RecursionError:
        raise YamlRejectedError("YAML document is nested too deeply") from None
    finally:
        loader.dispose()
    if not isinstance(data, dict):
        raise YamlRejectedError("YAML document must be a mapping")
    if not all(isinstance(key, str) for key in data):
        raise YamlRejectedError("YAML mapping keys must be plain text")
    return data


# A string is quoted when a YAML 1.1 reader could resolve it to something other than a string.
_SPECIAL_WORD: Final = re.compile(r"(?:y|n|yes|no|true|false|on|off|null|~|=|<<)", re.IGNORECASE)
_SPECIAL_NUMBER: Final = re.compile(
    r"[-+]?(?:0[xob][0-9a-f_]+|[0-9][0-9_,.:]*(?:e[-+]?[0-9]+)?|\.[0-9_]+(?:e[-+]?[0-9]+)?|\.(?:inf|nan))",
    re.IGNORECASE,
)
_SPECIAL_TIMESTAMP: Final = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt ].*)?", re.DOTALL)


def _looks_special(text: str) -> bool:
    return (
        _SPECIAL_WORD.fullmatch(text) is not None
        or _SPECIAL_NUMBER.fullmatch(text) is not None
        or _SPECIAL_TIMESTAMP.fullmatch(text) is not None
    )


class _NoteDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: object) -> bool:
        return True


# PyYAML writes these raw with ``allow_unicode`` although its own reader folds them into line breaks.
_UNSAFE_UNICODE: Final = re.compile("[\x85\u2028\u2029\ufeff]")


def _represent_str(dumper: yaml.SafeDumper, data: str) -> ScalarNode:
    style = '"' if _UNSAFE_UNICODE.search(data) else "'" if _looks_special(data) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_NoteDumper.add_representer(str, _represent_str)


def dump_mapping(raw: Mapping[str, object]) -> str:
    """Canonical YAML text of ``raw`` (keys in the given order, block style, ends with a newline)."""
    return yaml.dump(
        dict(raw),
        Dumper=_NoteDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        explicit_start=False,
    )
