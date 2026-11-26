"""Authenticated full-note replacement. The composition root serializes the entire operation."""

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import replace
from datetime import date

from cerebro.application.access import require_scope, visible_id
from cerebro.application.agent_write import AgentWriteRequest, AgentWriteResult, WriteFinding, WriteRejected, projected
from cerebro.application.dto import sensitivity_raise_warnings
from cerebro.application.errors import PermissionDenied
from cerebro.application.index_state import index_commit
from cerebro.application.note_validation import validate_candidate
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteChange, NoteRepository
from cerebro.application.ports.search import SearchIndex
from cerebro.application.ports.system import Clock, IdGenerator
from cerebro.application.ports.write_attempts import WriteAttemptStore
from cerebro.domain.access import AccessContext, Agent, Scope
from cerebro.domain.commit_message import AgentWriteCommit, create_request_key
from cerebro.domain.content_hash import content_hash, is_content_hash
from cerebro.domain.enums import Priority
from cerebro.domain.errors import ContentConflict, InvalidValueError
from cerebro.domain.frontmatter import FIELD_ORDER, parse_note
from cerebro.domain.links import loose_link_targets
from cerebro.domain.note import Note, NoteId
from cerebro.domain.report import AGENT_WRITE_POLICY, Severity, ValidationReport
from cerebro.domain.vocabulary import CORE, HUB, Vocabulary

