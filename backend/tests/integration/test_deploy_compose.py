"""Render both Compose deployments and check the same publication guard that runs before startup."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def checked(model: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-compose.py")],
        input=json.dumps(model),
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(("environment", "port"), [("hml", "18765"), ("prod", "28765")])
def test_deployment_model(tmp_path: Path, environment: str, port: str) -> None:
    data = tmp_path / environment
    origin = f"https://{environment}.example.invalid"
    sha = "a" * 40
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "deploy/compose.yaml"),
            "config",
            "--format",
            "json",
        ],
        env={
            "PATH": os.environ["PATH"],
            "ENVIRONMENT": environment,
            "PRIVATE_PORT": port,
            "PRIVATE_ORIGIN": origin,
            "DATA_DIR": str(data),
            "APP_SHA": sha,
            "CEREBRO_GIT_NAME": "Fixture",
            "CEREBRO_GIT_EMAIL": "fixture@example.invalid",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    model = json.loads(result.stdout)
    assert model["name"] == f"cerebro-{environment}"
    guard = checked(model)
    assert guard.returncode == 0, guard.stderr
    for unsafe in ({"external": True}, {"driver": "macvlan"}, {"name": "host"}, {"driver_opts": {"example": "1"}}):
        shared = {**model["networks"]["default"], **unsafe}
        assert checked({**model, "networks": {"default": shared}}).returncode != 0
    service = model["services"]["backend"]
    assert service["image"] == f"cerebro-backend:git-{sha}"
    assert service["build"]["context"] == str(ROOT)
    assert service["pull_policy"] == "never"
    assert service["restart"] == "unless-stopped"
    assert service["logging"]["options"].keys() >= {"max-size", "max-file"}
    assert service["user"] == "10001:10001"
    assert service["environment"]["CEREBRO_PRIVATE_PORT"] == port
    assert service["environment"]["CEREBRO_GIT_BRANCH"] == environment
    assert service["environment"]["CEREBRO_CONTENT_DIR"] == "/data/content/repo"
    assert service["environment"]["CEREBRO_INDEX_DIR"] == "/data/index"
    assert service["command"] == [
        "cerebro-mcp",
        "--container",
        "--private-origin",
        origin,
        "--web-dist",
        "/opt/cerebro/web",
    ]
    mounts = {v["target"]: v for v in service["volumes"]}
    assert set(mounts) == {"/data/content", "/data/index", "/data/state", "/data/secrets"}
    for target, volume in mounts.items():
        assert volume["source"] == str(data / Path(target).name)
        assert volume.get("read_only", False) == (target == "/data/secrets")


@pytest.mark.parametrize(
    "overrides",
    [
        {"ports": [{"target": 18765, "published": "18765"}]},
        {"ports": [{"target": 18765, "published": "18765", "host_ip": "0.0.0.0"}]},
        {"ports": [{"target": 18765, "published": "28765", "host_ip": "127.0.0.1"}]},
        {"network_mode": "host", "ports": []},
        {"network_mode": "host"},
        {"networks": {"another": None}},
        {"user": "0:10001"},
        {"user": "10001:0"},
    ],
)
def test_preflight_refuses_unsafe_service(overrides: dict[str, object]) -> None:
    network = {"name": "cerebro-hml_default"}
    service = {
        "environment": {"CEREBRO_PRIVATE_PORT": "18765"},
        "ports": [{"target": 18765, "published": "18765", "host_ip": "127.0.0.1"}],
        "networks": {"default": None},
        "user": "10001:10001",
    }
    model = {"name": "cerebro-hml", "networks": {"default": network}, "services": {"backend": service}}
    assert checked(model).returncode == 0
    assert checked({**model, "services": {"backend": {**service, **overrides}}}).returncode != 0
