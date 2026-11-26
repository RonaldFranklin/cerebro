"""Errors of the filesystem adapter. Messages name relative file paths and fixed reasons, never note content."""

from collections.abc import Sequence

from cerebro.application.errors import NoteReadError

_MAX_LISTED = 20


class NoteFileError(NoteReadError):
    """A file of the content repository cannot be used as a note.

    ``path`` is relative to the content root (rendered with ``ascii()`` so a hostile file name cannot inject
    control characters into logs); ``reason`` is a fixed text. ``problems`` lists every ``(path, reason)`` found
    when several files are wrong at once (listings report all of them, they never skip a file silently).
    """

    def __init__(
        self, path: str, reason: str, *, problems: Sequence[tuple[str, str]] = (), stable: bool = False
    ) -> None:
        self.path = path
        self.reason = reason
        self.stable = stable
        """For one file: the reason follows from the bytes or the kind of the file alone (``FileProblem.stable``
        once the file is known to equal ``HEAD``)."""
        listed = tuple(problems) or ((path, reason),)
        shown = [f"{p!a}: {r}" for p, r in listed[:_MAX_LISTED]]
        more = len(listed) - len(shown)
        if more > 0:
            shown.append(f"... and {more} more")
        super().__init__("; ".join(shown), problems=listed)

    @classmethod
    def from_problems(cls, problems: Sequence[tuple[str, str]]) -> NoteFileError:
        first_path, first_reason = problems[0]
        return cls(first_path, first_reason, problems=problems)


class NoteWriteError(Exception):
    """A write could not be completed. Nothing was kept: the tree and ``HEAD`` are as before the call."""


class WriterBusyError(NoteWriteError):
    """Another writer holds the content repository lock and did not release it in time."""


class VocabularyError(Exception):
    """``schema/areas.yaml`` or ``schema/types.yaml`` is missing, unsafe or malformed."""
