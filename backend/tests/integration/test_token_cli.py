"""Exercise the installed owner CLI with a real token store."""

import hashlib
import json
from pathlib import Path

import pytest

from tests.integration.content_kit import make_sample_repo
from tests.integration.test_cli_bootstrap import SAMPLE, invoke


def test_issue_list_and_revoke(tmp_path: Path) -> None:
    make_sample_repo(tmp_path)
    config = tmp_path / "dev.toml"
    config.write_text(SAMPLE.replace("keys/fernet.key", "secrets/cerebro.key"))
    command = ("--config", str(config), "token")
    assert json.loads(invoke(*command, "list").stdout) == []
    issued = invoke(*command, "issue", "fixture-agent", "--ceiling", "internal", "--scope", "read", "--scope", "write")
    assert issued.returncode == 0
    assert issued.stderr == ""
    secret = issued.stdout.strip()
    assert len(secret) >= 32
    assert len(secret.split()) == 1
    listing = invoke(*command, "list")
    (row,) = json.loads(listing.stdout)
    assert set(row) == {"token_id", "agent", "scopes", "ceiling", "created_at", "expires_at", "revoked_at"}
    assert (row["agent"], row["scopes"], row["ceiling"]) == ("fixture-agent", ["read", "write"], "internal")
    assert row["expires_at"] is None
    assert row["revoked_at"] is None
    first = invoke(*command, "revoke", row["token_id"])
    again = invoke(*command, "revoke", row["token_id"])
    assert first.returncode == again.returncode == 0
    assert not json.loads(first.stdout)["already_revoked"]
    assert json.loads(again.stdout)["already_revoked"]
    last = invoke(*command, "list")
    assert json.loads(last.stdout)[0]["revoked_at"] == json.loads(first.stdout)["revoked_at"]
    for result in (listing, first, again, last):
        output = result.stdout + result.stderr
        assert secret not in output
        assert hashlib.sha256(secret.encode()).hexdigest() not in output


@pytest.mark.parametrize(
    "args",
    [
        ("issue", "owner"),
        ("issue", "a", "--scope", "owner"),
        ("issue", "a", "--scope", "propose"),
        ("issue", "a", "--expires-at", "2030-01-01"),
        ("issue", "a", "--ceiling", "secret"),
        ("revoke", "é" * 129),
        ("revoke", "x" * 65),
    ],
)
def test_invalid_token_args_fail_before_wiring(args: tuple[str, ...]) -> None:
    result = invoke("token", *args)
    assert result.returncode == 2
    assert result.stderr == "cerebro: invalid arguments (see --help)\n"
