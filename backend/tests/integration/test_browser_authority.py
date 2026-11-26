import asyncio

import httpx2
import pytest
from starlette.responses import Response

from cerebro.adapters.inbound.browser_http import BrowserListener, browser_http
from cerebro.application.browser import BrowserBinding


@pytest.mark.parametrize(
    ("hosts", "origin", "status"),
    [
        (["OWNER.EXAMPLE:443"], "https://owner.example", 200),
        (["owner.example"], None, 200),
        (["owner.example:443", "owner.example"], None, 400),
        (["owner.example:8443"], "https://owner.example", 400),
        (["owner.example"], "https://owner.example:443", 403),
    ],
)
def test_authority_and_api_errors(hosts: list[str], origin: str | None, status: int) -> None:
    api = Response("accepted")
    binding = BrowserBinding("private", "https://owner.example")
    app = browser_http(api, {8765: BrowserListener(binding, api)}, api)

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as client:
            headers = [("Host", host) for host in hosts]
            if origin:
                headers.append(("Origin", origin))
            result = await client.get("http://127.0.0.1:8765/api/session", headers=headers)
            assert result.status_code == status
            if status != 200:
                assert set(result.json()) == {"error"}
                assert result.headers["cache-control"] == "no-store"

    asyncio.run(run())
