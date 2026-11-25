"""Domain errors.

Constructing a value that breaks an invariant raises; validating content returns a report instead
(see ``cerebro.domain.validation``, Phase 1 step 2).
"""


class DomainError(Exception):
    """Base class for every error raised by the domain."""


class InvalidValueError(DomainError, ValueError):
    """A value object was constructed from an invalid value."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class InvalidTransition(DomainError):  # noqa: N818
    """A proposal lifecycle transition that the lifecycle does not allow (e.g. applying a rejected proposal).

    Domain error: the application layer lets it propagate and adapters map it to their error format
    (plan: the "typed application exception" ``InvalidTransition`` is this class, defined once, here).
    """

    def __init__(self, action: str, current: str) -> None:
        super().__init__(f"cannot {action} a proposal that is {current}")
        self.action = action
        self.current = current


class ContentConflict(DomainError):  # noqa: N818
    """The note changed since the base hash a change was prepared against (optimistic concurrency)."""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__("the note changed since the change was prepared")
        self.expected = expected
        self.actual = actual
