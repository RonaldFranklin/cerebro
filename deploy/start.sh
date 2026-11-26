#!/usr/bin/env bash
# Start an already built image and verify the project's effective isolation.
set -euo pipefail
if (( $# != 1 )); then
    echo "Usage: deploy/start.sh ENV_FILE" >&2
    exit 2
fi
deploy_dir=$(cd -- "$(dirname -- "$0")" && pwd)
dc=(docker compose --env-file "$1" -f "$deploy_dir/compose.yaml")
check="$deploy_dir/../scripts/check-compose.py"
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
"${dc[@]}" config --format json > "$scratch/model"
project=$(python3 "$check" --project < "$scratch/model")
dc+=(-p "$project")

image=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["backend"]["image"])' < "$scratch/model")
docker image inspect "$image" > /dev/null
previous=$("${dc[@]}" ps -q backend)
"${dc[@]}" create --no-build --pull never backend
id=$("${dc[@]}" ps -a -q backend)

check_runtime() {
    docker container inspect "$id" > "$scratch/container" || return 1
    docker network inspect "${project}_default" > "$scratch/network" || return 1
    python3 "$check" "$scratch/container" "$scratch/network" "$1" < "$scratch/model"
}
check_runtime created
if ! "${dc[@]}" start --wait backend || ! check_runtime running; then
    if [[ "$id" != "$previous" ]]; then
        echo "Startup checks failed. Stopping the backend started by this attempt." >&2
        docker container stop "$id"
    fi
    exit 1
fi
