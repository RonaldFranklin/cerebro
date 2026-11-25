"""``Proposal``: a change an agent asks for, and its lifecycle.

Lifecycle (see ``docs/domain-model.md``)::

    received -> validated -> curated -> approved -> applied
    received -> rejected                  (validation failed)
    curated  -> rejected | needs_owner | approved
    needs_owner -> approved | rejected
    approved -> needs_owner               (conflict found when applying)

Every transition is a method returning a new ``Proposal`` (the entity is immutable); calling it from a status
that does not allow it raises ``InvalidTransition``. Time is always passed in, never read from the clock.
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Self

from cerebro.domain.access import validate_agent_name
from cerebro.domain.commit_message import OWNER_APPROVER, CommitMessage
from cerebro.domain.content_hash import is_content_hash
from cerebro.domain.decision import Decision, DecisionKind
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.errors import InvalidTransition, InvalidValueError
from cerebro.domain.note import NoteId
from cerebro.domain.report import ValidationReport
from cerebro.domain.vocabulary import Area, NoteType

_PROPOSAL_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")


def validate_proposal_id(value: str) -> str:
    if not isinstance(value, str) or _PROPOSAL_ID.fullmatch(value) is None:
        raise InvalidValueError("id", "must be 1-64 characters of letters, digits, '_' or '-'")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposalLimits:
    per_hour: int = 20
    pending: int = 10

    def __post_init__(self) -> None:
        for field, value in (("per_hour", self.per_hour), ("pending", self.pending)):
            if type(value) is not int or value <= 0:
                raise InvalidValueError(field, "expected a positive integer")


class ProposalKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    LINK = "link"
    DEPRECATE = "deprecate"


class ProposalStatus(StrEnum):
    RECEIVED = "received"
    VALIDATED = "validated"
    CURATED = "curated"
    APPROVED = "approved"
    NEEDS_OWNER = "needs_owner"
    REJECTED = "rejected"
    APPLIED = "applied"

    @property
    def is_terminal(self) -> bool:
        return self in (ProposalStatus.REJECTED, ProposalStatus.APPLIED)


S = ProposalStatus
ALLOWED_TRANSITIONS: Final[dict[ProposalStatus, frozenset[ProposalStatus]]] = {
    S.RECEIVED: frozenset({S.VALIDATED, S.REJECTED}),
    S.VALIDATED: frozenset({S.CURATED}),
    S.CURATED: frozenset({S.APPROVED, S.REJECTED, S.NEEDS_OWNER}),
    S.NEEDS_OWNER: frozenset({S.APPROVED, S.REJECTED}),
    S.APPROVED: frozenset({S.APPLIED, S.NEEDS_OWNER}),
    S.REJECTED: frozenset(),
    S.APPLIED: frozenset(),
}

MAX_TEXT_LENGTH = 2000


def _opt_text(value: object, field: str) -> None:
    if value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH):
        raise InvalidValueError(field, f"expected a string of at most {MAX_TEXT_LENGTH} characters")


def _require_aware(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidValueError(field, "expected a timezone-aware datetime")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposedContent:
    """The typed content of a proposal. Metadata fields are suggestions; the curator and owner have the final say.

    ``body`` is always the **full** new body (a patch format would need its own ADR).
    """

    title: str | None = None
    body: str | None = None
    area: Area | None = None
    type: NoteType | None = None
    priority: Priority | None = None
    sensitivity: Sensitivity | None = None
    rationale: str = ""
    related: tuple[NoteId, ...] = ()
    superseded_by: NoteId | None = None
    summary: str | None = None
    hub: NoteId | None = None

    def __post_init__(self) -> None:
        for name, value, expected in (
            ("title", self.title, str),
            ("body", self.body, str),
            ("summary", self.summary, str),
            ("hub", self.hub, NoteId),
            ("area", self.area, Area),
            ("type", self.type, NoteType),
            ("priority", self.priority, Priority),
            ("sensitivity", self.sensitivity, Sensitivity),
            ("superseded_by", self.superseded_by, NoteId),
        ):
            if value is not None and not isinstance(value, expected):
                raise InvalidValueError(name, f"expected {expected.__name__}")
        if not isinstance(self.rationale, str):
            raise InvalidValueError("rationale", "expected a string")
        if not isinstance(self.related, tuple) or not all(isinstance(r, NoteId) for r in self.related):
            raise InvalidValueError("related", "expected a tuple of NoteId")


def validate_submission_shape(
    kind: ProposalKind, target: NoteId | None, base_hash: str | None, content: ProposedContent
) -> None:
    """Required and forbidden fields for each submission kind."""
    if kind is ProposalKind.CREATE:
        if target is not None:
            raise InvalidValueError("target", "not allowed for create")
        if base_hash is not None:
            raise InvalidValueError("base_hash", "not allowed for create")
        if content.title is None or not content.title.strip():
            raise InvalidValueError("content.title", "required for create")
        if content.body is None:
            raise InvalidValueError("content.body", "required for create")
        return
    if target is None:
        raise InvalidValueError("target", f"required for {kind.value}")
    if base_hash is None:
        raise InvalidValueError("base_hash", f"required for {kind.value}")
    if kind is ProposalKind.UPDATE:
        if content.body is None:
            raise InvalidValueError("content.body", "required for update (the full new body)")
        return
    if content.body is not None:
        raise InvalidValueError("content.body", f"not allowed for {kind.value}")
    if kind is ProposalKind.LINK and not content.related:
        raise InvalidValueError("content.related", "required for link")


@dataclass(frozen=True, slots=True, kw_only=True)
class Proposal:
    """Required fields per kind (checked on construction):

    - ``create``: no ``target`` and no ``base_hash``; ``content.title`` and ``content.body``.
    - ``update``: ``target`` and ``base_hash`` (hash of the note the change was prepared against); ``content.body``.
    - ``link``: ``target`` and ``base_hash``; ``content.related`` non-empty; no body.
    - ``deprecate``: ``target`` and ``base_hash``; no body.

    ``source_agent`` always comes from the access context, never from the payload. ``conflict`` is set when
    applying found that the target changed after ``base_hash``.
    ``revision`` is the optimistic-concurrency counter of the stored copy: 0 on creation, bumped only by
    ``ProposalStore.save`` (transitions never touch it; a loaded proposal carries the revision it was read at).
    """

    id: str
    kind: ProposalKind
    status: ProposalStatus = ProposalStatus.RECEIVED
    target: NoteId | None = None
    content: ProposedContent
    source_agent: str
    source_session: str | None = None
    source_context: str | None = None
    created_at: datetime
    updated_at: datetime
    base_hash: str | None = None
    validation: ValidationReport | None = None
    decision: Decision | None = None
    curated_by: str | None = None
    approved_by: str | None = None
    conflict: bool = False
    revision: int = 0

    def __post_init__(self) -> None:
        validate_proposal_id(self.id)
        if not isinstance(self.kind, ProposalKind):
            raise InvalidValueError("kind", "expected a ProposalKind")
        if not isinstance(self.status, ProposalStatus):
            raise InvalidValueError("status", "expected a ProposalStatus")
        if self.target is not None and not isinstance(self.target, NoteId):
            raise InvalidValueError("target", "expected a NoteId")
        if not isinstance(self.content, ProposedContent):
            raise InvalidValueError("content", "expected ProposedContent")
        validate_agent_name(self.source_agent)
        _opt_text(self.source_session, "source_session")
        _opt_text(self.source_context, "source_context")
        _require_aware(self.created_at, "created_at")
        if _require_aware(self.updated_at, "updated_at") < self.created_at:
            raise InvalidValueError("updated_at", "must not precede created_at")
        if self.base_hash is not None and not is_content_hash(self.base_hash):
            raise InvalidValueError("base_hash", "expected a lowercase SHA-256 hex digest")
        if self.validation is not None and not isinstance(self.validation, ValidationReport):
            raise InvalidValueError("validation", "expected a ValidationReport")
        if self.decision is not None and not isinstance(self.decision, Decision):
            raise InvalidValueError("decision", "expected a Decision")
        if self.curated_by is not None:
            CommitMessage.check_curator(self.curated_by)
        if self.approved_by is not None:
            CommitMessage.check_approver(self.approved_by)
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 0:
            raise InvalidValueError("revision", "expected a non-negative integer")
        if not isinstance(self.conflict, bool):
            raise InvalidValueError("conflict", "expected a bool")
        if self.curated_by is not None and self.curated_by == self.source_agent:
            raise InvalidValueError("curated_by", "an agent cannot curate its own proposal")
        if self.approved_by is not None and self.approved_by == self.source_agent:
            raise InvalidValueError("approved_by", "an agent cannot approve its own proposal")
        if self.status in (ProposalStatus.APPROVED, ProposalStatus.APPLIED) and (
            self.curated_by is None or self.approved_by is None or self.decision is None
        ):
            raise InvalidValueError("status", f"{self.status.value} needs a decision, curated_by and approved_by")
        validate_submission_shape(self.kind, self.target, self.base_hash, self.content)

    # -- lifecycle ---------------------------------------------------------------------------------------------

    def _move(self, action: str, to: ProposalStatus, now: datetime, **changes: Any) -> Self:
        if to not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTransition(action, self.status.value)
        _require_aware(now, "now")
        if now < self.updated_at:
            raise InvalidValueError("now", "must not precede the last update")
        return replace(self, status=to, updated_at=now, **changes)

    def mark_validated(self, report: ValidationReport, now: datetime) -> Self:
        """received -> validated. ``report`` must have no errors (warnings are kept)."""
        if not isinstance(report, ValidationReport) or not report.ok:
            raise InvalidValueError("report", "a validated proposal needs a report without errors")
        return self._move("validate", S.VALIDATED, now, validation=report)

    def reject_invalid(self, report: ValidationReport, now: datetime) -> Self:
        """received -> rejected, because validation found errors."""
        if self.status is not S.RECEIVED:
            raise InvalidTransition("reject", self.status.value)
        if not isinstance(report, ValidationReport) or report.ok:
            raise InvalidValueError("report", "rejecting for validation needs a report with errors")
        return self._move("reject", S.REJECTED, now, validation=report)

    def mark_curated(self, decision: Decision, curated_by: str, now: datetime) -> Self:
        """validated -> curated, recording the curator's decision."""
        if not isinstance(decision, Decision):
            raise InvalidValueError("decision", "expected a Decision")
        CommitMessage.check_curator(curated_by)
        return self._move("curate", S.CURATED, now, decision=decision, curated_by=curated_by)

    def mark_needs_owner(self, now: datetime, *, conflict: bool = False) -> Self:
        """curated -> needs_owner, or approved -> needs_owner (``conflict=True``: the target changed on apply)."""
        if self.status is S.APPROVED and not conflict:
            raise InvalidTransition("send to the owner", self.status.value)
        # An approval is void once the proposal goes back to the owner.
        return self._move("send to the owner", S.NEEDS_OWNER, now, conflict=conflict or self.conflict, approved_by=None)

    def approve(self, approved_by: str, now: datetime) -> Self:
        """curated | needs_owner -> approved.

        From ``curated`` the decision must be ``merge_into`` or ``create`` (a ``reject`` / ``needs_owner`` decision
        is not approvable as is). The owner may approve from ``needs_owner``. The ``conflict`` flag is kept: the
        owner must ``rebase`` a conflicted proposal first.
        """
        CommitMessage.check_approver(approved_by)
        if self.status is S.CURATED and (
            self.decision is None or self.decision.kind not in (DecisionKind.MERGE_INTO, DecisionKind.CREATE)
        ):
            raise InvalidTransition("approve", f"{self.status.value} without an approvable decision")
        if self.conflict:
            raise InvalidTransition("approve", "conflicted (rebase it first)")
        if self.status in (S.CURATED, S.NEEDS_OWNER) and self._commit_target() is None:
            raise InvalidTransition("approve", "no note id to commit (the decision names no target)")
        return self._move("approve", S.APPROVED, now, approved_by=approved_by)

    def rebase(self, new_base_hash: str, now: datetime) -> Self:
        """Resolve a conflict: point ``base_hash`` at the current note and clear the flag (needs_owner only)."""
        if self.status is not S.NEEDS_OWNER or not self.conflict:
            raise InvalidTransition("rebase", self.status.value)
        if not is_content_hash(new_base_hash):
            raise InvalidValueError("base_hash", "expected a lowercase SHA-256 hex digest")
        _require_aware(now, "now")
        if now < self.updated_at:
            raise InvalidValueError("now", "must not precede the last update")
        return replace(self, base_hash=new_base_hash, conflict=False, updated_at=now)

    def override_decision(self, decision: Decision, now: datetime) -> Self:
        """curated | needs_owner: the owner replaces the decision (status unchanged); approval comes after."""
        if self.status not in (S.CURATED, S.NEEDS_OWNER):
            raise InvalidTransition("override the decision of", self.status.value)
        if not isinstance(decision, Decision):
            raise InvalidValueError("decision", "expected a Decision")
        _require_aware(now, "now")
        if now < self.updated_at:
            raise InvalidValueError("now", "must not precede the last update")
        return replace(self, decision=decision, updated_at=now)

    def reject(self, reason: str, now: datetime) -> Self:
        """curated | needs_owner -> rejected. The reason is stored as a ``reject`` decision."""
        if self.status not in (S.CURATED, S.NEEDS_OWNER):
            raise InvalidTransition("reject", self.status.value)
        return self._move("reject", S.REJECTED, now, decision=Decision.reject(reason))

    def mark_applied(self, now: datetime) -> Self:
        """approved -> applied."""
        return self._move("apply", S.APPLIED, now)

    def _commit_target(self) -> NoteId | None:
        if self.decision is not None and self.decision.target is not None:
            return self.decision.target
        return self.target

    # -- commit ------------------------------------------------------------------------------------------------

    def commit_message(self) -> CommitMessage:
        """The commit message for an approved proposal (subject built from the note id, never from free text)."""
        if self.status is not S.APPROVED or self.curated_by is None or self.approved_by is None:
            raise InvalidTransition("commit", self.status.value)
        note_id = self._commit_target()
        if note_id is None:
            raise InvalidValueError("target", "no note id to name the commit after")
        verb = {
            ProposalKind.CREATE: "Add",
            ProposalKind.UPDATE: "Update",
            ProposalKind.LINK: "Link",
            ProposalKind.DEPRECATE: "Deprecate",
        }[self.kind]
        return CommitMessage(
            subject=f"{verb} note: {note_id.value}",
            proposal_id=self.id,
            proposed_by=self.source_agent,
            curated_by=self.curated_by,
            approved_by=self.approved_by,
        )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "OWNER_APPROVER",
    "Proposal",
    "ProposalKind",
    "ProposalStatus",
    "ProposedContent",
]
