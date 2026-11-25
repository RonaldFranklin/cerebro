"""``CommitMessage``: the git commit message of an applied change, with attribution trailers.

Every value is validated, never silently altered: anything that could add a line, a fake trailer or control
characters raises ``InvalidValueError``. Subjects are built from the note id (a slug), never from free text.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from cerebro.domain.access import validate_agent_name
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
        validate_agent_name(self.proposed_by)  # also keeps "owner" out of Proposed-by
        _attribution(self.curated_by, "curated_by")
        _attribution(self.approved_by, "approved_by")

    @staticmethod
    def check_attribution(value: object, field: str) -> str:
        """Validate a `curated_by` / `approved_by` value before it is stored anywhere."""
        return _attribution(value, field)

    def render(self) -> str:
        """Subject, blank line, then the trailers (one per line, no trailing newline)."""
        return (
            f"{self.subject}\n\n"
            f"Proposed-by: {self.proposed_by}\n"
            f"Curated-by: {self.curated_by}\n"
            f"Approved-by: {self.approved_by}\n"
            f"Proposal-Id: {self.proposal_id}"
        )
