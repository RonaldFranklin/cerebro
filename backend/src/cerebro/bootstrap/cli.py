"""Console entry point. Configuration and wiring belong to the composition root."""

import os
import sys

from cerebro.adapters.inbound.cli import parse_arguments, run_read_command
from cerebro.application.dto import ReadNoteRequest, SearchNotesRequest
from cerebro.application.errors import NoteNotFound, SectionNotFound
from cerebro.bootstrap.container import build_container
from cerebro.bootstrap.init_content import InitializationError, initialize_content
from cerebro.bootstrap.settings import ConfigurationError, load_settings
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.enums import Sensitivity


def main() -> int:
    options = parse_arguments(sys.argv[1:])
    try:
        settings = load_settings(options.config, os.environ)
        if options.check_config:
            return 0
        if options.command == "init-content":
            initialize_content(settings)
            return 0
        container = build_container(settings)
        ctx = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
        if isinstance(options.request, (SearchNotesRequest, ReadNoteRequest)):
            container.start()
        return run_read_command(
            options.request,
            ctx,
            search=container.use_cases.search_notes,
            read=container.use_cases.read_note,
            reindex=container.use_cases.rebuild_index,
            health=container.use_cases.health_report,
        )
    except (ConfigurationError, InitializationError) as error:
        print(f"cerebro: {error}", file=sys.stderr)
        return 2
    except (NoteNotFound, SectionNotFound) as error:
        print(f"cerebro: {error}", file=sys.stderr)
        return 1
    except Exception:
        # Storage and dependency exceptions can include file paths, invalid values or note content.
        print("cerebro: operation failed", file=sys.stderr)
        return 1
