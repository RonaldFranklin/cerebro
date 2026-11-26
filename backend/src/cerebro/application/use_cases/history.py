"""Owner history projects each revision against its own contents and today's access rights."""

import difflib
import json
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from functools import lru_cache

from cerebro.application.access import require_owner
from cerebro.application.history import (
    MAX_CHANGES,
    MAX_DIFF_CHARS,
    RECENT_WINDOW,
    ChangeDetail,
    ChangeSummary,
    ChangeUnavailable,
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

    def list(self, ctx: AccessContext) -> tuple[ChangeSummary, ...]:
        require_owner(ctx, "inspect change history")
        with self._guard():
            return self._list(ctx)

    def _list(self, ctx: AccessContext) -> tuple[ChangeSummary, ...]:
        rows = []
        for revision in self._history.recent()[:RECENT_WINDOW]:
            try:
                versions = self._authorized(revision, ctx)
                if versions:
                    rows.append(self._summary(versions, ctx))
            except ChangeUnavailable:
                continue
            if len(rows) == MAX_CHANGES:
                break
        return tuple(rows)

    def detail(self, request: HistoryRequest, ctx: AccessContext) -> ChangeDetail:
        require_owner(ctx, "inspect change history")
        with self._guard():
            return self._detail(request.revision, ctx)

    def _detail(self, revision: str, ctx: AccessContext) -> ChangeDetail:
        if not is_revision(revision) or revision not in self._history.recent()[:RECENT_WINDOW]:
            raise ChangeUnavailable()
        versions = self._authorized(revision, ctx)
        if versions is None:
            raise ChangeUnavailable()
        before: list[str] = []
        short_before = False
        if versions.before is not None and versions.info.parent:
            before, short_before = _lines(versions.before, self._visibility(versions.info.parent, ctx))
        after, short_after = _lines(versions.after, self._visibility(revision, ctx))
        diff = "\n".join(difflib.unified_diff(before, after, fromfile="before", tofile="after", lineterm=""))
        return ChangeDetail(
            self._summary(versions, ctx),
            diff[:MAX_DIFF_CHARS],
            short_before or short_after or len(diff) > MAX_DIFF_CHARS,
        )

    def _authorized(self, revision: str, ctx: AccessContext) -> _Versions | None:
        info = self._history.change(revision)
        if info is None:
            return None
        current = self._notes.get(info.note_id)
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
            current = self._notes.get(note_id)
            if current is None or not ctx.ceiling.allows(current.sensitivity):
                return False
            old = self._history.note_at(revision, note_id)
            return old is not None and ctx.ceiling.allows(old.sensitivity)

        return visible


def _lines(note: Note, visible: Callable[[NoteId], bool]) -> tuple[list[str], bool]:
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
    bounded = document[:MAX_DIFF_CHARS]
    lines = bounded.splitlines()
    return lines[:512], len(lines) > 512 or len(document) > MAX_DIFF_CHARS
