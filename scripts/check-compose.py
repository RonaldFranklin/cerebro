"""Check the resolved deployment before startup: docker compose config --format json | python3 scripts/check-compose.py."""

import json
import sys
from pathlib import Path

model = json.load(sys.stdin)
services = model["services"]
if set(services) != {"backend"}:
    sys.exit("Expected the single backend service on its project network.")
service = services["backend"]
if "network_mode" in service:
    sys.exit("An explicit network_mode bypasses the dedicated project network.")
project = model.get("name")
networks = model.get("networks", {})
if project not in ("cerebro-hml", "cerebro-prod") or set(networks) != {"default"}:
    sys.exit("Expected the default network of the hml or prod project.")
environment = service.get("environment", {})
if project != f"cerebro-{environment.get('CEREBRO_GIT_BRANCH')}":
    sys.exit("The project must match the content branch for its environment.")
command = service.get("command", [])
if (
    service.get("entrypoint") is not None
    or len(command) != 6
    or command[:3] != ["cerebro-mcp", "--container", "--private-origin"]
    or not command[3].startswith("https://")
    or command[4:] != ["--web-dist", "/opt/cerebro/web"]
):
    sys.exit("Start the private-only server with its configured origin and web directory.")
network = networks["default"]
if (
    set(service.get("networks", {})) != {"default"}
    or network.get("name") != f"{project}_default"
    or network.get("driver", "bridge") != "bridge"
    or network.get("external")
    or network.get("driver_opts")
):
    sys.exit("The backend requires its dedicated project bridge.")
identity = service.get("user", "").split(":")
if len(identity) != 2 or not all(value.isascii() and value.isdecimal() and int(value) > 0 for value in identity):
    sys.exit("Set a non-root numeric service UID and GID.")
ports = service.get("ports", [])
if len(ports) != 1:
    sys.exit("Expected exactly one published private port.")
port = ports[0]
private = service.get("environment", {}).get("CEREBRO_PRIVATE_PORT", "")
if (
    port.get("host_ip") != "127.0.0.1"
    or port.get("protocol", "tcp") != "tcp"
    or str(port.get("target")) != private
    or str(port.get("published")) != private
):
    sys.exit("Publish the private port only as 127.0.0.1:PORT:PORT/tcp.")

if sys.argv[1:] == ["--project"]:
    print(project)
elif len(sys.argv) == 3:
    [container] = json.loads(Path(sys.argv[1]).read_text())
    [bridge] = json.loads(Path(sys.argv[2]).read_text())
    name = network["name"]
    labels = container["Config"]["Labels"]
    runtime = container["NetworkSettings"]
    bindings = {f"{private}/tcp": [{"HostIp": "127.0.0.1", "HostPort": private}]}
    if (
        container["State"].get("Health", {}).get("Status") != "healthy"
        or labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != "backend"
        or container["HostConfig"]["NetworkMode"] != name
        or set(runtime["Networks"]) != {name}
        or runtime["Ports"] != bindings
        or bridge["Name"] != name
        or bridge["Driver"] != "bridge"
        or bridge.get("Options")
        or bridge["Labels"].get("com.docker.compose.project") != project
        or bridge["Labels"].get("com.docker.compose.network") != "default"
        or set(bridge["Containers"]) != {container["Id"]}
    ):
        sys.exit("The running backend failed its health, project bridge or loopback publication check.")
elif len(sys.argv) != 1:
    sys.exit("Accepts --project or two Docker inspect JSON files (container and network).")
