"""The installed CLI validates inputs before opening the content repository."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cerebro.bootstrap.settings import ConfigurationError, load_settings


def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CEREBRO_")}
    return subprocess.run(
        [str(Path(sys.executable).with_name("cerebro")), *arguments],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=10,
    )


SAMPLE = """content_dir = "content"
state_dir = "state"
key_file = "keys/fernet.key"
git_name = "Fixture"
git_email = "fixture@example.invalid"
"""


def test_help_and_invalid_arguments() -> None:
    assert "--config" in invoke("--help").stdout
    bad = invoke("unrecognized-confidential-value")
    assert bad.returncode == 2
    assert "unrecognized-confidential-value" not in bad.stderr


def test_missing_configuration() -> None:
    result = invoke("--check-config")
    assert result.returncode == 2
    assert "CEREBRO_CONTENT_DIR" in result.stderr


def test_explicit_config_and_environment_override(tmp_path: Path) -> None:
    file = tmp_path / "settings.toml"
    file.write_text(SAMPLE)
    assert invoke("--config", str(file), "--check-config").returncode == 0
    settings = load_settings(file, {"CEREBRO_STATE_DIR": str(tmp_path / "override")})
    assert settings.content_repo == tmp_path / "content"
    assert settings.state_dir == tmp_path / "override"
    assert not settings.state_dir.exists()


@pytest.mark.parametrize("suffix", ['typo = "sensitive"', 'git_branch = ["sensitive"]', "sensitive syntax"])
def test_bad_toml_is_redacted(tmp_path: Path, suffix: str) -> None:
    file = tmp_path / "settings.toml"
    file.write_text(SAMPLE + suffix)
    result = invoke("--config", str(file), "--check-config")
    assert result.returncode == 2
    assert "sensitive" not in result.stderr


def test_config_byte_boundary(tmp_path: Path) -> None:
    file = tmp_path / "settings.toml"
    file.write_bytes(SAMPLE.encode() + b"#".ljust(65536 - len(SAMPLE), b" "))
    assert load_settings(file, {}).content_repo == tmp_path / "content"
    with file.open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ConfigurationError):
        load_settings(file, {})


def test_argument_byte_limit_precedes_even_help() -> None:
    # Two UTF-8 bytes per character. Include one byte for each argument separator.
    limit = 65536 - len("--help") - 2
    assert invoke("--help", "é" * (limit // 2)).returncode == 0
    assert invoke("--help", "é" * (limit // 2) + "x").returncode == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [("CEREBRO_GIT_BRANCH", "--invalid"), ("CEREBRO_GIT_EMAIL", "<invalid>"), ("CEREBRO_GIT_REMOTE", "--invalid")],
)
def test_check_config_rejects_invalid_git_settings(tmp_path: Path, field: str, value: str) -> None:
    file = tmp_path / "settings.toml"
    file.write_text(SAMPLE)
    with pytest.raises(ConfigurationError):
        load_settings(file, {field: value})


def test_missing_working_directory_is_a_configuration_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = tmp_path / "settings.toml"
    file.write_text(SAMPLE)
    environment = {"CEREBRO_CONTENT_DIR": "relative-content"}
    directory = tmp_path / "removed"
    directory.mkdir()
    monkeypatch.chdir(directory)
    directory.rmdir()
    with pytest.raises(ConfigurationError):
        load_settings(file, environment)
