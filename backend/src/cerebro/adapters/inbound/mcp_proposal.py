"""Closed proposal arguments shared by HTTP preflight, the stdio bridge and tool dispatch."""

from enum import StrEnum
from typing import Any

from mcp import types

from cerebro.adapters.inbound.input_limits import MAX_FIELD_BYTES, bounded_text
from cerebro.application.dto import SubmitProposalRequest
from cerebro.domain.content_hash import is_content_hash
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.note import MAX_NOTE_ID_LENGTH, NoteId
from cerebro.domain.proposal import MAX_TEXT_LENGTH, ProposalKind, ProposedContent, validate_submission_shape
from cerebro.domain.vocabulary import Area, NoteType


def _text(limit: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": limit, "description": f"Up to {limit} UTF-8 bytes."}


def _enum[E: StrEnum](values: type[E]) -> dict[str, Any]:
    return {"type": "string", "enum": [item.value for item in values]}


def _object(fields: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": fields, "additionalProperties": False}


_ID = {**_text(MAX_NOTE_ID_LENGTH), "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"}
_SLUG = {**_ID, "maxLength": MAX_FIELD_BYTES, "description": f"Up to {MAX_FIELD_BYTES} UTF-8 bytes."}
_RELATED_LIMIT = 32
_SUGGESTED = _object(
    {
        "area": _SLUG,
        "type": _SLUG,
        "priority": _enum(Priority),
        "sensitivity": _enum(Sensitivity),
        "summary": _text(1200),
        "hub": _ID,
        "superseded_by": _ID,
        "related": {"type": "array", "items": _ID, "maxItems": _RELATED_LIMIT},
    }
)
_SOURCE = _object({"session": _text(MAX_TEXT_LENGTH), "context": _text(MAX_TEXT_LENGTH)})
_SCHEMA = _object(
    {
        "kind": _enum(ProposalKind),
        "title": _text(480),
        "body": _text(48 * 1024),
        "target": _ID,
        "base_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$", "maxLength": 64},
        "suggested": _SUGGESTED,
        "rationale": _text(4096),
        "source": _SOURCE,
    }
)
_SCHEMA["required"] = ["kind"]
_SCHEMA["oneOf"] = [
    {
        "properties": {"kind": {"const": "create"}, "target": False, "base_hash": False, "title": {"pattern": r"\S"}},
        "required": ["title", "body"],
    },
    {"properties": {"kind": {"const": "update"}}, "required": ["target", "base_hash", "body"]},
    {
        "properties": {
            "kind": {"const": "link"},
            "body": False,
            "suggested": {"properties": {"related": {"minItems": 1}}, "required": ["related"]},
        },
        "required": ["target", "base_hash", "suggested"],
    },
    {"properties": {"kind": {"const": "deprecate"}, "body": False}, "required": ["target", "base_hash"]},
]

PROPOSE_TOOL = types.Tool(
    name="cerebro_propose", description="Submit a proposed change for owner review.", input_schema=_SCHEMA
)


def _fields(value: object, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() - schema["properties"].keys():
        raise ValueError("invalid fields")
    return value


def _strings(values: dict[str, Any], schema: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in values.items():
        field = schema["properties"][key]
        if field["type"] == "string":
            if not isinstance(value, str):
                raise ValueError("invalid text")
            if "enum" in field:
                if value not in field["enum"]:
                    raise ValueError("invalid enum")
            else:
                bounded_text(value, field["maxLength"])
            result[key] = value
    return result


def parse_proposal(value: object) -> SubmitProposalRequest:
    args = _fields(value, _SCHEMA)
    fields = _strings(args, _SCHEMA)
    suggested = _fields(args.get("suggested", {}), _SUGGESTED)
    metadata = _strings(suggested, _SUGGESTED)
    source = _strings(_fields(args.get("source", {}), _SOURCE), _SOURCE)
    related = suggested.get("related", [])
    if not isinstance(related, list) or len(related) > _RELATED_LIMIT:
        raise ValueError("invalid related ids")
    if "kind" not in fields:
        raise ValueError("missing kind")
    kind = ProposalKind(fields["kind"])
    content = ProposedContent(
        title=fields.get("title"),
        body=fields.get("body"),
        rationale=fields.get("rationale", ""),
        area=Area(metadata["area"]) if "area" in metadata else None,
        type=NoteType(metadata["type"]) if "type" in metadata else None,
        priority=Priority(metadata["priority"]) if "priority" in metadata else None,
        sensitivity=Sensitivity(metadata["sensitivity"]) if "sensitivity" in metadata else None,
        hub=NoteId(metadata["hub"]) if "hub" in metadata else None,
        superseded_by=NoteId(metadata["superseded_by"]) if "superseded_by" in metadata else None,
        related=tuple(NoteId(item) for item in related),
        summary=metadata.get("summary"),
    )
    target = NoteId(fields["target"]) if "target" in fields else None
    base_hash = fields.get("base_hash")
    if base_hash is not None and not is_content_hash(base_hash):
        raise ValueError("invalid digest")
    validate_submission_shape(kind, target, base_hash, content)
    return SubmitProposalRequest(
        kind=kind,
        target=target,
        base_hash=base_hash,
        content=content,
        source_session=source.get("session"),
        source_context=source.get("context"),
    )
