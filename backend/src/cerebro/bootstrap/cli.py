"""Console entry point. Configuration and wiring belong to the composition root."""

import os
import sys

from cerebro.adapters.inbound.cli import parse_arguments
from cerebro.bootstrap.settings import ConfigurationError, load_settings


def main() -> int:
    options = parse_arguments(sys.argv[1:])
    try:
        load_settings(options.config, os.environ)
    except ConfigurationError as error:
        print(f"cerebro: {error}", file=sys.stderr)
        return 2
    return 0
