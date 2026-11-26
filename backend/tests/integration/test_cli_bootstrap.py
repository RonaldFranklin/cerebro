"""The installed CLI validates inputs before opening the content repository."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cerebro.bootstrap.settings import ConfigurationError, load_settings


def invoke(*arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CEREBRO_")}
    return subprocess.run(
        [str(Path(sys.executable).with_name("cerebro")), *arguments],
        input=stdin,
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


def test_write_quota_configuration(tmp_path: Path) -> None:
    file = tmp_path / "settings.toml"
    file.write_text(SAMPLE)
    assert load_settings(file, {}).writes_per_hour == 60
    file.write_text(SAMPLE + "writes_per_hour = 7\n")
    assert load_settings(file, {}).writes_per_hour == 7
    assert load_settings(file, {"CEREBRO_WRITES_PER_HOUR": "4"}).writes_per_hour == 4
    with pytest.raises(ConfigurationError):
        load_settings(file, {"CEREBRO_WRITES_PER_HOUR": "0"})


@pytest.mark.parametrize("value", ["0", "-1", "true", "2.5", '"3"'])
def test_invalid_toml_quota(tmp_path: Path, value: str) -> None:
    file = tmp_path / "settings.toml"
    file.write_text(SAMPLE + "writes_per_hour = " + value)
    with pytest.raises(ConfigurationError):
        load_settings(file, {})


@pytest.mark.parametrize("value", ["0", "-1", "yes", "\u0661", " "])
def test_invalid_environment_quota(tmp_path: Path, value: str) -> None:
    file = tmp_path / "settings.toml"
    file.write_text(SAMPLE)
    with pytest.raises(ConfigurationError):
        load_settings(file, {"CEREBRO_WRITES_PER_HOUR": value})


def test_large_toml_hex_quota_reports_configuration_error(tmp_path: Path) -> None:
    file = tmp_path / "settings.toml"
    file.write_text(SAMPLE + "writes_per_hour = 0x" + "f" * 8000)
    with pytest.raises(ConfigurationError):
        load_settings(file, {})


@pytest.mark.parametrize("key", ["proposals_per_hour", "proposals_pending"])
def test_retired_quota_keys_are_unknown(tmp_path: Path, key: str) -> None:
    config = tmp_path / "retired.toml"
    config.write_text(SAMPLE + f"{key} = 1\n")
    with pytest.raises(ConfigurationError, match="unknown key"):
        load_settings(config, {})


def test_owner_messages_show_only_safe_texts() -> None:
    from cerebro.adapters.outbound.filesystem import NoteFileError
    from cerebro.bootstrap.container import StateDirectoryError
    from cerebro.bootstrap.diagnostics import owner_message

    assert owner_message(StateDirectoryError("the state directory is not a directory")) == (
        "the state directory is not a directory"
    )
    many = [(f"notes/n{i}.md", "invalid frontmatter") for i in range(22)]
    message = owner_message(NoteFileError.from_problems([("notes/evil\nname.md", "invalid frontmatter"), *many]))
    assert message.startswith("note files need attention: 'notes/evil\\nname.md': invalid frontmatter; ")
    assert message.endswith("... and 3 more")
    assert "\n" not in message
    withheld = owner_message(RuntimeError("private note text"))
    assert "private note text" not in withheld
    assert "RuntimeError" in withheld


def test_separate_index_directory(tmp_path: Path) -> None:
    from dataclasses import replace

    from cerebro.bootstrap.container import build_container
    from tests.integration.content_kit import make_sample_repo

    sample = make_sample_repo(tmp_path)
    file = tmp_path / "example.toml"
    file.write_text(SAMPLE + 'index_dir = "indexes"\n')
    explicit = load_settings(file, {})
    assert explicit.index_dir == tmp_path / "indexes"
    index = load_settings(file, {"CEREBRO_INDEX_DIR": str(tmp_path / "derived")}).index_dir
    assert index == tmp_path / "derived"
    service = build_container(replace(sample.settings, index_dir=index))
    service.start()
    assert (index / "index.sqlite").is_file()
    assert (sample.state_dir / "state.sqlite").is_file()
    assert not (sample.state_dir / "index.sqlite").exists()


def test_content_checkout_cannot_hold_plaintext_index(tmp_path: Path) -> None:
    from dataclasses import replace

    from cerebro.bootstrap.container import build_container
    from tests.integration.content_kit import make_sample_repo

    sample = make_sample_repo(tmp_path)
    location = sample.root / "derived"
    with pytest.raises(ConfigurationError, match="outside"):
        build_container(replace(sample.settings, index_dir=location))
    assert not location.exists()
