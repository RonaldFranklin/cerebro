"""``SqliteProposalStore``: proposals in ``state.sqlite``, sealed with the ``Cipher``.

Everything about a proposal that came from an agent or a curator (content, free text, target, validation report,
decision, ...) is JSON-serialised, bound to the proposal id and encrypted into one ``payload`` BLOB, even for a
``public`` proposal: the table never holds plaintext content. Only what queries and the compare-and-swap need is
in clear: id, kind, status, revision, source agent, timestamps (as sortable UTC keys), and the curator and approver
names. Those clear columns are copies: on every read they are compared with the sealed payload and a mismatch is
reported as corruption. The id inside the sealed payload must equal the row id, so a ciphertext moved to another
row is refused (``DecryptionError``).
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cerebro.adapters.outbound.sqlite._state_db import StateCorruptionError, StateDatabase
from cerebro.application.errors import AlreadyExists, DecryptionError, ProposalConflict, ProposalNotFound
from cerebro.application.ports.system import Cipher
from cerebro.domain.decision import Decision, DecisionKind
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.errors import DomainError, InvalidValueError
from cerebro.domain.note import NoteId
from cerebro.domain.proposal import Proposal, ProposalKind, ProposalStatus, ProposedContent
from cerebro.domain.report import Severity, ValidationReport, Violation, ViolationCode
from cerebro.domain.vocabulary import Area, NoteType

PAYLOAD_VERSION = 1
_COLUMNS = "id, kind, status, revision, source_agent, created_key, updated_key, curated_by, approved_by, payload"


def utc_key(moment: datetime) -> str:
    """A fixed-width UTC timestamp whose text order is the chronological order."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# -- serialisation (pure; the sealed payload) -------------------------------------------------------------------


def _note_id(value: NoteId | None) -> str | None:
    return None if value is None else value.value


def _maybe[T](value: Any, build: Callable[[Any], T]) -> T | None:
    return None if value is None else build(value)


def _content_to_json(c: ProposedContent) -> dict[str, Any]:
    return {
        "title": c.title,
        "body": c.body,
        "area": None if c.area is None else c.area.value,
        "type": None if c.type is None else c.type.value,
        "priority": None if c.priority is None else c.priority.value,
        "sensitivity": None if c.sensitivity is None else c.sensitivity.value,
        "rationale": c.rationale,
        "related": [r.value for r in c.related],
        "superseded_by": _note_id(c.superseded_by),
        "summary": c.summary,
        "hub": _note_id(c.hub),
    }


def _content_from_json(d: dict[str, Any]) -> ProposedContent:
    return ProposedContent(
        title=d["title"],
        body=d["body"],
        area=_maybe(d["area"], Area),
        type=_maybe(d["type"], NoteType),
        priority=_maybe(d["priority"], Priority),
        sensitivity=_maybe(d["sensitivity"], Sensitivity),
        rationale=d["rationale"],
        related=tuple(NoteId(r) for r in d["related"]),
        superseded_by=_maybe(d["superseded_by"], NoteId),
        summary=d["summary"],
        hub=_maybe(d["hub"], NoteId),
    )


def _report_to_json(report: ValidationReport) -> list[dict[str, Any]]:
    return [
        {
            "code": v.code.value,
            "field": v.field,
            "message": v.message,
            "severity": v.severity.value,
            "detector": v.detector,
            "line": v.line,
        }
        for v in report.violations
    ]


def _report_from_json(items: list[dict[str, Any]]) -> ValidationReport:
    return ValidationReport(
        tuple(
            Violation(
                code=ViolationCode(i["code"]),
                field=i["field"],
                message=i["message"],
                severity=Severity(i["severity"]),
                detector=i["detector"],
                line=i["line"],
            )
            for i in items
        )
    )


def _decision_to_json(d: Decision) -> dict[str, Any]:
    return {"kind": d.kind.value, "target": _note_id(d.target), "reason": d.reason}


def _decision_from_json(d: dict[str, Any]) -> Decision:
    return Decision(kind=DecisionKind(d["kind"]), target=_maybe(d["target"], NoteId), reason=d["reason"])


