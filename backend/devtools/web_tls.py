"""Disposable localhost certificates and a TLS terminator for browser fixtures."""

import asyncio
import base64
import hashlib
import os
import ssl
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass(frozen=True)
class Certificate:
    server: ssl.SSLContext
    client: ssl.SSLContext
    fingerprint: str
    public_key_pin: str


def certificate(directory: Path) -> Certificate:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = directory / "fixture.pem"
    fd = os.open(pem, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(cert.public_bytes(serialization.Encoding.PEM))
        stream.write(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(pem)
    client = ssl.create_default_context(cadata=cert.public_bytes(serialization.Encoding.PEM).decode("ascii"))
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    pin = base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")
    return Certificate(server, client, cert.fingerprint(hashes.SHA256()).hex(":"), pin)


async def _copy(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
    while block := await source.read(65536):
        destination.write(block)
        await destination.drain()
    if destination.can_write_eof():
        destination.write_eof()
    else:
        destination.close()


@contextmanager
def tls_relay(backend_port: int, context: ssl.SSLContext) -> Iterator[int]:
    """Forward the original HTTP bytes so duplicate headers, framing and methods reach the service unchanged."""
    ready: Future[tuple[int, Callable[[], None]]] = Future()

    async def forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer: asyncio.StreamWriter | None = None
        try:
            backend, peer = await asyncio.open_connection("127.0.0.1", backend_port)
            async with asyncio.TaskGroup() as group:
                group.create_task(_copy(reader, peer))
                group.create_task(_copy(backend, writer))
        except* OSError:
            # A browser can cancel an in-flight request by closing the connection.
            pass
        finally:
            for stream in (peer, writer):
                if stream is not None:
                    stream.close()
                    with suppress(OSError):
                        await stream.wait_closed()

    async def serve() -> None:
        loop = asyncio.get_running_loop()
        stopped = asyncio.Event()
        async with await asyncio.start_server(forward, "127.0.0.1", 0, ssl=context, ssl_handshake_timeout=5) as server:
            port = server.sockets[0].getsockname()[1]

            def stop() -> None:
                loop.call_soon_threadsafe(stopped.set)

            ready.set_result((port, stop))
            await stopped.wait()
            server.close()
            server.abort_clients()

    def run() -> None:
        try:
            asyncio.run(serve())
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:
                raise

    thread = threading.Thread(target=run)
    thread.start()
    port, stop = ready.result(timeout=10)
    try:
        yield port
    finally:
        stop()
        thread.join()
