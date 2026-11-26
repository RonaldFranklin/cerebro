"""Configured origins must match the host serialization used by browsers."""

import pytest

from cerebro.bootstrap.mcp import _origin


@pytest.mark.parametrize(
    "host",
    [
        "127.1",
        "127.0.1",
        "2130706433",
        "0x7f000001",
        "0x7f.0.0.1",
        "0177.0.0.1",
        "127.0.0.01",
        "127.0.0.0x",
        "0x",
        "256.0.0.1",
        "0xfffffffff",
        "app.1",
        "app.0xff",
        "08",
    ],
)
def test_ipv4_aliases_and_invalid_numeric_hosts_are_refused(host: str) -> None:
    with pytest.raises(ValueError, match="invalid browser origin"):
        _origin(f"https://{host}:8765")


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "0.0.0.0", "255.255.255.255", "[::1]", "localhost", "127.example", "app.0xoffice"]
)
def test_canonical_addresses_and_dns_names_are_accepted(host: str) -> None:
    value = f"https://{host}:8765"
    assert _origin(value) == value
