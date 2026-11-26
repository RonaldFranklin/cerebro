"""Read and confirm the owner's password through a local terminal without echo."""

import getpass
import hmac
import sys
import warnings
from collections.abc import Callable

from cerebro.application.errors import PasswordDurabilityError


def set_owner_password(provision: Callable[[bytes], None]) -> int:
    try:
        with warnings.catch_warnings():
            # getpass normally falls back to visible stdin when it cannot disable echo.
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("New owner password: ").encode("utf-8")
            confirmation = getpass.getpass("Confirm owner password: ").encode("utf-8")
    except getpass.GetPassWarning, EOFError, KeyboardInterrupt, UnicodeError:
        print("cerebro: a private interactive terminal is required", file=sys.stderr)
        return 1
    if not 1 <= len(password) <= 1024 or not hmac.compare_digest(password, confirmation):
        print("cerebro: invalid password or confirmation", file=sys.stderr)
        return 1
    try:
        provision(password)
    except PasswordDurabilityError as error:
        print(f"cerebro: {error}", file=sys.stderr)
        return 1
    print("Owner password updated.")
    return 0
