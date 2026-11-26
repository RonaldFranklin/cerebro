"""Only recognized browser routes and public, fingerprinted build files are served."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from cerebro.adapters.inbound.browser_assets import browser_assets

NONCE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"


def client(dist: Path | None, nonce: str = NONCE) -> TestClient:
    app = browser_assets(dist)

    async def mounted(scope: Scope, receive: Receive, send: Send) -> None:
        scope["cerebro.style_nonce"] = nonce
        await app(scope, receive, send)

    return TestClient(mounted)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    directory = tmp_path / "dist"
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text("<html><head></head><body>Sample</body></html>")
    return directory


@pytest.mark.parametrize(
    "route", ["/", "/login", "/search", "/graph", "/changes", "/health", "/notes/n-1", "/editor/n-1"]
)
def test_known_navigation_gets_nonce_and_no_cache(dist: Path, route: str) -> None:
    browser = client(dist)
    response = browser.get(route + "?q=example")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert f'<meta name="cerebro-style-nonce" content="{NONCE}">' in response.text
    head = browser.head(route)
    assert head.content == b""
    assert head.headers["content-length"] == str(len(response.content))


@pytest.mark.parametrize(
    "route",
    [
        "/api/unknown",
        "/mcp",
        "/unknown",
        "/inbox",
        "/assets/plain.js",
        "/assets/a-12345678.map",
        "/assets/a-12345678.html",
        "/index.html",
        "/notes/not_valid",
        "/notes/id/extra",
        "/assets/nested/app-12345678.js",
        "/%67raph",
    ],
)
def test_unknown_paths_are_never_navigation(dist: Path, route: str) -> None:
    assert client(dist).get(route).status_code == 404


def test_hashed_asset_headers_and_methods(dist: Path) -> None:
    asset = "app-Cn3RW20_.js"
    (dist / "assets" / asset).write_bytes(b"export default 42")
    browser = client(dist)
    response = browser.get("/assets/" + asset)
    assert response.content == b"export default 42"
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert browser.head("/assets/" + asset).headers["content-length"] == str(len(response.content))
    assert browser.post("/assets/" + asset).status_code == 405
    assert browser.post("/login").status_code == 405


@pytest.mark.parametrize(
    "kind", ["missing", "dist-link", "assets-link", "asset-link", "index-link", "hardlink", "fifo"]
)
def test_build_file_boundaries(dist: Path, kind: str) -> None:
    import os

    secret = dist.parent / "outside"
    secret.write_text("nonpublic fixture")
    asset = dist / "assets" / "test-12345678.js"
    location = dist
    route = "/assets/" + asset.name
    if kind == "missing":
        assert client(None).get("/").status_code == 404
        location = dist.parent / "absent"
    elif kind == "dist-link":
        location = dist.parent / "alias"
        location.symlink_to(dist)
        route = "/"
    elif kind == "assets-link":
        asset.parent.rename(dist.parent / "externals")
        (dist.parent / "externals" / asset.name).write_bytes(secret.read_bytes())
        asset.parent.symlink_to(dist.parent / "externals")
    elif kind == "asset-link":
        asset.symlink_to(secret)
    elif kind == "index-link":
        (dist / "index.html").unlink()
        (dist / "index.html").symlink_to(secret)
        route = "/"
    elif kind == "hardlink":
        asset.hardlink_to(secret)
    else:
        os.mkfifo(asset)
    response = client(location).get(route)
    assert response.status_code == 404
    assert "nonpublic" not in response.text


def test_index_rejects_unusable_nonce_or_build_template(dist: Path) -> None:
    response = client(dist, '" invalid').get("/")
    assert response.status_code == 503
    assert not response.content
    (dist / "index.html").write_text("<html>missing head</html>")
    assert client(dist).get("/").status_code == 503


def test_asset_encoded_separator_cannot_escape_build(dist: Path) -> None:
    browser = client(dist)
    for route in ("/assets/%2e%2e%2findex.html", "/assets%2Fmain-12345678.js"):
        assert browser.get(route).status_code == 404


@pytest.mark.parametrize("asset", [False, True])
def test_directory_in_place_of_file_returns_404_and_releases_descriptors(dist: Path, asset: bool) -> None:
    descriptors = Path("/proc/self/fd")
    if asset:
        file = dist / "assets/file-12345678.js"
        route = "/assets/file-12345678.js"
    else:
        file = dist / "index.html"
        file.unlink()
        route = "/"
    file.mkdir()
    browser = client(dist)
    before = len(list(descriptors.iterdir()))
    for _ in range(10):
        assert browser.get(route).status_code == 404
    assert len(list(descriptors.iterdir())) == before
