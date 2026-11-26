"""Messages for the owner's terminal when a command or the server cannot start or finish.

Only errors whose text is known to be safe are shown: fixed reasons, configuration and state paths, and note file
paths with a fixed reason (``ascii()``-escaped, never note content). Anything else is named by its class only, since
storage and dependency messages can include note content or invalid values.
"""

from cerebro.adapters.outbound.crypto import InvalidKeyError
from cerebro.adapters.outbound.filesystem import NoteWriteError, VocabularyError
from cerebro.adapters.outbound.sqlite import IndexUnavailableError
from cerebro.application.errors import NoteReadError
from cerebro.bootstrap.container import StateDirectoryError
from cerebro.bootstrap.settings import ConfigurationError

_MAX_LISTED = 20
_SAFE = (
    ConfigurationError,
    StateDirectoryError,
    InvalidKeyError,
    VocabularyError,
    IndexUnavailableError,
    NoteWriteError,
)


def owner_message(error: BaseException) -> str:
    """One line for the owner: the error's own text when it is safe, otherwise its class name."""
    if isinstance(error, NoteReadError) and error.problems:
        shown = [f"{path!a}: {reason}" for path, reason in error.problems[:_MAX_LISTED]]
        if len(error.problems) > _MAX_LISTED:
            shown.append(f"... and {len(error.problems) - _MAX_LISTED} more")
        return "note files need attention: " + "; ".join(shown)
    if isinstance(error, _SAFE):
        return str(error)
    return f"failed ({type(error).__name__}; details are withheld because they may contain note content)"
