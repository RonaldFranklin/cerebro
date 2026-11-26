"""Fixed failure messages and the operator log line shared by the inbound adapters.

Exception messages can carry note content, file paths or private values, so nothing here ever uses one. Agents get
fixed texts that tell a failure inside a running Cérebro apart from an unreachable one ("Cérebro offline", sent only
by the stdio bridge when the server cannot be reached); the operator's log gets the route and the exception class.
"""

import sys

NOTE_FILES_FAILURE = (
    "Cérebro local failure: some note files need the owner's attention (the owner should open Health)."
    " The server is reachable; do not retry this request now."
)
"""A ``NoteReadError``: the same text whatever note or file is concerned, so it says nothing about hidden notes."""

LOCAL_FAILURE = (
    "Cérebro local failure: the server is reachable but could not complete this request."
    " Try again later; if it persists, tell the owner."
)
"""Any other unexpected error inside a running Cérebro."""


def log_failure(route: str, error: BaseException) -> None:
    """One line on standard error for the operator: ``route`` (a fixed name or a route template, never a raw
    path) and the exception class, never its message."""
    print(f"cerebro: {route} failed: {type(error).__name__}", file=sys.stderr, flush=True)
