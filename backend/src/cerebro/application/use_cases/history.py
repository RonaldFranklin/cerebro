"""Owner history projects each revision against its own contents and today's access rights."""

import difflib
import json
import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from functools import lru_cache

from cerebro.application.access import require_owner
from cerebro.application.errors import NoteReadError
from cerebro.application.history import (
    MAX_CHANGES,
    MAX_DIFF_CHARS,
    RECENT_WINDOW,
    ChangeDetail,
    ChangePage,
    ChangeSummary,
    ChangeUnavailable,
    HistoryCursor,
    HistoryRequest,
    HistoryVersion,
    is_revision,
)
from cerebro.application.ports.history import ChangeHistory
from cerebro.application.ports.notes import NoteRepository
from cerebro.domain.access import AccessContext
from cerebro.domain.frontmatter import note_to_raw
from cerebro.domain.links import redact_wikilinks
from cerebro.domain.note import Note, NoteId

_CONTEXT_LINES = 3
# Line alignment costs up to the product of the two changed regions; beyond this one they are shown as replaced.
_MAX_COMPARISONS = 4_000 * 4_000
_HUNK_START = re.compile(r"([-+])(\d+)")


@dataclass(frozen=True, slots=True)
class _Versions:
    info: HistoryVersion
    before: Note | None
    after: Note


class OwnerHistory:
    def __init__(
        self,
        notes: NoteRepository,
        history: ChangeHistory,
        guard: Callable[[], AbstractContextManager[None]] = nullcontext,
    ) -> None:
        self._guard = guard
        self._notes = notes
        self._history = history

    def list(self, ctx: AccessContext, cursor: HistoryCursor | None = None) -> ChangePage:
        require_owner(ctx, "inspect change history")
        with self._guard():
            return self._list(ctx, cursor)

    def _window(self, cursor: HistoryCursor | None) -> tuple[str | None, tuple[str, ...]]:
        recent = self._history.recent()
        if cursor is None:
            return next(iter(recent), None), recent
        # The browser authenticated this continuation. Its original HEAD must still be on the current chain.
        if cursor.head not in recent or not is_revision(cursor.start):
            raise ChangeUnavailable()
        return recent[0], self._history.recent(cursor.start)

    def _list(self, ctx: AccessContext, cursor: HistoryCursor | None) -> ChangePage:
        rows: list[ChangeSummary] = []
        head, revisions = self._window(cursor)
        for index, revision in enumerate(revisions):
            if index == RECENT_WINDOW or len(rows) == MAX_CHANGES:
                assert head is not None
                return ChangePage(tuple(rows), HistoryCursor(head, revision))
            try:
                versions = self._authorized(revision, ctx)
                if versions:
                    rows.append(self._summary(versions, ctx))
            except ChangeUnavailable:
                continue
        return ChangePage(tuple(rows), None)

    def detail(self, request: HistoryRequest, ctx: AccessContext) -> ChangeDetail:
        require_owner(ctx, "inspect change history")
        with self._guard():
            return self._detail(request, ctx)

    def _detail(self, request: HistoryRequest, ctx: AccessContext) -> ChangeDetail:
        revision = request.revision
        if not is_revision(revision) or revision not in self._window(request.cursor)[1][:RECENT_WINDOW]:
            raise ChangeUnavailable()
        versions = self._authorized(revision, ctx)
        if versions is None:
            raise ChangeUnavailable()
        before: list[str] = []
        if versions.before is not None and versions.info.parent:
            before = _lines(versions.before, self._visibility(versions.info.parent, ctx))
        after = _lines(versions.after, self._visibility(revision, ctx))
        diff, truncated = _bounded(_unified_diff(before, after))
        return ChangeDetail(self._summary(versions, ctx), diff, truncated)

    def _readable_current(self, note_id: NoteId) -> Note | None:
        try:
            return self._notes.get(note_id)
        except NoteReadError:
            return None

    def _authorized(self, revision: str, ctx: AccessContext) -> _Versions | None:
        info = self._history.change(revision)
        if info is None:
            return None
        current = self._readable_current(info.note_id)
        if not current or not ctx.ceiling.allows(current.sensitivity):
            return None
        after = self._history.note_at(revision, info.note_id)
        if not after or not ctx.ceiling.allows(after.sensitivity):
            return None
        before = self._history.note_at(info.parent, info.note_id) if info.parent else None
        if (before is None) != (info.operation == "create") or (before and not ctx.ceiling.allows(before.sensitivity)):
            return None
        return _Versions(info, before, after)

    def _summary(self, versions: _Versions, ctx: AccessContext) -> ChangeSummary:
        info = versions.info
        title = redact_wikilinks(versions.after.title, self._visibility(info.revision, ctx))
        return ChangeSummary(info.revision, info.note_id.value, title[:160], info.actor, info.timestamp, info.operation)

    def _visibility(self, revision: str, ctx: AccessContext) -> Callable[[NoteId], bool]:
        checked = 0

        @lru_cache(maxsize=128)
        def visible(note_id: NoteId) -> bool:
            nonlocal checked
            if checked >= 32:
                return False
            checked += 1
            current = self._readable_current(note_id)
            if current is None or not ctx.ceiling.allows(current.sensitivity):
                return False
            old = self._history.note_at(revision, note_id)
            return old is not None and ctx.ceiling.allows(old.sensitivity)

        return visible


