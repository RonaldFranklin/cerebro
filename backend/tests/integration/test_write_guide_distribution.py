"""Verify the wheel contains the same guide the MCP tool serves from the source installation."""

import subprocess
import sys
from pathlib import Path

from cerebro.adapters.inbound.write_guide import read_write_guide


def test_guide_is_readable_from_the_installed_wheel(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[2]
    subprocess.run(["uv", "build", "--wheel", "--offline", "--out-dir", str(tmp_path)], cwd=backend, check=True)
    (wheel,) = tmp_path.glob("*.whl")
    installed = tmp_path / "installed"
    subprocess.run(
        ["uv", "pip", "install", "--offline", "--no-deps", "--target", str(installed), str(wheel)], check=True
    )
    result = subprocess.run(
        [
            sys.executable,
            "-IS",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from cerebro.adapters.inbound.write_guide import read_write_guide; "
            "sys.stdout.write(read_write_guide())",
            str(installed),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.startswith("# Cérebro writing guide v1")
    assert result.stdout == read_write_guide()