def seal_payload(proposal: Proposal) -> bytes:
    """The canonical JSON of everything but ``revision`` (which lives in clear, for the compare-and-swap)."""
    document: dict[str, Any] = {
        "v": PAYLOAD_VERSION,
        "id": proposal.id,
        "kind": proposal.kind.value,
        "status": proposal.status.value,
        "target": _note_id(proposal.target),
        "content": _content_to_json(proposal.content),
        "source_agent": proposal.source_agent,
        "source_session": proposal.source_session,
        "source_context": proposal.source_context,
        "created_at": proposal.created_at.isoformat(),
        "updated_at": proposal.updated_at.isoformat(),
        "base_hash": proposal.base_hash,
        "validation": None if proposal.validation is None else _report_to_json(proposal.validation),
        "decision": None if proposal.decision is None else _decision_to_json(proposal.decision),
        "curated_by": proposal.curated_by,
        "approved_by": proposal.approved_by,
        "conflict": proposal.conflict,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def open_payload(plaintext: bytes, *, revision: int) -> Proposal:
    d = json.loads(plaintext)
    if d["v"] != PAYLOAD_VERSION:
        raise StateCorruptionError(f"unknown proposal payload version {d['v']!r}")
    return Proposal(
        id=d["id"],
        kind=ProposalKind(d["kind"]),
        status=ProposalStatus(d["status"]),
        target=_maybe(d["target"], NoteId),
        content=_content_from_json(d["content"]),
        source_agent=d["source_agent"],
        source_session=d["source_session"],
        source_context=d["source_context"],
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
        base_hash=d["base_hash"],
        validation=_maybe(d["validation"], _report_from_json),
        decision=_maybe(d["decision"], _decision_from_json),
        curated_by=d["curated_by"],
        approved_by=d["approved_by"],
        conflict=d["conflict"],
        revision=revision,
    )


class SqliteProposalStore:
    """``ProposalStore`` over ``state.sqlite``; ``cipher`` seals every payload."""

    def __init__(self, path: Path | str, cipher: Cipher) -> None:
        self._db = StateDatabase(path)
        self._cipher = cipher

    def __repr__(self) -> str:
        return f"SqliteProposalStore({self._db!r})"

    def add(self, proposal: Proposal) -> None:
        if proposal.revision != 0:
            raise InvalidValueError("revision", "a new proposal starts at revision 0")
        row = self._row(proposal)
        try:
            with self._db.write() as conn:
                conn.execute(f"INSERT INTO proposals ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        except sqlite3.IntegrityError:
            raise AlreadyExists("proposal", proposal.id) from None

    def get(self, proposal_id: str) -> Proposal | None:
        with self._db.read() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return None if row is None else self._load(row)

    def save(self, proposal: Proposal, *, expected_revision: int) -> Proposal:
        saved = replace(proposal, revision=expected_revision + 1)
        row = self._row(saved)
        with self._db.write() as conn:
            cursor = conn.execute(
                "UPDATE proposals SET kind = ?, status = ?, revision = ?, source_agent = ?, created_key = ?, "
                "updated_key = ?, curated_by = ?, approved_by = ?, payload = ? WHERE id = ? AND revision = ?",
                (*row[1:], proposal.id, expected_revision),
            )
            if cursor.rowcount == 0:
                exists = conn.execute("SELECT 1 FROM proposals WHERE id = ?", (proposal.id,)).fetchone()
                raise ProposalConflict(proposal.id) if exists else ProposalNotFound(proposal.id)
        return saved

    def find(self, *, status: ProposalStatus | None = None, source_agent: str | None = None) -> tuple[Proposal, ...]:
        clauses: list[str] = []
        params: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if source_agent is not None:
            clauses.append("source_agent = ?")
            params.append(source_agent)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._db.read() as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM proposals{where} ORDER BY created_key, id", params).fetchall()
        return tuple(self._load(row) for row in rows)

    # -- internals ---------------------------------------------------------------------------------------------

    def _row(self, p: Proposal) -> tuple[Any, ...]:
        return (
            p.id,
            p.kind.value,
            p.status.value,
            p.revision,
            p.source_agent,
            utc_key(p.created_at),
            utc_key(p.updated_at),
            p.curated_by,
            p.approved_by,
            self._cipher.encrypt(seal_payload(p)),
        )

    def _load(self, row: tuple[Any, ...]) -> Proposal:
        row_id, kind, status, revision, agent, created, updated, curated_by, approved_by, payload = row
        plaintext = self._cipher.decrypt(bytes(payload))  # DecryptionError: wrong key or tampered data
        try:
            proposal = open_payload(plaintext, revision=revision)
        except KeyError, TypeError, ValueError, AttributeError, DomainError:
            raise StateCorruptionError(f"proposal {row_id!r} has an unreadable payload") from None
        if proposal.id != row_id:
            raise DecryptionError("encrypted payload does not belong to this proposal")
        clear = (kind, status, agent, created, updated, curated_by, approved_by)
        sealed = (
            proposal.kind.value,
            proposal.status.value,
            proposal.source_agent,
            utc_key(proposal.created_at),
            utc_key(proposal.updated_at),
            proposal.curated_by,
            proposal.approved_by,
        )
        if clear != sealed:
            raise StateCorruptionError(f"proposal {row_id!r}: clear columns disagree with the sealed payload")
        return proposal
