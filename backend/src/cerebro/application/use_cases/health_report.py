"""``HealthReport``: everything wrong with the knowledge base, for the owner."""

from collections.abc import Callable, Iterable, Mapping

from cerebro.application.access import require_owner
from cerebro.application.dto import HealthCode, HealthIssue, HealthReportRequest, HealthReportResult
from cerebro.application.errors import NoteReadError, PermissionDenied
from cerebro.application.index_state import index_is_stale
from cerebro.application.ports.backup import BackupStatus
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import FileProblem, NoteRepository
from cerebro.application.ports.search import MAX_LIMIT, SearchIndex, SearchQuery
from cerebro.application.ports.system import Clock
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.budgets import core_budget_findings, word_count
from cerebro.domain.cross_note import cross_note_findings
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.report import OWNER_EDIT_POLICY, Finding, Severity, ViolationCode
from cerebro.domain.secrets import secret_message
from cerebro.domain.validation import validate_note
from cerebro.domain.vocabulary import Vocabulary

FULL_VIEW = AccessCeiling(Sensitivity.RESTRICTED)


class HealthReport:
    """Owner only, and only on a listener that may see every note (ceiling ``restricted``): the report names notes,
    hub chains and index differences, so on a lower ceiling it would reveal what the ceiling hides.

    It runs the **same domain rules** as the write paths over every stored note, with the owner-edit severity
    policy (broken links warn): per-note rules, budgets, cross-note rules (broken links, hubs, ``superseded_by``
    cycles, hub chains), the combined P0 budget. It adds orphan notes and overdue ``review_by`` dates, then checks both
    index revisions and individual entries against the content repository. A stale index requires ``RebuildIndex``.

    Problem files come first, by path with the fixed reason: ``note_file_excluded`` for a committed file that is
    not a valid note (left out of everything else), ``note_file_blocking`` for a file that differs from the last
    commit or cannot be read (rebuilds and writes fail until the owner fixes it; the index entries are then not
    compared, since the files read are not the indexed revision). The report never fails because of a note file.

    Secrets in existing notes are reported as ``secret_detected`` with the field, detector and line only,
    described from the findings' structured fields (the scanner's message text is never used), so the
    report never carries any part of a secret.
    """

    def __init__(
        self,
        notes: NoteRepository,
        search: SearchIndex,
        graph: GraphIndex,
        clock: Clock,
        vocabulary: Vocabulary,
        quarantines: Callable[[], Iterable[str]] = tuple,
        *,
        backup: BackupStatus,
    ) -> None:
        self._backup = backup
        self._quarantines = quarantines
        self._notes = notes
        self._search = search
        self._graph = graph
        self._clock = clock
        self._vocabulary = vocabulary

    def execute(self, request: HealthReportRequest, ctx: AccessContext) -> HealthReportResult:
        require_owner(ctx, "read the health report")
        if ctx.ceiling.level is not Sensitivity.RESTRICTED:
            raise PermissionDenied("read the health report below the restricted ceiling")
        checked_at = self._clock.now()
        last_backup = self._backup.last()
        issues = [
            HealthIssue(
                subject=name,
                code=HealthCode.CHANGES_QUARANTINED,
                severity=Severity.WARNING,
                field=None,
                message=(
                    "Incomplete quarantine. Inspect this state directory folder and the content checkout."
                    if name.endswith(".partial")
                    else "Uncommitted content changes were saved in this state directory folder."
                ),
            )
            for name in self._quarantines()
        ]
        try:
            scan = self._notes.scan()
        except NoteReadError as error:
            problems = [FileProblem(path, reason, stable=False) for path, reason in error.problems]
            return HealthReportResult(
                issues=(
                    *map(_file_issue, problems or [FileProblem("notes", "cannot be listed", stable=False)]),
                    *issues,
                ),
                notes_checked=0,
                search_index_checked=False,
                checked_at=checked_at,
                last_backup=last_backup,
                # A repository that cannot be scanned may not answer for its revision either: do not ask.
                revision=None,
            )
        notes = scan.notes
        revision = self._notes.head()
        now = self._clock.now()
        issues.extend(map(_file_issue, scan.problems))
        issues.extend(self._note_issues(notes))
        issues.extend(
            HealthIssue(
                subject=str(note_id),
                code=HealthCode.ORPHAN_NOTE,
                severity=Severity.WARNING,
                field=None,
                message="no links to or from any other note",
            )
            for note_id in self._graph.orphans(ceiling=FULL_VIEW)
        )
        issues.extend(
            HealthIssue(
                subject=str(n.id),
                code=HealthCode.REVIEW_OVERDUE,
                severity=Severity.WARNING,
                field="review_by",
                message=f"review was due on {n.review_by}",
            )
            for n in notes
            if n.is_current and n.review_by is not None and n.review_by < now.date()
        )
        search_checked = scan.stable and len(notes) <= MAX_LIMIT
        issues.extend(self._index_issues(notes, entries=scan.stable, search_checked=search_checked))
        return HealthReportResult(
            issues=tuple(issues),
            notes_checked=len(notes),
            search_index_checked=search_checked,
            checked_at=checked_at,
            last_backup=last_backup,
            revision=revision,
        )

    def _note_issues(self, notes: tuple[Note, ...]) -> Iterable[HealthIssue]:
        snapshot = {ref.id: ref for ref in (NoteRef.from_note(n) for n in notes)}
        for note in notes:
            findings = [*cross_note_findings(snapshot[note.id], snapshot)]
            report = validate_note(
                note, self._vocabulary, OWNER_EDIT_POLICY, known_id=snapshot.__contains__
            ) + OWNER_EDIT_POLICY.report(findings)
            yield from (
                HealthIssue(
                    subject=str(note.id),
                    code=v.code,
                    severity=v.severity,
                    field=v.field,
                    message=_message(v.code, v.message, v.detector, v.line),
                )
                for v in report.violations
            )
        words = {n.id: word_count(n.body) for n in notes if n.priority is Priority.P0 and n.is_current}
        yield from (_finding_issue("core-budget", f) for f in core_budget_findings(words))

    def _index_issues(self, notes: tuple[Note, ...], *, entries: bool, search_checked: bool) -> Iterable[HealthIssue]:
        if index_is_stale(self._notes, self._search, self._graph):
            yield HealthIssue(
                subject="index",
                code=HealthCode.INDEX_STALE,
                severity=Severity.ERROR,
                field=None,
                message="an index was not built from the current content revision: run RebuildIndex",
            )
        if not entries:
            return
        by_id = {n.id: n for n in notes}
        stored_refs = {n.id: NoteRef.from_note(n) for n in notes}
        indexed_refs = {r.id: r for r in self._graph.graph(ceiling=FULL_VIEW, include_history=True).nodes}
        yield from _differences("graph index", HealthCode.GRAPH_INDEX_STALE, stored_refs, indexed_refs)
        if not search_checked:
            return
        hits = self._search.search(SearchQuery(ceiling=FULL_VIEW, include_history=True, limit=MAX_LIMIT))
        stored = {i: _search_view(n) for i, n in by_id.items()}
        indexed = {h.id: (h.title, h.summary, h.area, h.type, h.priority, h.sensitivity, h.status) for h in hits}
        yield from _differences("search index", HealthCode.SEARCH_INDEX_STALE, stored, indexed)


