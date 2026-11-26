"""Command-line parsing with bounded inputs and value-free error messages."""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Never

from cerebro.adapters.inbound.cli_views import note_view
from cerebro.adapters.inbound.input_limits import MAX_FIELD_BYTES, MAX_INPUT_BYTES, MAX_QUERY_BYTES, bounded_text
from cerebro.adapters.inbound.read_views import health_view, search_view
from cerebro.application.dto import (
    AmendProposalRequest,
    HealthReportRequest,
    HealthReportResult,
    IssueAgentTokenRequest,
    ListAgentTokensRequest,
    ListProposalsRequest,
    NoteView,
    ProposalIdRequest,
    ReadNoteRequest,
    RebuildIndexRequest,
    RebuildIndexResult,
    ReviewAction,
    ReviewProposalRequest,
    RevokeAgentTokenRequest,
    SearchNotesRequest,
    SearchResults,
)
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.access import AccessCeiling, AccessContext, Scope
from cerebro.domain.agent_token import validate_token_id
from cerebro.domain.decision import Decision
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import DomainError
from cerebro.domain.note import NoteId
from cerebro.domain.proposal import validate_proposal_id
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
    read.add_argument("--include-history", action="store_true")
    commands.add_parser("reindex", help="rebuild the search and graph indexes")
    commands.add_parser("health", help="inspect the repository and index state")

    token = commands.add_parser("token", help="manage agent credentials")
    actions = token.add_subparsers(dest="token_action", required=True)
    issue = actions.add_parser("issue", help="issue a token and print its secret once")
    issue.add_argument("agent", type=short_field)
    issue.add_argument("--ceiling", type=short_field, default="public", help="public (default), internal or restricted")
    issue.add_argument("--scope", type=short_field, action="append", help="read (default) or propose; repeatable")
    issue.add_argument("--expires-at", type=short_field, help="ISO 8601 timestamp with timezone")
    actions.add_parser("list", help="list credential metadata")
    revoke = actions.add_parser("revoke", help="revoke a token by its id")
    revoke.add_argument("token_id", type=short_field)

    inbox = commands.add_parser("inbox", help="review queued proposals")
    verbs = inbox.add_subparsers(dest="inbox_action", required=True)
    listing = verbs.add_parser("list", help="list metadata or preview a reviewable proposal")
    listing.add_argument("--id", type=short_field)
    amend = verbs.add_parser("amend", help="replace the candidate body with UTF-8 text from stdin")
    amend.add_argument("id", type=short_field)
    amend.add_argument("--revision", required=True, type=int)
    approve = verbs.add_parser("approve", help="approve the previewed revision and apply it")
    approve.add_argument("id", type=short_field)
    mode = approve.add_mutually_exclusive_group(required=True)
    mode.add_argument("--revision", type=int, help="proposal revision from the preview")
    mode.add_argument("--resume", action="store_true", help="retry application of an already approved proposal")
    decision = approve.add_mutually_exclusive_group()
    decision.add_argument("--create", type=short_field, metavar="NOTE_ID", help="override the create target")
    decision.add_argument("--merge-into", type=short_field, metavar="NOTE_ID", help="override the merge decision")
    reject = verbs.add_parser("reject", help="reject the previewed revision")
    reject.add_argument("id", type=short_field)
    reject.add_argument("--revision", required=True, type=int)
    reject.add_argument("--reason", required=True, type=lambda text: bounded_text(text, 4096))

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
            case "inbox":
                pid = validate_proposal_id(options.id) if options.id is not None else None
                if options.inbox_action == "list":
                    options.request = ListProposalsRequest(proposal_id=pid)
                elif options.inbox_action == "amend":
                    raw_body = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
                    if len(raw_body) > MAX_INPUT_BYTES:
                        parser.error("body is too large")
                    options.request = AmendProposalRequest(
                        proposal_id=options.id, expected_revision=options.revision, body=raw_body.decode("utf-8")
                    )
                elif options.inbox_action == "reject":
                    reason = Decision.reject(options.reason).reason
                    options.request = ReviewProposalRequest(
                        proposal_id=options.id,
                        action=ReviewAction.REJECT,
                        reason=reason,
                        expected_revision=options.revision,
                    )
                elif options.resume:
                    if options.create is not None or options.merge_into is not None:
                        parser.error("cannot change a decision during a retry")
                    options.request = ProposalIdRequest(proposal_id=options.id)
                else:
                    override = None
                    if options.create is not None:
                        override = Decision.create(NoteId(options.create))
                    elif options.merge_into is not None:
                        override = Decision.merge_into(NoteId(options.merge_into))
                    options.request = ReviewProposalRequest(
                        proposal_id=options.id,
                        action=ReviewAction.APPROVE,
                        override=override,
                        expected_revision=options.revision,
                    )
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
