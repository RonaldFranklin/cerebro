"""Use the same disposable loopback TLS terminator as the owner's browser demo."""

import ssl
from pathlib import Path

from devtools import web_tls
from devtools.web_tls import tls_relay as tls_relay


def certificate(directory: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    cert = web_tls.certificate(directory)
    return cert.server, cert.client
