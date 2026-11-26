"""Wire schema for a validated full-note create or update."""

from typing import Any

from mcp import types

from cerebro.application.agent_write import AgentWriteRequest
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.note import MAX_NOTE_ID_LENGTH


def _text(maximum: int) -> dict[str, object]:
    return {"type": "string", "maxLength": maximum, "description": f"Up to {maximum} UTF-8 bytes."}


_ID = {**_text(MAX_NOTE_ID_LENGTH), "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"}
_FRONTMATTER = {
    "type": "object",
    "properties": {
        "title": _text(480),
        "type": _text(256),
        "area": _text(256),
        "priority": {"type": "string", "enum": [p.value for p in Priority]},
        "sensitivity": {"type": "string", "enum": [s.value for s in Sensitivity]},
        "status": {"type": "string", "enum": [s.value for s in Status]},
        "summary": _text(1200),
        "hub": {"anyOf": [_ID, {"type": "null"}]},
        "related": {"type": "array", "maxItems": 32, "items": _ID},
        "superseded_by": {"anyOf": [_ID, {"type": "null"}]},
        "lang": _text(256),
        "review_by": {"type": ["string", "null"], "format": "date"},
    },
    "required": ["area", "type", "title", "priority", "sensitivity", "summary"],
    "additionalProperties": False,
}

WRITE_TOOL = types.Tool(
    name="cerebro_write",
    description=(
        "Create (with request_id) or update (with target and base_hash) a validated note. "
        "Consult cerebro_write_guide before writing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["create", "update"]},
            "frontmatter": _FRONTMATTER,
            "body": _text(48 * 1024),
            "request_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$", "description": "Create only."},
            "target": {**_ID, "description": "Update only: the note id."},
            "base_hash": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
                "description": "Update only: from a full read.",
            },
        },
        "required": ["operation", "frontmatter", "body"],
        "additionalProperties": False,
    },
)


def write_request(args: dict[str, Any]) -> AgentWriteRequest:
    # Transport frames have already been bounded. The application charges the authenticated
    # token before validating the submitted shape, UTF-8 byte budgets and note content.
    operation = args.get("operation", "")
    unexpected = bool(args.keys() - WRITE_TOOL.input_schema["properties"].keys())
    if operation == "create":
        unexpected |= "target" in args or "base_hash" in args
    elif operation == "update":
        unexpected |= "request_id" in args
    return AgentWriteRequest(
        has_unexpected_fields=unexpected,
        operation=operation,
        frontmatter=args.get("frontmatter"),
        body=args.get("body"),
        request_id=args.get("request_id"),
        target=args.get("target"),
        base_hash=args.get("base_hash"),
    )
