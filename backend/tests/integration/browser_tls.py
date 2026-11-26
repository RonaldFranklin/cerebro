"""A localhost-only HTTPS terminator for browser acceptance against the installed service."""

import http.client
import os
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def certificate(directory: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
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
        .not_valid_after(now + timedelta(hours=1))
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
    return server, client


@contextmanager
def tls_relay(backend_port: int, context: ssl.SSLContext) -> Iterator[int]:
    class Relay(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def forward(self) -> None:
            upstream = http.client.HTTPConnection("127.0.0.1", backend_port, timeout=5)
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                upstream.request(self.command, self.path, body=body, headers=dict(self.headers.items()))
                response = upstream.getresponse()
                self.send_response_only(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in {"transfer-encoding", "connection"}:
                        self.send_header(name, value)
                self.end_headers()
                self.wfile.write(response.read())
            finally:
                upstream.close()

        do_GET = forward  # noqa: N815 (standard library hook)
        do_POST = forward  # noqa: N815

    with ThreadingHTTPServer(("127.0.0.1", 0), Relay) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()
