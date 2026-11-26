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

start_backend() {
    "${dc[@]}" up --wait --no-build --pull never backend || return 1
    local id
    id=$("${dc[@]}" ps -q backend) || return 1
    docker container inspect "$id" > "$scratch/container" || return 1
    docker network inspect "${project}_default" > "$scratch/network" || return 1
    python3 "$check" "$scratch/container" "$scratch/network" < "$scratch/model"
}
if ! start_backend; then
    echo "Startup checks failed. Stopping this project's backend." >&2
    "${dc[@]}" stop backend
    exit 1
fi