def _file_issue(problem: FileProblem) -> HealthIssue:
    if problem.stable:
        code, effect = HealthCode.NOTE_FILE_EXCLUDED, "left out of reads, search and the graph until fixed"
    else:
        code, effect = HealthCode.NOTE_FILE_BLOCKING, "index rebuilds and writes fail until it is fixed"
    return HealthIssue(
        subject=problem.path, code=code, severity=Severity.ERROR, field=None, message=f"{problem.reason} ({effect})"
    )


def _search_view(n: Note) -> tuple[object, ...]:
    return (n.title, n.summary, n.area, n.type, n.priority, n.sensitivity, n.status)


def _differences(
    name: str, code: HealthCode, stored: Mapping[NoteId, object], indexed: Mapping[NoteId, object]
) -> Iterable[HealthIssue]:
    for note_id in sorted(set(stored) | set(indexed), key=str):
        if note_id not in indexed:
            reason = "missing from the index"
        elif note_id not in stored:
            reason = "in the index but not in the repository"
        elif stored[note_id] != indexed[note_id]:
            reason = "out of date in the index"
        else:
            continue
        yield HealthIssue(
            subject=str(note_id), code=code, severity=Severity.ERROR, field=None, message=f"{name}: {reason}"
        )


def _finding_issue(subject: str, finding: Finding) -> HealthIssue:
    severity = OWNER_EDIT_POLICY.severity(finding.code)
    return HealthIssue(
        subject=subject,
        code=finding.code,
        severity=severity,
        field=finding.field,
        message=_message(finding.code, finding.message, finding.detector, finding.line),
    )


def _message(code: ViolationCode, message: str, detector: str | None, line: int | None) -> str:
    """Secret findings are described from their structured fields (detector, line), never from the scanner's
    ``message``: whatever that text says, no part of a value can reach the report."""
    if code is ViolationCode.SECRET_DETECTED:
        return secret_message(detector, line)
    return message
