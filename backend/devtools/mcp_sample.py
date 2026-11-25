"""Prepare a new synthetic instance for the MCP acceptance test and manual client checks."""

import argparse
from pathlib import Path

from tests.integration.content_kit import make_sample_repo


def create_sample(directory: Path) -> Path:
    directory.mkdir(mode=0o700)
    make_sample_repo(directory)
    config = directory / "dev.toml"
    config.write_text(
        'content_dir = "content"\nstate_dir = "state"\nkey_file = "secrets/cerebro.key"\n'
        'git_name = "Fixture"\ngit_email = "fixture@example.invalid"\n',
        encoding="utf-8",
    )
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="a new directory under an existing private parent")
    print(create_sample(parser.parse_args().directory))
