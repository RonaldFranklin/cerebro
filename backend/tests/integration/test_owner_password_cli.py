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