def _lines(note: Note, visible: Callable[[NoteId], bool]) -> list[str]:
    raw = note_to_raw(note)
    for key, ref in (("hub", note.hub), ("superseded_by", note.superseded_by)):
        if ref and not visible(ref):
            raw.pop(key, None)
    if note.related:
        related = [ref.value for ref in note.related if visible(ref)]
        if related:
            raw["related"] = related
        else:
            raw.pop("related")
    for key in ("title", "summary", "lang"):
        raw[key] = redact_wikilinks(str(raw[key]), visible)
    frontmatter = [f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}" for key, value in raw.items()]
    document = "\n".join(["---", *frontmatter, "---", redact_wikilinks(note.body, visible)])
    # Not splitlines(): a change of final newline or of line ending must remain a difference.
    return document.split("\n")


def _bounded(lines: Iterator[str]) -> tuple[str, bool]:
    """The diff text within ``MAX_DIFF_CHARS``, and whether anything was left out of it.

    Removals come before their additions, so a diff that must be clipped gives removed lines at most half of it.
    """
    kept = list(lines)
    diff = "\n".join(kept)
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    room = MAX_DIFF_CHARS // 2
    # The first two lines are the file headers, not removals.
    for index in range(2, len(kept)):
        if kept[index].startswith("-"):
            kept[index] = kept[index][: max(room, 0)]
            room -= len(kept[index]) + 1
    return "\n".join(line for line in kept if line)[:MAX_DIFF_CHARS], True


def _unified_diff(before: list[str], after: list[str]) -> Iterator[str]:
    """Unified diff of two whole documents. Only the region between their common ends is compared."""
    shortest = min(len(before), len(after))
    head = 0
    while head < shortest and before[head] == after[head]:
        head += 1
    tail = 0
    while tail < shortest - head and before[-1 - tail] == after[-1 - tail]:
        tail += 1
    old, new = len(before) - head - tail, len(after) - head - tail
    if old * new > _MAX_COMPARISONS:
        yield from ("--- before", "+++ after", f"@@ -{head + 1},{old} +{head + 1},{new} @@")
        yield from (f"-{line}" for line in before[head : head + old])
        yield from (f"+{line}" for line in after[head : head + new])
        return
    skipped = max(head - _CONTEXT_LINES, 0)
    dropped = max(tail - _CONTEXT_LINES, 0)
    for line in difflib.unified_diff(
        before[skipped : len(before) - dropped],
        after[skipped : len(after) - dropped],
        fromfile="before",
        tofile="after",
        n=_CONTEXT_LINES,
        lineterm="",
    ):
        if line.startswith("@@"):
            # Content lines carry a one-character prefix, so only hunk headers start like this.
            line = _HUNK_START.sub(lambda start: f"{start[1]}{int(start[2]) + skipped}", line)
        yield line
