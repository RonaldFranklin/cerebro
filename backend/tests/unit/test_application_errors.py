from cerebro.application.errors import (
    AlreadyExists,
    ApplicationError,
    NoteNotFound,
    PermissionDenied,
    ProposalConflict,
    ProposalNotFound,
    TokenNotFound,
)
from cerebro.domain.note import NoteId


def test_errors_carry_their_context_and_share_a_base() -> None:
    errors = [
        NoteNotFound(NoteId("a-note")),
        ProposalNotFound("p1"),
        ProposalConflict("p1"),
        TokenNotFound("t1"),
        AlreadyExists("proposal", "p1"),
        PermissionDenied("propose"),
    ]
    assert all(isinstance(e, ApplicationError) for e in errors)
    assert NoteNotFound(NoteId("a-note")).note_id == NoteId("a-note")
    assert "a-note" in str(NoteNotFound(NoteId("a-note")))
    assert PermissionDenied("propose").action == "propose"
    assert AlreadyExists("proposal", "p1").key == "p1"


def test_note_not_found_message_is_the_same_whatever_the_reason() -> None:
    # The use case raises this for missing and for hidden notes: nothing in it may differ.
    assert str(NoteNotFound(NoteId("x-note"))) == "note not found: x-note"
