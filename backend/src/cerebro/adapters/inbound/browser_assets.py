"""Serve the optional local web build on a bounded set of browser routes."""

import asyncio
import os
import re
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from cerebro.domain.note import MAX_NOTE_ID_LENGTH
from cerebro.domain.slug import is_slug

_NAVIGATION = frozenset({"/", "/login", "/search", "/graph", "/inbox", "/health"})
_ASSET = re.compile(r"/assets/([A-Za-z0-9_][A-Za-z0-9_.-]*-[A-Za-z0-9_-]{8,64}\.([a-z0-9]+))", re.ASCII)
_TYPES = {
    "js": "text/javascript",
    "css": "text/css",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "png": "image/png",
    "webp": "image/webp",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}
_INDEX_LIMIT = 1024 * 1024
_DIRECTORY = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY


def _navigation(path: str) -> bool:
    if path in _NAVIGATION:
        return True
    prefix, _, note_id = path.rpartition("/")
    return prefix in ("/notes", "/editor") and len(note_id) <= MAX_NOTE_ID_LENGTH and is_slug(note_id)


@contextmanager
def _build_file(root: Path, name: str, *, asset: bool) -> Iterator[BinaryIO]:
    # Anchor every open to a checked directory descriptor. Never resolve through a symlink,
    # including a replacement of an ancestor while a request is in flight.
    with ExitStack() as stack:
        directory = os.open(root.anchor, _DIRECTORY)
        stack.callback(os.close, directory)
        components = root.parts[1:] + (("assets",) if asset else ())
        for component in components:
            directory = os.open(component, _DIRECTORY, dir_fd=directory)
            stack.callback(os.close, directory)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stream = stack.enter_context(os.fdopen(fd, "rb"))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise FileNotFoundError
        yield stream


def _chunks(stream: BinaryIO) -> Iterator[bytes]:
    while chunk := stream.read(65536):
        yield chunk


def _index(stream: BinaryIO, nonce: object, head: bool) -> Response:
    if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9+/_-]{1,128}={0,2}", nonce):
        return Response(status_code=503)
    raw = stream.read(_INDEX_LIMIT + 1)
    if len(raw) > _INDEX_LIMIT or b"</head>" not in raw:
        return Response(status_code=503)
    meta = f'<meta name="cerebro-style-nonce" content="{nonce}">'.encode("ascii")
    body = raw.replace(b"</head>", meta + b"</head>", 1)
    return Response(
        b"" if head else body,
        media_type="text/html",
        headers={"Cache-Control": "no-store", "Content-Length": str(len(body))},
    )


def browser_assets(dist: Path | None) -> ASGIApp:
    root = Path(os.path.abspath(dist)) if dist is not None else None

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"]
        raw = scope.get("raw_path")
        canonical = path.isascii() and (raw is None or raw == path.encode("ascii"))
        asset = _ASSET.fullmatch(path) if canonical and len(path) <= 255 else None
        mime = _TYPES.get(asset[2]) if asset else None
        if root is None or not canonical or not (mime or _navigation(path)):
            await Response(status_code=404)(scope, receive, send)
            return
        if scope["method"] not in ("GET", "HEAD"):
            await Response(status_code=405, headers={"Allow": "GET, HEAD"})(scope, receive, send)
            return
        head = scope["method"] == "HEAD"
        with ExitStack() as stack:
            try:
                stream = stack.enter_context(_build_file(root, asset[1] if asset else "index.html", asset=bool(asset)))
            except OSError:
                await Response(status_code=404)(scope, receive, send)
                return
            if mime:
                headers = {
                    "Cache-Control": "public, max-age=31536000, immutable",
                    "Content-Length": str(os.fstat(stream.fileno()).st_size),
                }
                response = (
                    Response(media_type=mime, headers=headers)
                    if head
                    else StreamingResponse(_chunks(stream), media_type=mime, headers=headers)
                )
            else:
                response = await asyncio.to_thread(_index, stream, scope.get("cerebro.style_nonce"), head)
            await response(scope, receive, send)

    return app
