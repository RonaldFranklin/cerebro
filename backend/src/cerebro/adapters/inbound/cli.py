"""Command-line parsing with bounded inputs and value-free error messages."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Never

from cerebro.adapters.inbound.cli_views import health_view, note_view, search_view
from cerebro.application.dto import (
    HealthReportRequest,
    HealthReportResult,
    NoteView,
    ReadNoteRequest,
    RebuildIndexRequest,
    RebuildIndexResult,
    SearchNotesRequest,
    SearchResults,
)
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.access import AccessContext
from cerebro.domain.enums import Priority, Status
from cerebro.domain.errors import DomainError
from cerebro.domain.note import NoteId
from cerebro.domain.vocabulary import Area, NoteType

MAX_INPUT_BYTES = 65536
MAX_QUERY_BYTES = 1024
MAX_FIELD_BYTES = 256


def bounded_text(text: str, maximum: int) -> str:
    if len(text.encode("utf-8")) > maximum:
        raise ValueError("field exceeds byte limit")
    return text


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

    search = commands.add_parser("search", help="find note summaries")
    search.add_argument("query", nargs="?", default="", type=lambda text: bounded_text(text, MAX_QUERY_BYTES))
    for name in ("area", "type", "priority", "status"):
        search.add_argument("--" + name, type=short_field, action="append", default=[])
    search.add_argument("--include-history", action="store_true")
    search.add_argument("--limit", type=int, default=20)
    read = commands.add_parser("read", help="read a note or one of its sections")
    read.add_argument("id", type=short_field)
    read.add_argument("--section", type=short_field)
    read.add_argument("--include-history", action="store_true")
    commands.add_parser("reindex", help="rebuild the search and graph indexes")
    commands.add_parser("health", help="inspect the repository and index state")

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
    except DomainError:
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
