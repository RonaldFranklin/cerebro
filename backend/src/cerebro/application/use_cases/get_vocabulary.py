"""``GetVocabulary``: the areas and note types a write may use."""

from cerebro.application.access import require_scope
from cerebro.application.dto import GetVocabularyRequest
from cerebro.domain.access import AccessContext, Scope
from cerebro.domain.vocabulary import Vocabulary


class GetVocabulary:
    """Serves the same vocabulary writes are validated against: schema names only, nothing derived from notes."""

    def __init__(self, vocabulary: Vocabulary) -> None:
        self._vocabulary = vocabulary

    def execute(self, request: GetVocabularyRequest, ctx: AccessContext) -> Vocabulary:
        require_scope(ctx, Scope.WRITE)
        return self._vocabulary
