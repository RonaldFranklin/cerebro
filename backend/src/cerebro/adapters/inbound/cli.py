"""Command-line parsing with bounded inputs and value-free error messages."""

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Never

MAX_INPUT_BYTES = 65536


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # argparse may interpolate arguments, including note text, into diagnostics.
        self.exit(2, "cerebro: invalid arguments (see --help)\n")


def parse_arguments(args: Sequence[str]) -> argparse.Namespace:
    parser = ArgumentParser(prog="cerebro", description="Manage the local Cérebro instance.", allow_abbrev=False)
    parser.add_argument("--config", type=Path, help="read instance settings from a UTF-8 TOML file")
    parser.add_argument("--check-config", action="store_true", help="validate settings and exit")

    size = 0
    for arg in args:
        try:
            size += len(arg.encode("utf-8")) + 1
        except UnicodeError:
            parser.error("invalid encoding")
        if size > MAX_INPUT_BYTES:
            parser.error("arguments exceed byte limit")
    options = parser.parse_args(args)
    if not options.check_config:
        parser.error("missing command")
    return options
