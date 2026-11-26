"""Check the resolved deployment before startup: docker compose config --format json | python3 scripts/check-compose.py."""

import json
import sys

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
