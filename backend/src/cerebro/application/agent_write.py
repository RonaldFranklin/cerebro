"""Agent write requests, receipts and bounded feedback suitable for presentation to the caller."""

from dataclasses import dataclass

from cerebro.application.dto import ApplyWarning
from cerebro.application.errors import ApplicationError
from cerebro.domain.note import NoteId
from cerebro.domain.report import Severity, ValidationReport, ViolationCode


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentWriteRequest:
    operation: str
    frontmatter: object
    body: object
    target: str | None = None
    base_hash: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class WriteFinding:
    code: str
    field: str
    hint: str


class WriteRejected(ApplicationError):  # noqa: N818
    def __init__(self, first: WriteFinding, *others: WriteFinding) -> None:
        self.findings = (first, *others)
        super().__init__(first.code)


@dataclass(frozen=True, slots=True)
class AgentWriteResult:
    note_id: NoteId
    revision: str
    findings: tuple[WriteFinding, ...] = ()
    warnings: tuple[ApplyWarning, ...] = ()


# Cross-note rules can describe other notes or the size of the private corpus.
# Only fixed remediation text crosses the agent boundary for those rules.
_HINTS = {
    ViolationCode.LINK_TO_HIGHER_SENSITIVITY: "Keep linked notes in compatible sensitivity tiers.",
    ViolationCode.CORE_OVER_BUDGET: "Shorten the core note to fit the combined 1500-word budget.",
    ViolationCode.HUB_CHAIN_TOO_DEEP: "Use a hub chain of at most three hubs.",
    ViolationCode.HUB_CHAIN_CYCLE: "Select a hub outside this chain.",
    ViolationCode.SUPERSEDED_BY_CYCLE: "Select a successor outside this chain.",
    ViolationCode.SECRET_DETECTED: "Remove the secret or replace it with a reference to your secret manager.",
    ViolationCode.UNKNOWN_FIELD: "Use only the documented frontmatter fields.",
    ViolationCode.INVALID_FIELD: "Check the field type, format and permitted values in the guide.",
    ViolationCode.POSSIBLE_DUPLICATE: "Search for an existing note covering the same topic.",
}


def projected(report: ValidationReport, severity: Severity) -> tuple[WriteFinding, ...]:
    unique = dict.fromkeys(
        WriteFinding(v.code.value, v.field or "frontmatter", _HINTS.get(v.code, v.message))
        for v in report.violations
        if v.severity is severity
    )
    return tuple(unique)[:12]
