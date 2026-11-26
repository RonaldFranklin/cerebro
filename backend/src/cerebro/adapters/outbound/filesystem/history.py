"""Read exact note blobs from the bounded first-parent history with authenticated attribution formats."""

import re
from datetime import datetime

from cerebro.adapters.outbound.filesystem.errors import NoteFileError
from cerebro.adapters.outbound.filesystem.repository import FOLDERS, MAX_FILE_BYTES, decode_note_file
from cerebro.adapters.outbound.git import GitError, GitRepo
from cerebro.application.history import RECENT_WINDOW, ChangeUnavailable, HistoryVersion
from cerebro.application.ports.system import Cipher
from cerebro.domain.access import validate_agent_name
from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import Note, NoteId

_DIRECT = re.compile(
    r"(?P<verb>Add|Update) note: (?P<id>[a-z0-9-]{1,80})\n\nWritten-by: (?P<actor>[^\n]+)"
    r"(?P<receipt>\nWrite-Request: [0-9a-f]{64}\nWrite-Receipt: [a-zA-Z0-9+/=]{1,4096})?"
)
_LEGACY = re.compile(
    r"(?P<verb>Add|Update|Link|Deprecate) note: (?P<id>[a-z0-9-]{1,80})\n\nProposed-by: (?P<actor>[^\n]+)"
    r"\nCurated-by: (?P<curator>[^\n]+)\nApproved-by: (?P<owner>[^\n]+)\nProposal-Id: (?P<proposal>[^\n]+)"
)


class GitChangeHistory:
    def __init__(self, git: GitRepo, cipher: Cipher) -> None:
        self._git = git
        self._cipher = cipher

    def recent(self) -> tuple[str, ...]:
        return self._git.recent_revisions(RECENT_WINDOW)

    def change(self, revision: str) -> HistoryVersion | None:
        try:
            metadata = self._git.history_commit(revision)
        except GitError:
            raise ChangeUnavailable() from None
        if not metadata:
            return None
        parent, timestamp, message, path = metadata
        direct = _DIRECT.fullmatch(message)
        match = direct or _LEGACY.fullmatch(message)
        if not match:
            return None
        try:
            note_id = NoteId(match["id"])
            actor = match["actor"]
            if direct:
                validate_agent_name(actor)
                if (match["verb"] == "Add") != bool(match["receipt"]):
                    return None
            else:
                CommitMessage(
                    subject=message.split("\n", 1)[0],
                    proposal_id=match["proposal"],
                    proposed_by=actor,
                    curated_by=match["curator"],
                    approved_by=match["owner"],
                )
            moment = datetime.fromisoformat(timestamp)
        except InvalidValueError, ValueError:
            return None
        if path not in {f"{folder}/{note_id.value}.md" for folder in FOLDERS} or moment.tzinfo is None:
            return None
        return HistoryVersion(
            revision, parent or None, note_id, actor, timestamp, "create" if match["verb"] == "Add" else "update"
        )

    def note_at(self, revision: str, note_id: NoteId) -> Note | None:
        try:
            entry = self._git.historical_blob(
                revision, [f"{folder}/{note_id.value}.md" for folder in FOLDERS], MAX_FILE_BYTES
            )
            return decode_note_file(*entry, self._cipher) if entry else None
        except GitError, NoteFileError:
            raise ChangeUnavailable() from None
