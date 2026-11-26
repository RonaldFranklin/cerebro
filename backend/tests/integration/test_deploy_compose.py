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
        {"command": ["cerebro-mcp", "--container", "--enable-public"]},
        {"entrypoint": ["sh", "-c", "cerebro-mcp --enable-public"]},
        {"environment": {"CEREBRO_PRIVATE_PORT": "18765", "CEREBRO_GIT_BRANCH": "prod"}},
        {"user": "0:10001"},
        {"user": "10001:0"},
    ],
)
def test_preflight_refuses_unsafe_service(overrides: dict[str, object]) -> None:
    network = {"name": "cerebro-hml_default"}
    service = {
        "command": [
            "cerebro-mcp",
            "--container",
            "--private-origin",
            "https://example.invalid",
            "--web-dist",
            "/opt/cerebro/web",
        ],
        "environment": {"CEREBRO_PRIVATE_PORT": "18765", "CEREBRO_GIT_BRANCH": "hml"},
        "ports": [{"target": 18765, "published": "18765", "host_ip": "127.0.0.1"}],
        "networks": {"default": None},
        "user": "10001:10001",
    }
    model = {"name": "cerebro-hml", "networks": {"default": network}, "services": {"backend": service}}
    assert checked(model).returncode == 0
    assert checked({**model, "services": {"backend": {**service, **overrides}}}).returncode != 0


@pytest.mark.parametrize("failure", ["", "up", "inspect", "port", "network", "health", "preflight"])
def test_guarded_start(tmp_path: Path, failure: str) -> None:
    """Real Compose resolves the file; a simulated daemon records startup and shutdown."""
    import shutil

    docker = shutil.which("docker")
    assert docker
    stub = tmp_path / "docker"
    stub.write_text("""#!/bin/sh
echo "$*" >> "$CALLS"
case "$*" in
    *" config --format json") exec "$REAL_DOCKER" "$@" ;;
    *" up "*) test "$FAILURE" != up ;;
    *" ps "*) echo abc ;;
    "container inspect abc") test "$FAILURE" != inspect && cat "$CONTAINER" ;;
    "network inspect cerebro-hml_default") cat "$NETWORK" ;;
    *" stop backend") exit 0 ;;
    *) exit 1 ;;
esac
""")
    stub.chmod(0o700)
    port = "18765"
    network = "cerebro-hml_default"
    calls = tmp_path / "calls"
    instance = {
        "Id": "abc",
        "State": {"Health": {"Status": "unhealthy" if failure == "health" else "healthy"}},
        "Config": {"Labels": {"com.docker.compose.project": "cerebro-hml", "com.docker.compose.service": "backend"}},
        "HostConfig": {"NetworkMode": network},
        "NetworkSettings": {
            "Networks": {network: {}},
            "Ports": {f"{port}/tcp": [{"HostIp": "0.0.0.0" if failure == "port" else "127.0.0.1", "HostPort": port}]},
        },
    }
    bridge: dict[str, object] = {
        "Name": network,
        "Driver": "host" if failure == "network" else "bridge",
        "Containers": {"abc": {}},
        "Labels": {"com.docker.compose.project": "cerebro-hml", "com.docker.compose.network": "default"},
    }
    for filename, record in (("instance", instance), ("bridge", bridge)):
        (tmp_path / filename).write_text(json.dumps([record]))
    env = {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "ENVIRONMENT": "hml",
        "PRIVATE_PORT": port,
        "PRIVATE_ORIGIN": "https://hml.example.invalid",
        "APP_SHA": "a" * 40,
        "DATA_DIR": str(tmp_path / "hml"),
        "CEREBRO_GIT_NAME": "Fixture",
        "CEREBRO_GIT_EMAIL": "fixture@example.invalid",
        "REAL_DOCKER": docker,
        "FAILURE": failure,
        "CALLS": str(calls),
        "CONTAINER": str(tmp_path / "instance"),
        "NETWORK": str(tmp_path / "bridge"),
    }
    if failure == "preflight":
        env["COMPOSE_PROJECT_NAME"] = "cerebro-prod"
    result = subprocess.run([str(ROOT / "deploy/start.sh"), "/dev/null"], env=env, capture_output=True, check=False)
    assert (result.returncode == 0) == (not failure), result.stderr
    commands = calls.read_text()
    assert (" up " in commands) == (failure != "preflight")
    assert (" stop backend" in commands) == (failure not in ("", "preflight"))
