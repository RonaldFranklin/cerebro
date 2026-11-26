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
from urllib.parse import urlsplit

import uvicorn

from cerebro.adapters.inbound.browser_api import browser_api
from cerebro.adapters.inbound.browser_assets import browser_assets
from cerebro.adapters.inbound.browser_edit import install_browser_edit
from cerebro.adapters.inbound.browser_history import install_browser_history
from cerebro.adapters.inbound.browser_http import BrowserListener, browser_http
from cerebro.adapters.inbound.browser_reads import BrowserReads, install_browser_reads
from cerebro.adapters.inbound.cli import ArgumentParser
from cerebro.adapters.inbound.input_limits import MAX_INPUT_BYTES
from cerebro.adapters.inbound.mcp_http import http_app
from cerebro.adapters.inbound.mcp_stdio import bridge
from cerebro.adapters.inbound.mcp_tools import MCPTools
from cerebro.application.browser import BrowserBinding
from cerebro.bootstrap.container import build_container
from cerebro.bootstrap.diagnostics import owner_message
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


def _origin(value: str) -> str:
    """Accept canonical browser-serialized HTTPS origins, including bracketed IPv6."""
    if not value.isascii() or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid browser origin")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{ipaddress.IPv6Address(host).compressed}]"
    elif re.fullmatch(r"[0-9]+|0x[0-9a-f]*", host.rsplit(".", 1)[-1]):
        # Browsers parse domains ending in a number as IPv4, including hex/octal and shortened forms.
        # The configured origin must already use the canonical dotted-decimal spelling.
        try:
            host = str(ipaddress.IPv4Address(host))
        except ipaddress.AddressValueError:
            raise ValueError("invalid browser origin") from None
    elif not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
        raise ValueError("invalid browser origin")
    port = parsed.port
    canonical = f"https://{host}" + (f":{port}" if port and port != 443 else "")
    if len(host) > 253 or "%" in host or value != canonical:
        raise ValueError("invalid browser origin")
    return canonical


def _local_origin(port: int) -> str:
    return "https://localhost" if port == 443 else f"https://localhost:{port}"


def _arguments(*, stdio: bool) -> argparse.Namespace:
    parser = ArgumentParser(prog="cerebro-mcp-stdio" if stdio else "cerebro-mcp", allow_abbrev=False)
    parser.add_argument("--host", type=_loopback, default="127.0.0.1", help="numeric loopback address")
    parser.add_argument("--private-port", type=_port, default=8765)
    if not stdio:
        parser.add_argument("--config", type=Path)
        parser.add_argument("--public-port", type=_port, default=8766)
        parser.add_argument("--enable-public", action="store_true")
        parser.add_argument("--private-origin", type=_origin, help="external private HTTPS origin")
        parser.add_argument("--public-origin", type=_origin, help="external public HTTPS origin")
        parser.add_argument("--web-dist", type=Path, help="directory containing the built browser application")
    if sum(len(os.fsencode(item)) + 1 for item in sys.argv[1:]) > MAX_INPUT_BYTES:
        parser.error("argument limit exceeded")
    options = parser.parse_args()
    if not stdio and options.private_port == options.public_port:
        parser.error("ports must differ")
    if not stdio:
        options.private_origin = options.private_origin or _local_origin(options.private_port)
        options.public_origin = options.public_origin or _local_origin(options.public_port)
        if options.enable_public and options.private_origin == options.public_origin:
            parser.error("browser origins must differ")
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
            tools = MCPTools(
                get_overview=cases.get_overview,
                search_notes=cases.search_notes,
                read_note=cases.read_note,
                get_neighbors=cases.get_neighbors,
                agent_write=cases.agent_write,
                get_vocabulary=cases.get_vocabulary,
            )
            auth = container.browser_auth
            reads = BrowserReads(
                get_overview=cases.get_overview,
                search_notes=cases.search_notes,
                read_note=cases.read_note,
                get_neighbors=cases.get_neighbors,
                get_graph=cases.get_graph,
                health_report=cases.health_report,
            )
            browsers = {}
            for port, ceiling in listeners.items():
                private = port == options.private_port
                binding = BrowserBinding(
                    "private" if private else "public", options.private_origin if private else options.public_origin
                )
                api = browser_api(auth, binding, ceiling)
                install_browser_history(api, cases.history)
                install_browser_reads(api, reads)
                install_browser_edit(api, cases.owner_edit_note)
                browsers[port] = BrowserListener(binding, api)
            app = browser_http(
                http_app(tools, cases.authenticate_token, listeners), browsers, browser_assets(options.web_dist)
            )
            config = uvicorn.Config(
                app, proxy_headers=False, access_log=False, log_config=None, ws="none", http="h11", lifespan="on"
            )
            server = uvicorn.Server(config)
            server.run(sockets=sockets)
            return 0 if server.started else 1
    except Exception as error:
        print(f"cerebro: MCP server unavailable: {owner_message(error)}", file=sys.stderr)
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
