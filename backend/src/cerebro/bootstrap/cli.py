"""Console entry point. Configuration and wiring belong to the composition root."""

import os
import sys

from cerebro.adapters.inbound.cli import parse_arguments, run_read_command
from cerebro.adapters.inbound.cli_owner import set_owner_password
from cerebro.adapters.inbound.cli_tokens import run_token_command
from cerebro.application.browser import ProvisionOwnerPassword
from cerebro.application.dto import ReadNoteRequest, SearchNotesRequest
from cerebro.application.errors import (
    NoteNotFound,
    SectionNotFound,
)
from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.container import build_container
from cerebro.bootstrap.diagnostics import owner_message
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
        if options.command == "owner-password":
            return set_owner_password(ProvisionOwnerPassword(owner_credentials(settings)).execute)
        container = build_container(settings)
        ctx = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
        if options.command == "token":
            return run_token_command(
                options.request,
                ctx,
                issue=container.use_cases.issue_agent_token,
                list_tokens=container.use_cases.list_agent_tokens,
                revoke=container.use_cases.revoke_agent_token,
            )
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
    except Exception as error:
        # Storage and dependency exceptions can include invalid values or note content: only safe texts are shown.
        print(f"cerebro: {owner_message(error)}", file=sys.stderr)
        return 1
