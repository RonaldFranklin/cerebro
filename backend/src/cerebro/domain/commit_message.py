"""``CommitMessage``: the git commit message of an applied change, with attribution trailers.

Every value is validated, never silently altered: anything that could add a line, a fake trailer or control
characters raises ``InvalidValueError``. Subjects are built from the note id (a slug), never from free text.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from cerebro.domain.access import validate_agent_name, validate_curator_name
from cerebro.domain.errors import InvalidValueError

MAX_SUBJECT_LENGTH = 100
_ATTRIBUTION: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@+-]{0,99}")
_PROPOSAL_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")
OWNER_APPROVER: Final = "owner"


def _attribution(value: object, field: str) -> str:
    if not isinstance(value, str) or _ATTRIBUTION.fullmatch(value) is None:
        raise InvalidValueError(field, "expected 1-100 characters of letters, digits and . _ / @ + -")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class CommitMessage:
    subject: str
    proposal_id: str
    proposed_by: str
    curated_by: str
    approved_by: str

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str) or not self.subject.strip():
            raise InvalidValueError("subject", "must be a non-blank string")
        if len(self.subject) > MAX_SUBJECT_LENGTH:
            raise InvalidValueError("subject", f"longer than {MAX_SUBJECT_LENGTH} characters")
        if any(unicodedata.category(c)[0] in "CZ" and c != " " for c in self.subject):
            raise InvalidValueError("subject", "must be a single line without control characters")
        if self.subject != self.subject.strip():
            raise InvalidValueError("subject", "must not start or end with whitespace")
        if not isinstance(self.proposal_id, str) or _PROPOSAL_ID.fullmatch(self.proposal_id) is None:
            raise InvalidValueError("proposal_id", "must be 1-64 characters of letters, digits, '_' or '-'")
        _attribution(self.curated_by, "curated_by")
        CommitMessage.check_approver(self.approved_by)
        if self.proposed_by == OWNER_APPROVER and self.curated_by == self.approved_by == OWNER_APPROVER:
            return  # an owner edit: the only commit that may carry ``Proposed-by: owner`` / ``Curated-by: owner``
        validate_curator_name(self.curated_by)  # ``curator/...`` only: no agent or owner look-alike
        validate_agent_name(self.proposed_by)  # keeps "owner" out of Proposed-by for every agent proposal

    @staticmethod
    def check_attribution(value: object, field: str) -> str:
        """Validate an attribution (well-formed, no injection) before it is stored anywhere."""
        return _attribution(value, field)

    @staticmethod
    def check_approver(value: object) -> str:
        """Validate an `approved_by` value. Only ``owner`` is accepted: deliberate, as no auto-approval exists yet.

        An autonomy policy needs its own ADR, which must define who else may approve and how they are named.
        """
        _attribution(value, "approved_by")
        if value != OWNER_APPROVER:
            raise InvalidValueError("approved_by", f"must be '{OWNER_APPROVER}' (no other approver exists yet)")
        return OWNER_APPROVER

    @staticmethod
    def check_curator(value: object) -> str:
        """Validate a `curated_by` value of a proposal: a well-formed attribution that starts with ``curator/``."""
        return validate_curator_name(_attribution(value, "curated_by"))

    @classmethod
    def for_owner_edit(cls, *, subject: str, edit_id: str) -> CommitMessage:
        """Commit of a direct owner edit: the owner is proposer, curator and approver; ``edit_id`` is the audit id."""
        return cls(
            subject=subject,
            proposal_id=edit_id,
            proposed_by=OWNER_APPROVER,
            curated_by=OWNER_APPROVER,
            approved_by=OWNER_APPROVER,
        )

    def render(self) -> str:
        """Subject, blank line, then the trailers (one per line, no trailing newline)."""
        return (
            f"{self.subject}\n\n"
            f"Proposed-by: {self.proposed_by}\n"
            f"Curated-by: {self.curated_by}\n"
            f"Approved-by: {self.approved_by}\n"
            f"Proposal-Id: {self.proposal_id}"
        )
