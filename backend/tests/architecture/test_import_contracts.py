"""Canary tests: prove the import-linter contracts actually fail on forbidden imports.

Each case copies ``src/cerebro`` into a temporary directory, injects one import and runs the real
``lint-imports`` against the project configuration. No network access is needed: import-linter only parses
source files, so third-party packages such as ``yaml`` do not have to be installed.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
PACKAGE_DIR = BACKEND_DIR / "src" / "cerebro"
LINT_IMPORTS = Path(sys.executable).parent / "lint-imports"


def run_lint_imports(tmp_path: Path, module: str, source: str) -> subprocess.CompletedProcess[str]:
    shutil.copytree(PACKAGE_DIR, tmp_path / "cerebro")
    (tmp_path / "cerebro" / Path(*module.split(".")[1:])).with_suffix(".py").write_text(source)
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    return subprocess.run(
        [str(LINT_IMPORTS), "--config", str(BACKEND_DIR / "pyproject.toml"), "--no-cache"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.parametrize(
    ("module", "source", "expected"),
    [
        ("cerebro.domain.canary", "import yaml\n", "cerebro.domain.canary imports non-standard-library module yaml"),
        (
            "cerebro.application.canary",
            "from pydantic import BaseModel\n",
            "cerebro.application.canary imports non-standard-library module pydantic",
        ),
        (
            "cerebro.domain.canary",
            "import cerebro.application\n",
            "cerebro.domain.canary -> cerebro.application (l.1)",
        ),
        (
            "cerebro.application.canary",
            "import cerebro.adapters\n",
            "cerebro.application.canary -> cerebro.adapters (l.1)",
        ),
        (
            "cerebro.adapters.inbound.canary",
            "import cerebro.application.ports\n",
            "cerebro.adapters.inbound.canary -> cerebro.application.ports (l.1)",
        ),
        (
            "cerebro.adapters.inbound.canary",
            "from cerebro.application.ports import __name__\n",
            "cerebro.adapters.inbound.canary -> cerebro.application.ports (l.1)",
        ),
        (
            "cerebro.adapters.inbound.canary",
            "import cerebro.adapters.outbound\n",
            "cerebro.adapters.inbound.canary -> cerebro.adapters.outbound (l.1)",
        ),
        (
            "cerebro.adapters.inbound.canary",
            "import cerebro.bootstrap.container\n",
            "cerebro.adapters.inbound.canary -> cerebro.bootstrap.container (l.1)",
        ),
        (
            "cerebro.adapters.inbound.canary",
            "from cerebro.bootstrap import settings\n",
            "cerebro.adapters.inbound.canary -> cerebro.bootstrap",
        ),
    ],
)
def test_forbidden_import_breaks_a_contract(tmp_path: Path, module: str, source: str, expected: str) -> None:
    result = run_lint_imports(tmp_path, module, source)

    assert result.returncode != 0, result.stdout
    assert expected in result.stdout


def test_standard_library_imports_are_allowed_in_the_core(tmp_path: Path) -> None:
    source = "from __future__ import annotations\nimport dataclasses\nimport json\nfrom collections import abc\n"

    result = run_lint_imports(tmp_path, "cerebro.domain.canary", source)

    assert result.returncode == 0, result.stdout


def test_inbound_adapters_may_use_use_cases_dto_and_domain(tmp_path: Path) -> None:
    source = (
        "import cerebro.application.dto\n"
        "import cerebro.application.use_cases\n"
        "import cerebro.domain\n"
        "import dataclasses\n"
    )

    result = run_lint_imports(tmp_path, "cerebro.adapters.inbound.canary", source)

    assert result.returncode == 0, result.stdout
