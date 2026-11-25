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
