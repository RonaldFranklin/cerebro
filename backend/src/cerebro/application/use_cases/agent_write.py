"""Authenticated full-note replacement. The composition root serializes the entire operation."""

import re
import unicodedata
from dataclasses import replace
from datetime import date

from cerebro.application.access import require_scope, visible_id
from cerebro.application.agent_write import AgentWriteRequest, AgentWriteResult, WriteFinding, WriteRejected, projected
from cerebro.application.dto import sensitivity_raise_warnings
from cerebro.application.errors import PermissionDenied
from cerebro.application.note_validation import validate_candidate
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteChange, NoteRepository
from cerebro.application.ports.search import SearchIndex
from cerebro.application.ports.system import Clock, IdGenerator
from cerebro.application.ports.write_attempts import WriteAttemptStore
from cerebro.domain.access import AccessContext, Agent, Scope
from cerebro.domain.commit_message import AgentWriteCommit, create_request_key
from cerebro.domain.content_hash import content_hash, is_content_hash
from cerebro.domain.errors import ContentConflict, InvalidValueError
from cerebro.domain.frontmatter import FIELD_ORDER, parse_note
from cerebro.domain.links import loose_link_targets
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.report import AGENT_WRITE_POLICY, Severity, ValidationReport
from cerebro.domain.vocabulary import CORE, HUB, Vocabulary

_CREATE_PLACEHOLDER = NoteId("create")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SETTABLE = frozenset(FIELD_ORDER) - {"id", "created", "updated"}
_UNAVAILABLE = WriteFinding("target_unavailable", "target", "Search for a writable note and read it in full.")
_REFERENCE = WriteFinding("broken_link", "references", "Remove unavailable references or select a visible note.")
_CONFLICT = WriteFinding("request_conflict", "request_id", "Use a new request id for a different create.")


