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
    in_checkout = {**service, "build": {"context": str(tmp_path)}}
    assert checked({**model, "services": {"backend": in_checkout}}).returncode != 0
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
def test_preflight_refuses_unsafe_service(tmp_path: Path, overrides: dict[str, object]) -> None:
    network = {"name": "cerebro-hml_default"}
    service: dict[str, object] = {
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
    service["build"] = {"context": str(ROOT)}
    service["volumes"] = [
        {"target": f"/data/{part}", "source": str(tmp_path / "hml" / part), "type": "bind"}
        for part in ("state", "index", "content", "secrets")
    ]
    model = {"name": "cerebro-hml", "networks": {"default": network}, "services": {"backend": service}}
    assert checked(model).returncode == 0
    assert checked({**model, "services": {"backend": {**service, **overrides}}}).returncode != 0


@pytest.mark.parametrize(
    "failure",
    [
        "",
        "start",
        "inspect",
        "port",
        "network",
        "attached",
        "concurrent",
        "foreign",
        "late-foreign",
        "health",
        "preflight",
        "missing-image",
        "wrong-data",
        "symlink",
        "existing",
    ],
)
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
    "image inspect "*) test "$FAILURE" != missing-image ;;
    *" create "*) exit 0 ;;
    *" start "*)
        touch "$STARTED"
        if [ "$FAILURE" = concurrent ]; then
            FAILURE="" DATA_DIR="$OTHER_DATA" CALLS="$OTHER_CALLS" "$START_SCRIPT" /dev/null
            echo "$?" > "$SECOND_RESULT"
        fi
        test "$FAILURE" != start ;;
    *" ps -a -q backend") echo abc ;;
    "container inspect abc") test "$FAILURE" != inspect && cat "$CONTAINER" ;;
    "network inspect cerebro-hml_default")
        if [ "$FAILURE" = late-foreign ] && [ -f "$STARTED" ]; then cat "$LATE_NETWORK"; else cat "$NETWORK"; fi ;;
    "container stop abc") exit 0 ;;
    *) exit 1 ;;
esac
""")
    stub.chmod(0o700)
    port = "18765"
    network = "cerebro-hml_default"
    calls = tmp_path / "calls"
    instance = {
        "Id": "abc",
        "State": {"Health": {"Status": "unhealthy" if failure in ("health", "existing") else "healthy"}},
        "Config": {"Labels": {"com.docker.compose.project": "cerebro-hml", "com.docker.compose.service": "backend"}},
        "HostConfig": {
            "NetworkMode": network,
            "PortBindings": {f"{port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]},
        },
        "NetworkSettings": {
            "Networks": {network: {}, "other": {}} if failure == "attached" else {network: {}},
            "Ports": {f"{port}/tcp": [{"HostIp": "0.0.0.0" if failure == "port" else "127.0.0.1", "HostPort": port}]},
        },
    }
    bridge: dict[str, object] = {
        "Name": network,
        "Driver": "host" if failure == "network" else "bridge",
        "Containers": {"abc": {}, "intruder": {}} if failure == "foreign" else {"abc": {}},
        "Labels": {"com.docker.compose.project": "cerebro-hml", "com.docker.compose.network": "default"},
    }
    for filename, record in (
        ("instance", instance),
        ("bridge", bridge),
        ("late-bridge", {**bridge, "Containers": {"abc": {}, "intruder": {}}}),
    ):
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
        "HOME": str(tmp_path),
        "CALLS": str(calls),
        "OTHER_DATA": str(tmp_path / "drill" / "hml"),
        "OTHER_CALLS": str(tmp_path / "drill-calls"),
        "SECOND_RESULT": str(tmp_path / "drill-result"),
        "START_SCRIPT": str(ROOT / "deploy/start.sh"),
        "CONTAINER": str(tmp_path / "instance"),
        "NETWORK": str(tmp_path / "bridge"),
        "LATE_NETWORK": str(tmp_path / "late-bridge"),
        "STARTED": str(tmp_path / "started"),
    }
    if failure == "wrong-data":
        env["DATA_DIR"] = str(tmp_path / "prod")
    data = Path(env["DATA_DIR"])
    if failure == "symlink":
        (tmp_path / "prod").mkdir()
        data.symlink_to(tmp_path / "prod", target_is_directory=True)
    for part in ("state", "index", "content", "secrets"):
        (data / part).mkdir(parents=True)
        if failure == "concurrent":
            (Path(env["OTHER_DATA"]) / part).mkdir(parents=True)
    if failure == "preflight":
        env["COMPOSE_PROJECT_NAME"] = "cerebro-prod"
    result = subprocess.run([env["START_SCRIPT"], "/dev/null"], env=env, capture_output=True, check=False)
    assert (result.returncode == 0) == (failure in ("", "concurrent")), result.stderr
    if failure == "concurrent":
        assert (tmp_path / "drill-result").read_text().strip() != "0"
        assert " create " not in (tmp_path / "drill-calls").read_text()
    commands = calls.read_text()
    assert (" create " in commands) == (failure not in ("preflight", "missing-image", "wrong-data", "symlink"))
    started = failure in ("", "concurrent", "start", "port", "health", "existing", "late-foreign")
    assert (" start " in commands) == started
    assert ("container stop abc" in commands) == (
        failure in ("start", "port", "health", "existing", "network", "foreign", "late-foreign", "inspect", "attached")
    )
    assert " stop backend" not in commands
