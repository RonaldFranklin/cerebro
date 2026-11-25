"""MCP process entry points. Listener sockets and their ceilings are established together."""

import argparse
import ipaddress
import logging
import os
import re
import socket
import sys
from contextlib import ExitStack
from pathlib import Path

import uvicorn

from cerebro.adapters.inbound.cli import ArgumentParser
from cerebro.adapters.inbound.input_limits import MAX_INPUT_BYTES
from cerebro.adapters.inbound.mcp_http import http_app
from cerebro.adapters.inbound.mcp_stdio import bridge
from cerebro.adapters.inbound.mcp_tools import ReadTools
from cerebro.bootstrap.container import build_container
from cerebro.bootstrap.settings import load_settings
from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Sensitivity


def _loopback(value: str) -> str:
    address = ipaddress.ip_address(value)
    if not address.is_loopback or "%" in value:
        raise ValueError("loopback required")
    return str(address)


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return port


def _arguments(*, stdio: bool) -> argparse.Namespace:
    parser = ArgumentParser(prog="cerebro-mcp-stdio" if stdio else "cerebro-mcp", allow_abbrev=False)
    parser.add_argument("--host", type=_loopback, default="127.0.0.1", help="numeric loopback address")
    parser.add_argument("--private-port", type=_port, default=8765)
    if not stdio:
        parser.add_argument("--config", type=Path)
        parser.add_argument("--public-port", type=_port, default=8766)
        parser.add_argument("--enable-public", action="store_true")
    if sum(len(os.fsencode(item)) + 1 for item in sys.argv[1:]) > MAX_INPUT_BYTES:
        parser.error("argument limit exceeded")
    options = parser.parse_args()
    if not stdio and options.private_port == options.public_port:
        parser.error("ports must differ")
    return options


def serve() -> int:
    options = _arguments(stdio=False)
    # Dependencies and HTTP parsers can log untrusted bytes on malformed requests. The server emits no request logs.
    logging.disable(logging.CRITICAL)
    listeners = {options.private_port: AccessCeiling(Sensitivity.RESTRICTED)}
    if options.enable_public:
        listeners[options.public_port] = AccessCeiling(Sensitivity.INTERNAL)
    try:
        with ExitStack() as stack:
            sockets = [
                stack.enter_context(
                    socket.create_server(
                        (options.host, port),
                        family=socket.AF_INET6 if ":" in options.host else socket.AF_INET,
                    )
                )
                for port in listeners
            ]
            container = build_container(load_settings(options.config, os.environ))
            container.start()
            cases = container.use_cases
            tools = ReadTools(
                get_overview=cases.get_overview,
                search_notes=cases.search_notes,
                read_note=cases.read_note,
                get_neighbors=cases.get_neighbors,
            )
            app = http_app(tools, cases.authenticate_token, listeners)
            config = uvicorn.Config(
                app, proxy_headers=False, access_log=False, log_config=None, ws="none", http="h11", lifespan="on"
            )
            server = uvicorn.Server(config)
            server.run(sockets=sockets)
            return 0 if server.started else 1
    except Exception:
        print("cerebro: MCP server unavailable", file=sys.stderr)
        return 1


def stdio() -> int:
    options = _arguments(stdio=True)
    secret = os.environ.get("CEREBRO_MCP_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_~-]{1,512}", secret):
        print("cerebro: missing or invalid MCP credential", file=sys.stderr)
        return 2
    try:
        return bridge(sys.stdin.buffer, sys.stdout.buffer, host=options.host, port=options.private_port, secret=secret)
    except Exception:
        print("cerebro: MCP bridge unavailable", file=sys.stderr)
        return 1
