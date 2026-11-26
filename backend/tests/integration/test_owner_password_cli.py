"""A local CLI rotation revokes browser credentials in the running process."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cerebro.adapters.outbound.crypto.owner import OwnerCredentials
from cerebro.adapters.outbound.system.sessions import MemorySessions
from cerebro.application.browser import AuthenticationFailed, BrowserAuth, BrowserBinding
from tests.fakes.login_attempts import FakeLoginAttempts
from tests.fakes.system import FixedClock, SequentialIdGenerator
from tests.integration.test_cli_bootstrap import SAMPLE


def _command(config: Path, *, prompts: tuple[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    arguments = [str(Path(sys.executable).with_name("cerebro")), "--config", str(config), "owner-password"]
    if prompts is not None:
        # Replace only the interactive terminal in the separate CLI process.
        program = (
            "import getpass, sys\n"
            "replies = iter(sys.stdin.read().splitlines())\n"
            "getpass.getpass = lambda prompt: next(replies)\n"
            "from cerebro.bootstrap.cli import main\n"
            "sys.exit(main())"
        )
        arguments = [sys.executable, "-c", program, *arguments[1:]]
    return subprocess.run(
        arguments,
        input="\n".join(prompts or ("private fixture", "private fixture")),
        capture_output=True,
        start_new_session=True,
        timeout=10,
        text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("CEREBRO_")},
        check=False,
    )


@pytest.fixture
def config(tmp_path: Path) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(SAMPLE)
    return config


def test_cli_requires_hidden_prompt_and_matching_confirmation(config: Path) -> None:
    for prompts in (None, ("private fixture", "wrong confirmation")):
        result = _command(config, prompts=prompts)
        assert result.returncode == 1
        assert "private fixture" not in result.stdout + result.stderr
        assert not (config.parent / "state/owner.verifier").exists()


def test_cli_external_rotation_after_and_during_login(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _command(config, prompts=("first", "first")).returncode == 0
    credentials = OwnerCredentials(config.parent / "state/owner.verifier")
    auth = BrowserAuth(credentials, FakeLoginAttempts(), MemorySessions(), FixedClock(), SequentialIdGenerator())
    binding = BrowserBinding("private", "https://localhost")
    session = auth.login(binding, b"first")
    assert _command(config, prompts=("second", "second")).returncode == 0
    with pytest.raises(AuthenticationFailed):
        auth.session(binding, session.session_id)
    verify = credentials.verify

    def rotate_before_issuance(password: bytes) -> str | None:
        generation = verify(password)
        assert generation
        assert _command(config, prompts=("third", "third")).returncode == 0
        return generation

    monkeypatch.setattr(credentials, "verify", rotate_before_issuance)
    with pytest.raises(AuthenticationFailed):
        auth.login(binding, b"second")


def test_rotation_reports_durability_failure_after_replacement(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import stat

    from cerebro.bootstrap.cli import main

    assert _command(config, prompts=("old fixture", "old fixture")).returncode == 0
    credentials = OwnerCredentials(config.parent / "state/owner.verifier")
    auth = BrowserAuth(credentials, FakeLoginAttempts(), MemorySessions(), FixedClock(), SequentialIdGenerator())
    binding = BrowserBinding("private", "https://localhost")
    session = auth.login(binding, b"old fixture")
    previous = credentials.generation()
    sync = os.fsync

    def fail_directory_sync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("confidential failure detail")
        sync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_sync)
    monkeypatch.setattr(sys, "argv", ["cerebro", "--config", str(config), "owner-password"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: "new fixture")
    for key in os.environ.copy():
        if key.startswith("CEREBRO_"):
            monkeypatch.delenv(key)
    assert main() == 1
    output = capsys.readouterr()
    assert output.err == "cerebro: owner password updated, durability unconfirmed\n"
    assert output.out == ""
    current = credentials.generation()
    assert current
    assert current != previous
    assert credentials.verify(b"new fixture") == current
    assert credentials.verify(b"old fixture") is None
    with pytest.raises(AuthenticationFailed):
        auth.session(binding, session.session_id)


@pytest.mark.parametrize("location", ["content", "repository", "symlink"])
def test_provisioning_refuses_unsafe_state_location(config: Path, location: str) -> None:
    if location == "content":
        config.write_text(SAMPLE.replace('"state"', '"content/state"'))
    elif location == "repository":
        (config.parent / ".git").mkdir()
    else:
        target = config.parent / "target"
        target.mkdir(mode=0o700)
        (config.parent / "state").symlink_to(target)
    result = _command(config, prompts=("fixture", "fixture"))
    assert result.returncode == 2
    assert not tuple(config.parent.rglob("owner.verifier"))
