"""Load the versioned agent guide on demand from the installed distribution."""

from importlib.resources import files


def read_write_guide() -> str:
    return files(__package__).joinpath("write-guide-v1.md").read_text(encoding="utf-8")
