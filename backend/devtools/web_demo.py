"""Run the installed browser service on disposable synthetic content through loopback HTTPS."""

import argparse
import http.client
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.integration.content_kit import ISLAND_ID

from cerebro.adapters.inbound.cli_owner import set_owner_password
from cerebro.application.browser import ProvisionOwnerPassword
from cerebro.application.dto import SubmitProposalRequest
from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.container import build_container
from cerebro.bootstrap.settings import load_settings
from cerebro.domain.access import AccessCeiling, AccessContext, Agent, Scope
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity
from cerebro.domain.proposal import ProposalKind, ProposedContent
from devtools.mcp_sample import create_sample
from devtools.web_tls import certificate, tls_relay


def _populate(config: Path, *, automation: bool) -> bool:
    settings = load_settings(config, {})
    container = build_container(settings)
    container.start()
    provision = ProvisionOwnerPassword(owner_credentials(settings))
    if automation:
        line = sys.stdin.buffer.readline(1026)
        if not line.endswith(b"\n"):
            raise ValueError("Invalid fixture credential")
        provision.execute(line[:-1])
    elif set_owner_password(provision.execute):
        return False
    note = container.notes.get(ISLAND_ID)
    assert note
    submitted = container.use_cases.submit_proposal.execute(
        SubmitProposalRequest(
            kind=ProposalKind.UPDATE,
            target=note.id,
            base_hash=content_hash(note),
            content=ProposedContent(body="Updated guidance for the synthetic infrastructure island.\n"),
        ),
        AccessContext(
            principal=Agent("fixture-agent"),
            scopes=frozenset({Scope.PROPOSE}),
            ceiling=AccessCeiling(Sensitivity.RESTRICTED),
            token_id="fixture-token",
        ),
    )
    if not submitted.valid:
        raise RuntimeError("Invalid fixture proposal")
    return True


def _ready(process: subprocess.Popen[bytes], port: int, external_port: int) -> None:
    deadline = time.monotonic() + 15
    while process.poll() is None and time.monotonic() < deadline:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
        try:
            conn.request("GET", "/api/session", headers={"Host": f"localhost:{external_port}"})
            if conn.getresponse().status == 401 and process.poll() is None:
                return
        except OSError, http.client.HTTPException:
            pass
        finally:
            conn.close()
        time.sleep(0.05)
    raise RuntimeError("Fixture service did not start")


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True, help="built web distribution")
    parser.add_argument(
        "--automation", action="store_true", help="take a disposable password line on stdin; stop at EOF"
    )
    args = parser.parse_args()
    dist: Path = args.dist.resolve()
    if not (dist / "index.html").is_file():
        parser.error("build the web distribution first")

    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        with TemporaryDirectory(prefix="cerebro-web-", dir="/tmp") as temp, ExitStack() as stack:
            directory = Path(temp)
            config = create_sample(directory / "instance")
            if not _populate(config, automation=args.automation):
                return 1
            trust = certificate(directory)
            # Keep both selected addresses reserved until the installed process is ready to bind.
            with socket.socket() as first, socket.socket() as second:
                for sock in (first, second):
                    sock.bind(("127.0.0.1", 0))
                private, public = first.getsockname()[1], second.getsockname()[1]
                browser_private = stack.enter_context(tls_relay(private, trust.server))
                browser_public = stack.enter_context(tls_relay(public, trust.server))
            private_origin, public_origin = (f"https://localhost:{port}" for port in (browser_private, browser_public))
            environment = {k: v for k, v in os.environ.items() if not k.startswith(("CEREBRO_", "GIT_"))}
            command = [
                str(Path(sys.executable).with_name("cerebro-mcp")),
                "--config",
                str(config),
                "--private-port",
                str(private),
                "--public-port",
                str(public),
                "--enable-public",
                "--private-origin",
                private_origin,
                "--public-origin",
                public_origin,
                "--web-dist",
                str(dist),
            ]
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment
            )
            stack.callback(_stop, process)
            _ready(process, private, browser_private)
            _ready(process, public, browser_public)
            if args.automation:
                print(
                    json.dumps(
                        {
                            "private_origin": private_origin,
                            "public_origin": public_origin,
                            "certificate_public_key_pin": trust.public_key_pin,
                        }
                    ),
                    flush=True,
                )
            else:
                print(f"Private browser: {private_origin}\nPublic listener fixture: {public_origin}")
                print(
                    f"Local certificate SHA-256: {trust.fingerprint}\nPress Ctrl+C to stop and remove the fixture.",
                    flush=True,
                )
            while process.poll() is None:
                if args.automation:
                    if select.select([sys.stdin], [], [], 0.25)[0]:
                        return 0
                else:
                    time.sleep(0.25)
            raise RuntimeError("Fixture service exited")
    except KeyboardInterrupt:
        return 0
    except OSError, RuntimeError, ValueError:
        print("The browser fixture could not run.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
