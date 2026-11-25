"""The installed init-content command publishes a complete, private repository."""

import stat
from pathlib import Path

import pytest

from cerebro.adapters.outbound.filesystem import load_vocabulary
from cerebro.adapters.outbound.git import GitIdentity, GitRepo
from cerebro.bootstrap.container import build_container
from cerebro.bootstrap.settings import load_settings
from cerebro.domain.vocabulary import INITIAL_USER_NOTE_TYPES
from tests.integration.content_kit import write_key_file
from tests.integration.test_cli_bootstrap import SAMPLE, invoke


def test_init_and_repeat_preserve_the_owner_schema(tmp_path: Path) -> None:
    config = tmp_path / "settings.toml"
    config.write_text(SAMPLE)
    result = invoke("--config", str(config), "init-content")
    assert result.returncode == 0, result.stderr
    settings = load_settings(config, {})
    root = settings.content_repo
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert {p.name for p in root.iterdir()} == {".git", "core", "hubs", "notes", "schema"}
    vocabulary = load_vocabulary(root)
    assert vocabulary.areas == frozenset()
    assert vocabulary.user_types == INITIAL_USER_NOTE_TYPES
    git = GitRepo(root, identity=GitIdentity(name=settings.git_name, email=settings.git_email))
    head = git.head()
    assert head
    assert git.is_clean()
    assert not settings.state_dir.exists()
    assert not settings.key_file.exists()
    key, _ = write_key_file(tmp_path / "keys")
    key.rename(settings.key_file)
    build_container(settings).start()

    areas = root / "schema/areas.yaml"
    areas.write_text("areas: [example]\n")
    repeated = invoke("--config", str(config), "init-content")
    assert repeated.returncode == 0, repeated.stderr
    assert git.head() == head
    assert areas.read_text() == "areas: [example]\n"


@pytest.mark.parametrize("kind", ["unrelated", "partial", "symlink"])
def test_refuse_existing_unsafe_or_unrelated_content(tmp_path: Path, kind: str) -> None:
    config = tmp_path / "settings.toml"
    config.write_text(SAMPLE)
    root = tmp_path / "content"
    if kind == "symlink":
        target = tmp_path / "elsewhere"
        target.mkdir()
        root.symlink_to(target, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
    if kind == "partial":
        (root / "schema").mkdir()
    sentinel = root / "existing"
    sentinel.write_text("preserved")
    result = invoke("--config", str(config), "init-content")
    assert result.returncode != 0
    assert sentinel.read_text() == "preserved"
    assert not (root / ".git").exists()


def test_failed_initialization_can_be_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cerebro.adapters.outbound.git import GitError
    from cerebro.bootstrap.init_content import InitializationError, initialize_content

    config = tmp_path / "settings.toml"
    config.write_text(SAMPLE)
    settings = load_settings(config, {})

    def fail(*args: object, **kwargs: object) -> str:
        raise GitError("injected commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(GitRepo, "commit", fail)
        with pytest.raises(InitializationError):
            initialize_content(settings)
    assert not settings.content_repo.exists()
    assert not list(tmp_path.glob(".cerebro-init-*"))
    initialize_content(settings)
    assert load_vocabulary(settings.content_repo).areas == frozenset()