class AgentWrite:
    def __init__(
        self,
        notes: NoteRepository,
        search: SearchIndex,
        graph: GraphIndex,
        attempts: WriteAttemptStore,
        clock: Clock,
        ids: IdGenerator,
        vocabulary: Vocabulary,
        limit: int,
    ) -> None:
        self._notes = notes
        self._search = search
        self._graph = graph
        self._attempts = attempts
        self._clock = clock
        self._ids = ids
        self._vocabulary = vocabulary
        self._limit = limit

    def execute(self, request: AgentWriteRequest, ctx: AccessContext) -> AgentWriteResult:
        require_scope(ctx, Scope.WRITE)
        if ctx.token_id is None or not isinstance(ctx.principal, Agent):
            raise PermissionDenied("write without an authenticated agent token")
        now = self._clock.now()
        if not self._attempts.reserve(ctx.token_id, now, self._limit):
            raise WriteRejected(WriteFinding("rate_limited", "request", "Wait for a rolling-hour quota slot."))

        if request.has_unexpected_fields:
            raise WriteRejected(
                WriteFinding(
                    "unknown_field",
                    "request",
                    "Use operation, frontmatter and body, plus request_id on create or target and base_hash on update.",
                )
            )

        key = fingerprint = None
        base = None
        receipt = None
        if request.operation == "create":
            if not isinstance(request.request_id, str) or not _REQUEST_ID.fullmatch(request.request_id):
                raise WriteRejected(WriteFinding("invalid_field", "request_id", "Supply a 1-64 character request id."))
            if request.target is not None or request.base_hash is not None:
                raise WriteRejected(WriteFinding("invalid_field", "target", "Omit target and base_hash on create."))
            key = create_request_key(ctx.token_id, request.request_id)
            receipt = self._notes.find_create(key)
            if receipt is not None:
                self._visible_target(receipt.note_id, ctx)
            note_id = receipt.note_id if receipt else _CREATE_PLACEHOLDER
        elif request.operation == "update":
            if request.request_id is not None or not is_content_hash(request.base_hash):
                raise WriteRejected(WriteFinding("invalid_field", "base_hash", "Supply the hash from a full read."))
            try:
                note_id = NoteId(request.target or "")
            except InvalidValueError:
                raise WriteRejected(_UNAVAILABLE) from None
            base = self._visible_target(note_id, ctx)
            if content_hash(base) != request.base_hash:
                raise self._stale()
        else:
            raise WriteRejected(WriteFinding("invalid_field", "operation", "Choose create or update."))

        try:
            candidate = _parse(request, note_id, now.date(), base)
        except WriteRejected:
            if receipt:
                raise WriteRejected(_CONFLICT) from None
            raise
        if not ctx.ceiling.allows(candidate.sensitivity):
            raise WriteRejected(WriteFinding("outside_ceiling", "sensitivity", "Choose an authorized sensitivity."))
        if key is not None and receipt is None:
            note_id = self._new_id(candidate.title)
            candidate = replace(candidate, id=note_id)
        if not self._references_visible(candidate, ctx):
            raise WriteRejected(_REFERENCE)
        if key is not None:
            # Canonical user-controlled content, independent of service timestamps and allocated identifiers.
            fingerprint = content_hash(replace(candidate, id=_CREATE_PLACEHOLDER, created=date.min, updated=date.min))
        if receipt is not None and receipt.fingerprint != fingerprint:
            raise WriteRejected(_CONFLICT)
        if receipt is not None:
            return AgentWriteResult(receipt.note_id, receipt.revision)
        if base and base.type != candidate.type and {base.type, candidate.type} & {CORE, HUB}:
            raise WriteRejected(
                WriteFinding("invalid_field", "type", "Keep the note in its original core, hub or regular category.")
            )
        if base and replace(candidate, updated=base.updated) == base:
            raise WriteRejected(WriteFinding("unchanged", "request", "The stored note already contains these changes."))
        report = validate_candidate(
            candidate, notes=self._notes, vocabulary=self._vocabulary, policy=AGENT_WRITE_POLICY, base=base
        )
        errors = projected(report, Severity.ERROR)
        if errors:
            raise WriteRejected(*errors)
        warnings = projected(report, Severity.WARNING)
        if base is None:
            hits = self._search.find_similar(f"{candidate.title} {candidate.summary}", ceiling=ctx.ceiling, limit=3)
            if any(hit.score >= 0.5 and visible_id(self._notes, hit.id, ctx, include_history=False) for hit in hits):
                warnings += (WriteFinding("possible_duplicate", "title", "Search for a note covering the same topic."),)
        message = AgentWriteCommit(
            note_id=note_id, written_by=ctx.principal.name, request_key=key, fingerprint=fingerprint
        )
        revision = self._commit(candidate, base, message, request.base_hash)
        return AgentWriteResult(
            note_id,
            revision,
            warnings,
            sensitivity_raise_warnings(base.sensitivity if base else None, candidate.sensitivity),
        )

    def _new_id(self, title: str) -> NoteId:
        decomposed = unicodedata.normalize("NFKD", title.casefold())
        unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
        stem = re.sub(r"[^a-z0-9]+", "-", unaccented).strip("-")[:60].rstrip("-") or "note"
        for _ in range(32):
            note_id = NoteId(f"{stem}-{self._ids.new_note_suffix()}")
            if self._notes.get(note_id) is None:
                return note_id
        raise WriteRejected(WriteFinding("id_unavailable", "request", "Retry the create with the same request id."))

    def _references_visible(self, note: Note, ctx: AccessContext) -> bool:
        refs = set(note.related)
        refs.update(ref for ref in (note.hub, note.superseded_by) if ref is not None)
        for text in (note.body, note.title, note.summary):
            refs.update(loose_link_targets(text))
        return all(ref == note.id or visible_id(self._notes, ref, ctx, include_history=True) for ref in refs)

    def _visible_target(self, note_id: NoteId, ctx: AccessContext) -> Note:
        note = self._notes.get(note_id)
        if note is None or not ctx.ceiling.allows(note.sensitivity) or not self._references_visible(note, ctx):
            raise WriteRejected(_UNAVAILABLE)
        return note

    @staticmethod
    def _stale() -> WriteRejected:
        return WriteRejected(WriteFinding("stale_base", "base_hash", "Read the full note again and reconcile changes."))

    def _commit(self, candidate: Note, base: Note | None, message: AgentWriteCommit, expected: str | None) -> str:
        previous = self._notes.head()
        search_fresh = self._search.built_from() == previous
        graph_fresh = self._graph.built_from() == previous
        # Evict the wider projection before committing a narrower one. A crash at any subsequent
        # boundary leaves only missing results, and a stale revision that startup repairs.
        if base and (base.sensitivity < candidate.sensitivity or (base.is_current and not candidate.is_current)):
            self._search.set_revision(None)
            self._graph.set_revision(None)
            self._search.remove(base.id)
            self._graph.remove(base.id)
        try:
            revision = self._notes.apply([NoteChange(note=candidate, expected_hash=expected)], message)
        except ContentConflict:
            raise self._stale() from None
        try:
            self._search.upsert(candidate)
            self._graph.upsert(NoteRef.from_note(candidate))
            if search_fresh:
                self._search.set_revision(revision)
            if graph_fresh:
                self._graph.set_revision(revision)
        except Exception:
            # The authoritative commit and its receipt are durable. Startup checks both index
            # revisions against HEAD and rebuilds them under this same writer lock.
            pass
        return revision


def _check_text(value: object, field: str, max_bytes: int) -> None:
    if not isinstance(value, str):
        raise WriteRejected(WriteFinding("invalid_field", field, "Supply a UTF-8 string."))
    try:
        if len(value.encode("utf-8")) <= max_bytes:
            return
    except UnicodeEncodeError:
        pass
    raise WriteRejected(WriteFinding("invalid_field", field, f"Supply valid UTF-8 text of at most {max_bytes} bytes."))


def _parse(request: AgentWriteRequest, note_id: NoteId, today: date, base: Note | None) -> Note:
    fields = request.frontmatter
    if not isinstance(fields, dict) or fields.keys() - _SETTABLE:
        raise WriteRejected(WriteFinding("unknown_field", "frontmatter", "Use only documented writable fields."))
    _check_text(request.body, "body", 48 * 1024)
    for field, value in fields.items():
        if field == "related" and value is not None:
            if not isinstance(value, list) or len(value) > 32:
                raise WriteRejected(WriteFinding("invalid_field", field, "Supply a list of at most 32 note ids."))
            for ref in value:
                _check_text(ref, field, 80)
        elif value is not None:
            _check_text(value, field, {"summary": 1200, "title": 480}.get(field, 256))
    raw = {
        "status": base.status.value if base else "current",
        **fields,
        "id": note_id.value,
        "created": base.created if base else today,
        "updated": today,
    }
    candidate = parse_note(raw, request.body)
    if isinstance(candidate, ValidationReport):
        raise WriteRejected(*projected(candidate, Severity.ERROR))
    return candidate
