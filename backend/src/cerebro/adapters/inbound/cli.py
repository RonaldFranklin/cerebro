"""Command-line parsing with bounded inputs and value-free error messages."""

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Never

from cerebro.adapters.inbound.cli_views import note_view
from cerebro.adapters.inbound.input_limits import MAX_FIELD_BYTES, MAX_INPUT_BYTES, MAX_QUERY_BYTES, bounded_text
from cerebro.adapters.inbound.read_views import health_view, search_view
from cerebro.application.dto import (
    HealthReportRequest,
    HealthReportResult,
    IssueAgentTokenRequest,
    ListAgentTokensRequest,
    NoteView,
    ReadNoteRequest,
    RebuildIndexRequest,
    RebuildIndexResult,
    RevokeAgentTokenRequest,
    SearchNotesRequest,
    SearchResults,
)
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.access import AccessCeiling, AccessContext, Scope
from cerebro.domain.agent_token import validate_token_id
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import DomainError
from cerebro.domain.note import NoteId
from cerebro.domain.vocabulary import Area, NoteType


def short_field(text: str) -> str:
    return bounded_text(text, MAX_FIELD_BYTES)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # argparse may interpolate arguments, including note text, into diagnostics.
        self.exit(2, "cerebro: invalid arguments (see --help)\n")


def parse_arguments(args: Sequence[str]) -> argparse.Namespace:
    parser = ArgumentParser(prog="cerebro", description="Manage the local Cérebro instance.", allow_abbrev=False)
    parser.add_argument("--config", type=Path, help="read instance settings from a UTF-8 TOML file")
    parser.add_argument("--check-config", action="store_true", help="validate settings and exit")

    commands = parser.add_subparsers(dest="command")
    commands.add_parser("init-content", help="initialize the private content repository")
    commands.add_parser("owner-password", help="set or rotate the local owner password interactively")

    search = commands.add_parser("search", help="find note summaries")
    search.add_argument("query", nargs="?", default="", type=lambda text: bounded_text(text, MAX_QUERY_BYTES))
    for name in ("area", "type", "priority", "status"):
        search.add_argument("--" + name, type=short_field, action="append", default=[])
    search.add_argument("--include-history", action="store_true")
    search.add_argument("--limit", type=int, default=20)
    read = commands.add_parser("read", help="read a note or one of its sections")
    read.add_argument("id", type=short_field)
    read.add_argument("--section", type=short_field)
    read.add_argument("--include-history", action="store_true", help="also list superseded and archived hub members")
    commands.add_parser("reindex", help="rebuild the search and graph indexes")
    commands.add_parser("health", help="inspect the repository and index state")

    token = commands.add_parser("token", help="manage agent credentials")
    actions = token.add_subparsers(dest="token_action", required=True)
    issue = actions.add_parser("issue", help="issue a token and print its secret once")
    issue.add_argument("agent", type=short_field)
    issue.add_argument("--ceiling", type=short_field, default="public", help="public (default), internal or restricted")
    issue.add_argument(
        "--scope",
        type=short_field,
        action="append",
        help="read (default), write, or core (core and P0 notes; requires write); repeat to combine",
    )
    issue.add_argument("--expires-at", type=short_field, help="ISO 8601 timestamp with timezone")
    actions.add_parser("list", help="list credential metadata")
    revoke = actions.add_parser("revoke", help="revoke a token by its id")
    revoke.add_argument("token_id", type=short_field)

    size = 0
    for arg in args:
        try:
            size += len(arg.encode("utf-8")) + 1
        except UnicodeError:
            parser.error("invalid encoding")
        if size > MAX_INPUT_BYTES:
            parser.error("arguments exceed byte limit")
    options = parser.parse_args(args)
    if options.check_config == (options.command is not None):
        parser.error("missing command")
    try:
        match options.command:
            case "token":
                match options.token_action:
                    case "issue":
                        options.request = IssueAgentTokenRequest(
                            agent=options.agent,
                            ceiling=AccessCeiling(Sensitivity.parse(options.ceiling)),
                            scopes=frozenset(Scope.parse(s) for s in options.scope or ["read"]),
                            expires_at=datetime.fromisoformat(options.expires_at)
                            if options.expires_at is not None
                            else None,
                        )
                    case "list":
                        options.request = ListAgentTokensRequest()
                    case "revoke":
                        options.request = RevokeAgentTokenRequest(token_id=validate_token_id(options.token_id))
            case "search":
                options.request = SearchNotesRequest(
                    text=options.query,
                    areas=frozenset(Area(value) for value in options.area),
                    types=frozenset(NoteType(value) for value in options.type),
                    priorities=frozenset(Priority.parse(value) for value in options.priority),
                    statuses=frozenset(Status.parse(value) for value in options.status),
                    limit=options.limit,
                    include_history=options.include_history,
                )
            case "read":
                options.request = ReadNoteRequest(
                    note_id=NoteId(options.id), section=options.section, include_history=options.include_history
                )
            case "reindex":
                options.request = RebuildIndexRequest()
            case "health":
                options.request = HealthReportRequest()
    except DomainError, ValueError:
        parser.error("invalid request")
    return options


def run_read_command(
    request: SearchNotesRequest | ReadNoteRequest | RebuildIndexRequest | HealthReportRequest,
    ctx: AccessContext,
    *,
    search: ContextUseCase[SearchNotesRequest, SearchResults],
    read: ContextUseCase[ReadNoteRequest, NoteView],
    reindex: ContextUseCase[RebuildIndexRequest, RebuildIndexResult],
    health: ContextUseCase[HealthReportRequest, HealthReportResult],
) -> int:
    result: object
    exit_code = 0
    match request:
        case SearchNotesRequest():
            result = search_view(search.execute(request, ctx))
        case ReadNoteRequest():
            result = note_view(read.execute(request, ctx))
        case RebuildIndexRequest():
            result = {"notes_indexed": reindex.execute(request, ctx).notes_indexed}
        case HealthReportRequest():
            report = health.execute(request, ctx)
            result = health_view(report)
            exit_code = 0 if report.ok else 1
    print(json.dumps(result, indent=2))
    return exit_code
