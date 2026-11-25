"""The shape of a use case, for code that holds use cases without knowing their classes (composition root, inbound
adapters)."""

from typing import Protocol

from cerebro.domain.access import AccessContext


class ContextUseCase[Request, Result](Protocol):
    """A use case that acts for an authenticated caller."""

    def execute(self, request: Request, ctx: AccessContext) -> Result: ...


class AnonymousUseCase[Request, Result](Protocol):
    """A use case that runs before there is a caller (token authentication)."""

    def execute(self, request: Request) -> Result: ...