_CREATE_PLACEHOLDER = NoteId("create")
_DUPLICATE_SIMILARITY = 0.5  # shared fraction of the distinct words of title and summary
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SETTABLE = frozenset(FIELD_ORDER) - {"id", "created", "updated"}
_UNAVAILABLE = WriteFinding("target_unavailable", "target", "Search for a writable note and read it in full.")
_CONFLICT = WriteFinding("request_conflict", "request_id", "Use a new request id for a different create.")
_LINK_FORMAT = (
    "Use ids from cerebro_search in hub, related, superseded_by and [[note-id]] links. "
    'Any [[note-id]] counts, even in code (in TOML write [["name"]]).'
)
_CORE = "Core and P0 notes need a token with the core scope. Use P1 for a hub or P2/P3 for a regular note."


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

        key = None
        base = None
        created = None
        if request.operation == "create":
            if not isinstance(request.request_id, str) or not _REQUEST_ID.fullmatch(request.request_id):
                raise WriteRejected(WriteFinding("invalid_field", "request_id", "Supply a 1-64 character request id."))
            if request.target is not None or request.base_hash is not None:
                raise WriteRejected(WriteFinding("invalid_field", "target", "Omit target and base_hash on create."))
            key = create_request_key(ctx.token_id, request.request_id)
            created = self._notes.find_create(key)
            if created is not None:
                _require_core_scope(self._visible_target(created.note.id, ctx), ctx, "target")
            note_id = created.note.id if created else _CREATE_PLACEHOLDER
        elif request.operation == "update":
            if request.request_id is not None or not is_content_hash(request.base_hash):
                raise WriteRejected(WriteFinding("invalid_field", "base_hash", "Supply the hash from a full read."))
            try:
                note_id = NoteId(request.target or "")
            except InvalidValueError:
                raise WriteRejected(_UNAVAILABLE) from None
            base = self._visible_target(note_id, ctx)
            _require_core_scope(base, ctx, "target")
            if content_hash(base) != request.base_hash:
                raise self._stale()
        else:
            raise WriteRejected(WriteFinding("invalid_field", "operation", "Choose create or update."))

        try:
            candidate = _parse(request, note_id, now.date(), base)
        except WriteRejected:
            if created:
                raise WriteRejected(_CONFLICT) from None
            raise
        if not ctx.ceiling.allows(candidate.sensitivity):
            raise WriteRejected(WriteFinding("outside_ceiling", "sensitivity", "Choose an authorized sensitivity."))
        _require_core_scope(candidate, ctx, "priority")
        if key is not None and created is None:
            note_id = self._new_id(candidate.title)
            candidate = replace(candidate, id=note_id)
        unavailable = self._unavailable_references(candidate, ctx)
        if unavailable:
            raise WriteRejected(*unavailable[:12])
        if created is not None:
            # Compare with the note as the original commit stored it, ignoring service timestamps (ADR-0024).
            if replace(candidate, created=date.min, updated=date.min) != replace(
                created.note, created=date.min, updated=date.min
            ):
                raise WriteRejected(_CONFLICT)
            return AgentWriteResult(created.note.id, created.revision)
        if base and base.type != candidate.type and {base.type, candidate.type} & {CORE, HUB}:
            raise WriteRejected(
                WriteFinding("invalid_field", "type", "Keep the note in its original core, hub or regular category.")
            )
        if base and replace(candidate, updated=base.updated) == base:
            raise WriteRejected(WriteFinding("unchanged", "request", "The stored note already contains these changes."))
        report = validate_candidate(
            candidate, ctx=ctx, notes=self._notes, vocabulary=self._vocabulary, policy=AGENT_WRITE_POLICY, base=base
        )
        errors = projected(report, Severity.ERROR)
        if errors:
            raise WriteRejected(*errors)
        warnings = projected(report, Severity.WARNING)
        if base is None:
            warnings += self._possible_duplicate(candidate, ctx)
        message = AgentWriteCommit(note_id=note_id, written_by=ctx.principal.name, request_key=key)
        revision = self._commit(candidate, message, request.base_hash)
        return AgentWriteResult(
            note_id,
            revision,
            warnings,
            sensitivity_raise_warnings(base.sensitivity if base else None, candidate.sensitivity),
        )

    def _possible_duplicate(self, note: Note, ctx: AccessContext) -> tuple[WriteFinding, ...]:
        """A warning naming the most similar current note the caller can read, if one is similar enough."""
        hits = self._search.find_similar(f"{note.title} {note.summary}", ceiling=ctx.ceiling, limit=3)
        for hit in hits:
            if hit.score >= _DUPLICATE_SIMILARITY and visible_id(self._notes, hit.id, ctx, include_history=False):
                hint = f"Note `{hit.id.value}` has a similar title and summary. Read it and keep one note per topic."
                return (WriteFinding("possible_duplicate", "title", hint),)
        return ()

    def _new_id(self, title: str) -> NoteId:
        decomposed = unicodedata.normalize("NFKD", title.casefold())
        unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
        stem = re.sub(r"[^a-z0-9]+", "-", unaccented).strip("-")[:60].rstrip("-") or "note"
        for _ in range(32):
            note_id = NoteId(f"{stem}-{self._ids.new_note_suffix()}")
            if self._notes.get(note_id) is None:
                return note_id
        raise WriteRejected(WriteFinding("id_unavailable", "request", "Retry the create with the same request id."))

    def _unavailable_references(self, note: Note, ctx: AccessContext) -> list[WriteFinding]:
        """One finding per position (entry or text line) that references a missing or hidden note. It is built from
        the submitted note alone, so both cases read exactly the same. Only the first explains the link format."""
        visible: dict[NoteId, bool] = {note.id: True}
        positions: dict[tuple[str, str], None] = {}  # ordered and without duplicates
        for field, position, ref in _references(note):
            if ref not in visible:
                visible[ref] = visible_id(self._notes, ref, ctx, include_history=True) is not None
            if not visible[ref]:
                positions[field, position] = None
        return [
            WriteFinding(
                "broken_link",
                field,
                f"{position}: no readable note has this id." + ("" if number else f" {_LINK_FORMAT}"),
            )
            for number, (field, position) in enumerate(positions)
        ]

    def _visible_target(self, note_id: NoteId, ctx: AccessContext) -> Note:
        note = self._notes.get(note_id)
        if note is None or not ctx.ceiling.allows(note.sensitivity) or self._unavailable_references(note, ctx):
            raise WriteRejected(_UNAVAILABLE)
        return note

    @staticmethod
    def _stale() -> WriteRejected:
        return WriteRejected(WriteFinding("stale_base", "base_hash", "Read the full note again and reconcile changes."))

    def _commit(self, candidate: Note, message: AgentWriteCommit, expected: str | None) -> str:
        previous = self._notes.head()
        try:
            revision = self._notes.apply([NoteChange(note=candidate, expected_hash=expected)], message)
        except ContentConflict:
            raise self._stale() from None
        index_commit(self._search, self._graph, candidate, previous=previous, revision=revision)
        return revision


def _references(note: Note) -> Iterator[tuple[str, str, NoteId]]:
    """``(field, position, target)`` of every reference, including each ``[[...]]`` that redaction treats as one."""
    if note.hub is not None:
        yield "hub", "The hub id", note.hub
    for number, ref in enumerate(note.related, 1):
        yield "related", f"Related entry #{number}", ref
    if note.superseded_by is not None:
        yield "superseded_by", "The superseded_by id", note.superseded_by
    for field, text in (("title", note.title), ("summary", note.summary), ("body", note.body)):
        # Split on "\n" only: a link cannot contain one, but may contain other line separators.
        for number, line in enumerate(text.split("\n"), 1):
            for ref in loose_link_targets(line):
                yield field, f"Line {number} of {field}", ref


def _require_core_scope(note: Note, ctx: AccessContext, field: str) -> None:
    """Core and P0 notes form the overview's global rules: changing, archiving or superseding one, creating one
    or promoting a note to one needs ``core`` on top of ``write``. Called on the stored version and on the
    candidate, so a replay is authorized like the original request. Only visible notes reach this check."""
    if (note.type == CORE or note.priority is Priority.P0) and not ctx.can(Scope.CORE):
        raise WriteRejected(WriteFinding("core_scope_required", field, _CORE))


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
