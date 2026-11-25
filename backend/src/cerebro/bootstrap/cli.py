"""Console entry point. Configuration and wiring belong to the composition root."""

import os
import sys

from cerebro.adapters.inbound.cli import parse_arguments
from cerebro.bootstrap.init_content import InitializationError, initialize_content
from cerebro.bootstrap.settings import ConfigurationError, load_settings


def main() -> int:
    options = parse_arguments(sys.argv[1:])
    try:
        settings = load_settings(options.config, os.environ)
        if options.command == "init-content":
            initialize_content(settings)
    except (ConfigurationError, InitializationError) as error:
        print(f"cerebro: {error}", file=sys.stderr)
        return 2
    return 0
