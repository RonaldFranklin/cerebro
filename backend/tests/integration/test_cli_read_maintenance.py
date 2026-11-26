"""Exercise owner reads and maintenance through the installed CLI and real storage."""

import json
from pathlib import Path

import pytest

from tests.integration.content_kit import RESTRICTED_MARKER, make_sample_repo
from tests.integration.test_cli_bootstrap import SAMPLE, invoke


@pytest.fixture
def config(tmp_path: Path) -> str:
    make_sample_repo(tmp_path)
    path = tmp_path / "dev.toml"
    path.write_text(SAMPLE.replace("keys/fernet.key", "secrets/cerebro.key"))
    return str(path)


def test_search_and_read_as_local_owner(config: str) -> None:
    result = invoke("--config", config, "search", RESTRICTED_MARKER, "--type", "reference", "--limit", "1")
    assert result.returncode == 0, result.stderr
    (hit,) = json.loads(result.stdout)
    assert hit["id"] == "vault-layout"
    assert "body" not in hit
    result = invoke("--config", config, "read", "vault-layout", "--section", "Layout")
    assert result.returncode == 0, result.stderr
    assert RESTRICTED_MARKER in json.loads(result.stdout)["body"]
    hub = invoke("--config", config, "read", "infrastructure")
    assert "vault-layout" in {m["id"] for m in json.loads(hub.stdout)["members"]}
    assert "old-proxy" not in {m["id"] for m in json.loads(hub.stdout)["members"]}
    hub = invoke("--config", config, "read", "infrastructure", "--include-history")
    assert "old-proxy" in {m["id"] for m in json.loads(hub.stdout)["members"]}
    result = invoke("--config", config, "read", "absent")
    assert result.returncode == 1
    assert result.stderr == "cerebro: note not found\n"
    old = invoke("--config", config, "read", "old-proxy")
    assert old.returncode == 0, old.stderr
    assert json.loads(old.stdout)["status"] == "superseded"


def test_reindex_and_health(config: str) -> None:
    (Path(config).parent / "content/schema/types.yaml").write_text("invalid yaml [")
    result = invoke("--config", config, "reindex")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"notes_indexed": 8, "files_excluded": 0}
    result = invoke("--config", config, "health")
    report = json.loads(result.stdout)
    assert report["notes_checked"] == 8
    assert any(issue["subject"] == "broken-link-note" for issue in report["issues"])
    assert "zebrafruit" not in result.stdout


def test_note_file_problems_are_named_for_the_owner(config: str) -> None:
    stray = Path(config).parent / "content/notes/stray-draft.md"
    stray.write_text(f"---\ntitle: {RESTRICTED_MARKER}\n")  # never committed
    result = invoke("--config", config, "search", "ssh")
    assert result.returncode == 0, result.stderr
    assert not stray.exists()
    health = invoke("--config", config, "health")
    assert RESTRICTED_MARKER not in health.stdout
    issues = json.loads(health.stdout)["issues"]
    [warning] = [i for i in issues if i["code"] == "changes_quarantined"]
    assert warning["severity"] == "warning"
    assert warning["subject"].startswith("quarantine-")


@pytest.mark.parametrize(
    "args",
    [
        ("search", "é" * 512 + "x"),
        ("search", "x", "--area", "x" * 257),
        ("read", "x" * 257),
        ("read", "x", "--section", "é" * 128 + "x"),
        ("search", "x", "--limit", "0"),
    ],
)
def test_invalid_fields_fail_before_wiring(args: tuple[str, ...]) -> None:
    result = invoke(*args)
    assert result.returncode == 2
    assert "invalid arguments" in result.stderr


def test_exact_query_and_section_limits_reach_settings() -> None:
    for args in (("search", "é" * 512), ("read", "x", "--section", "é" * 128)):
        assert "missing configuration" in invoke(*args).stderr
