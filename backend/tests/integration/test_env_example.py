"""The published environment example must describe a layout the application accepts."""

import shlex
from pathlib import Path

from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.settings import load_settings

_REPOSITORY = Path(__file__).resolve().parents[3]
_PATH_KEYS = ("CEREBRO_CONTENT_DIR", "CEREBRO_STATE_DIR", "CEREBRO_KEY_FILE")


def _example() -> dict[str, str]:
    text = (_REPOSITORY / ".env.example").read_text(encoding="utf-8")
    words = (word for line in text.splitlines() for word in shlex.split(line, comments=True))
    return dict(word.split("=", 1) for word in words)


def test_example_paths_are_absolute_and_separate() -> None:
    example = _example()
    content, state, key = (Path(example[name]) for name in _PATH_KEYS)
    for path in (content, state, key):
        assert path.is_absolute()
        assert not path.is_relative_to(_REPOSITORY)
    assert not state.is_relative_to(content)
    assert not content.is_relative_to(state)
    assert not key.is_relative_to(content)
    assert not key.is_relative_to(state)


def test_example_loads_and_its_layout_is_accepted(tmp_path: Path) -> None:
    environment = _example()
    # Keep the example's layout, re-rooted so the test never touches the placeholder location.
    for name in _PATH_KEYS:
        environment[name] = str(tmp_path / Path(environment[name]).relative_to("/"))
    settings = load_settings(None, environment)
    owner_credentials(settings)
    assert settings.state_dir.is_dir()
