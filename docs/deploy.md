# Deploy Cérebro with a private tailnet and a public agent entrance

Run as the designated **non-root Linux operator** with access to local Docker, GitHub, Tailscale and the host's `cloudflared`. Use Bash in a private terminal. The owner supplies the placeholders and approves the host, tailnet access policy and backup location. Run hml first. Record release SHAs and image IDs locally. Keep passwords, tokens and key material out of transcripts, Git and Compose environment files.

## Installation variables

Run the entire definitions block in each operator shell, including for updates. These directory and port defaults are suggestions. Choose disjoint private trees on trusted filesystems and distinct ports. Set the server's SSH alias separately from its tailnet HTTPS name. Each environment also has its own public hostname on the owner's Cloudflare zone. Agents use only that public URL and a bearer token ([ADR-0028](adr/0028-token-decides-public-access.md)). Tailscale Serve stays the owner's private browser entrance.

```bash
set -euo pipefail
set +x
umask 077
SERVER_HOST='<server-ssh-alias>'
TAILNET_URL='https://<machine>.<tailnet>.ts.net'
HML_PUBLIC_ORIGIN='https://<hml-public-hostname>'; PROD_PUBLIC_ORIGIN='https://<prod-public-hostname>'
GITHUB_OWNER='<private-content-repository-owner>'
APP_REPOSITORY='<app-repository-owner>/cerebro'
PROJECT_ROOT=/srv/projetos/cerebro
DATA_ROOT=/data/projetos/cerebro
BACKUP_ROOT=/data/backups/cerebro
BACKUP_MOUNT=''  # set to the expected mountpoint when backups use a dedicated filesystem
HML_PORT=18765; PROD_PORT=28765
HML_PUBLIC_PORT=18766; PROD_PUBLIC_PORT=28766
HML_HTTPS=8443; PROD_HTTPS=443
REPOS="$PROJECT_ROOT/repos"
APP_REPO="$REPOS/cerebro"
export CEREBRO_GIT_NAME='Cerebro Service' CEREBRO_GIT_EMAIL='cerebro@example.invalid'
export SERVICE_UID="$(id -u)" SERVICE_GID="$(id -g)"
test "$SERVICE_UID" -gt 0 && test "$SERVICE_GID" -gt 0 || exit 1
select_env() {
  export ENVIRONMENT="$1"
  case "$1" in
    hml) PRIVATE_PORT=$HML_PORT; HTTPS_PORT=$HML_HTTPS; PUBLIC_PORT=$HML_PUBLIC_PORT; PUBLIC_ORIGIN=$HML_PUBLIC_ORIGIN ;;
    prod) PRIVATE_PORT=$PROD_PORT; HTTPS_PORT=$PROD_HTTPS; PUBLIC_PORT=$PROD_PUBLIC_PORT; PUBLIC_ORIGIN=$PROD_PUBLIC_ORIGIN ;;
    *) return 1 ;;
  esac
  PRIVATE_ORIGIN="${TAILNET_URL%/}"
  if [[ $HTTPS_PORT != 443 ]]; then PRIVATE_ORIGIN+=":$HTTPS_PORT"; fi
  export PRIVATE_ORIGIN PRIVATE_PORT DATA_DIR="$DATA_ROOT/$1"
  # The token ceiling alone decides what each agent sees on the public entrance (ADR-0028).
  export PUBLIC_ORIGIN PUBLIC_PORT PUBLIC_CEILING=restricted
  ENV_DIR="$PROJECT_ROOT/ambientes/$1"; BACKUP_DIR="$BACKUP_ROOT/$1"
  export CEREBRO_CONTENT_DIR="$DATA_DIR/content/repo" CEREBRO_STATE_DIR="$DATA_DIR/state"
  export CEREBRO_INDEX_DIR="$DATA_DIR/index" CEREBRO_KEY_FILE="$DATA_DIR/secrets/fernet.key"
  export CEREBRO_GIT_BRANCH="$1"
  CLI="$ENV_DIR/bootstrap-venv/bin/cerebro"
}
use_release() {
  [[ $1 =~ ^[a-f0-9]{40}$ ]] || return 1
  export APP_SHA="$1"; RELEASE="$REPOS/releases/$1"
  test "$(git -C "$RELEASE" rev-parse HEAD)" = "$1"
  local changes
  changes=$(git -C "$RELEASE" status --porcelain --untracked-files=all) || return 1
  test -z "$changes"
}
dc() { docker compose --env-file /dev/null -p "cerebro-$ENVIRONMENT" -f "$RELEASE/deploy/compose.yaml" "$@"; }
guard() { dc config --format json | python3 "$RELEASE/scripts/check-compose.py"; }
backup() {
  if [[ -n $BACKUP_MOUNT ]]; then
    mountpoint -q "$BACKUP_MOUNT" || return 1
    test "$(findmnt -n -o TARGET -T "$BACKUP_DIR")" = "$BACKUP_MOUNT" || return 1
  fi
  python3 "$RELEASE/deploy/backup.py" --content "$CEREBRO_CONTENT_DIR" --state "$CEREBRO_STATE_DIR" \
    --destination "$BACKUP_DIR" --repository "$GITHUB_OWNER/cerebro-content" --branch "$ENVIRONMENT"
}
start_release() {
  guard
  "$RELEASE/deploy/start.sh" /dev/null  # validated Compose create --no-build --pull never, then start --wait
  cid=$(dc ps -q backend)
  test "$(docker inspect --format '{{.State.Health.Status}}' "$cid")" = healthy
  if [[ $(docker inspect --format '{{.Image}}' "$cid") != "$image_id" ]]; then
    dc stop backend; return 1
  fi
  docker inspect --format '{{json .NetworkSettings.Ports}}' "$cid"
  test "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PRIVATE_PORT/mcp")" = 401
  test "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PUBLIC_PORT/mcp")" = 401
  ss -H -lnt "( sport = :$PRIVATE_PORT or sport = :$PUBLIC_PORT )"
}
record_release() {
  if [[ -f $ENV_DIR/current ]] && ! cmp -s "$ENV_DIR/current" <(printf '%s %s\n' "$APP_SHA" "$image_id"); then
    cp "$ENV_DIR/current" "$ENV_DIR/previous.new"; mv "$ENV_DIR/previous.new" "$ENV_DIR/previous"
  fi
  printf '%s %s\n' "$APP_SHA" "$image_id" > "$ENV_DIR/current.new"
  mv "$ENV_DIR/current.new" "$ENV_DIR/current"
}
select_env hml
```

## A. Read-only preflight

```bash
hostname
docker context inspect --format '{{.Endpoints.docker.Host}}'
env | cut -d= -f1 | grep -E '^(DOCKER_HOST|COMPOSE_FILE|COMPOSE_PROFILES|COMPOSE_PROJECT_NAME)$' || true
docker version
docker info --format '{{.OSType}} {{.Architecture}} {{.DockerRootDir}} {{json .SecurityOptions}}'
docker compose version
docker ps --format '{{.Names}} {{.Ports}}'
tailscale status --json
tailscale serve status
tailscale funnel status
cloudflared --version
systemctl status cloudflared --no-pager
ss -H -lnt
df -h
git --version
flock --version
python3 -c 'import sqlite3,sys; assert sys.version_info >= (3,10); print(sqlite3.sqlite_version)'
uv --version
gh auth status --hostname github.com
gh repo view "$APP_REPOSITORY" --json nameWithOwner,visibility,viewerPermission
gh repo list "$GITHUB_OWNER" --limit 1000 --json nameWithOwner,visibility --jq '.[] | select(.nameWithOwner | endswith("/cerebro-content"))'
```

**Verify** the expected host and a local Unix Docker socket, Engine 28+ on Linux amd64/arm64, Compose with `--wait`, Git, Python with SQLite and uv 0.13.9. Confirm bind-mount ownership maps the operator UID and GID into the container. Stop for an owner-approved mapping if user namespace remapping is active. Confirm enough free space for two release images, Git history, both environments and backups. Inspect listening ports, existing Docker publications, Serve routes and Funnel. Confirm `cloudflared` runs as a host service, so it can reach host loopback, and identify the tunnel dedicated to Cérebro. Never route Cérebro through a tunnel shared with other services. Confirm the tailnet is running, its DNS name matches the configured URL, HTTPS is enabled and its access rules limit the service to the owner's clients. Check read access to the app and permission to create or push to the private content repository.

**Stop and contact the owner** for missing prerequisites, inadequate storage, a different host or Docker endpoint, deployment overrides, conflicting listeners/routes, ambiguous GitHub identity or public access. A missing listing alone does not establish that the repository is available. [Docker port publication](https://docs.docker.com/engine/network/port-publishing/) · [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)

## B. Create private directories

Inspect the path components first. Resolve unexpected symlinks, shared writable ancestors or a missing expected backup mount with the owner. For a new installation the environment's four data directories must be new. Reuse shared release storage only if it already belongs to this operator.

```bash
for d in "$REPOS" "$ENV_DIR" "$DATA_DIR" "$BACKUP_DIR"; do namei -l "$d" || true; done
for d in "$REPOS" "$REPOS/releases" "$ENV_DIR" "$DATA_DIR" "$BACKUP_DIR" \
         "$DATA_DIR"/{content,state,index,secrets}; do
  if [[ ! -e $d && ! -L $d ]]; then
    sudo install -d -m 0700 -o "$SERVICE_UID" -g "$SERVICE_GID" "$d"
  fi
  test ! -L "$d"; test -d "$d"; test "$(stat -c '%u:%g:%a' "$d")" = "$SERVICE_UID:$SERVICE_GID:700"
done
for d in "$DATA_DIR"/{content,state,index,secrets}; do entries=$(ls -A "$d"); test -z "$entries"; done
test ! -e "$ENV_DIR/current"; test ! -e "$ENV_DIR/previous"
```

**Verify** the checkout will live at `content/repo`, the other three mounts are separate and backups reside outside all four. For a dedicated backup filesystem verify its expected mount with `findmnt -T "$BACKUP_DIR"`.

**Stop** on existing instance data, insecure ownership or filesystem mismatch. Use the update or recovery procedure for an existing instance.

## C. Clone the application and initialize content

Clone the app once. Select the approved main commit for hml. For prod use the hml acceptance record. Reuse a verified clean release worktree for the same commit.

```bash
if [[ ! -d $APP_REPO ]]; then gh repo clone "$APP_REPOSITORY" "$APP_REPO"; fi
(cd "$APP_REPO" && test "$(gh repo view --json nameWithOwner --jq .nameWithOwner)" = "$APP_REPOSITORY")
git -C "$APP_REPO" fetch origin main
if [[ $ENVIRONMENT = hml ]]; then sha=$(git -C "$APP_REPO" rev-parse 'FETCH_HEAD^{commit}')
else read -r sha accepted_id < "$PROJECT_ROOT/ambientes/hml/current"; fi
if [[ ! -d $REPOS/releases/$sha ]]; then git -C "$APP_REPO" worktree add --detach "$REPOS/releases/$sha" "$sha"; fi
use_release "$sha"
(cd "$RELEASE/backend" && UV_PROJECT_ENVIRONMENT="$ENV_DIR/bootstrap-venv" uv sync --locked --no-dev --no-editable --python 3.14.10)
"$CLI" --check-config
"$CLI" init-content
if [[ $ENVIRONMENT = hml ]]; then
  printf 'areas:\n  - validation\n' > "$CEREBRO_CONTENT_DIR/schema/areas.yaml"
  git -C "$CEREBRO_CONTENT_DIR" -c user.name="$CEREBRO_GIT_NAME" -c user.email="$CEREBRO_GIT_EMAIL" \
    commit -m 'Add synthetic validation area' -- schema/areas.yaml
fi
test "$(git -C "$CEREBRO_CONTENT_DIR" branch --show-current)" = "$ENVIRONMENT"
changes=$(git -C "$CEREBRO_CONTENT_DIR" status --porcelain); test -z "$changes"
```

Use the locked host CLI only for provisioning. Inspect the intended GitHub repository explicitly.

```bash
if ! gh repo view "$GITHUB_OWNER/cerebro-content" --json nameWithOwner,visibility,viewerPermission; then
  echo "Resolve access or confirm initial absence with owner before continuing." >&2
fi
```

Only on first hml installation, after the owner confirms that this name is unused and creation is intended, run:

```bash
gh repo create "$GITHUB_OWNER/cerebro-content" --private
```

```bash
gh repo view "$GITHUB_OWNER/cerebro-content" --json nameWithOwner,visibility,viewerPermission
```

**Verify** the exact owner/name, PRIVATE visibility and write permission. Hml and prod each start with their own local Git history and push to distinct branches in this one private repository. Keep credentials on the host.

**Stop** on existing unrelated content, a nonempty target branch during initial installation, unexpected app revision or any GitHub error other than owner-confirmed initial absence. Check remote branches using `gh api --paginate "repos/$GITHUB_OWNER/cerebro-content/branches" --jq '.[].name'` before continuing.

## D. Generate and escrow both secrets

Have the owner generate a distinct strong password in their off-server password manager. In a private interactive terminal paste it twice at the prompt. Create a fresh Fernet key, without displaying it:

```bash
test ! -e "$CEREBRO_STATE_DIR/owner.verifier"
"$ENV_DIR/bootstrap-venv/bin/python" - <<'PY'
import os
from pathlib import Path
from cerebro.adapters.outbound.crypto import generate_key
with Path(os.environ["CEREBRO_KEY_FILE"]).open("xb") as key:
    key.write(generate_key() + b"\n")
PY
"$CLI" owner-password
test "$(stat -c '%a' "$CEREBRO_KEY_FILE")" = 600
test -s "$CEREBRO_STATE_DIR/owner.verifier"
```

From the owner's trusted workstation with verified SSH host key, set `SERVER_HOST` and `CEREBRO_KEY_FILE` to the agreed alias and the absolute remote file path. Transfer directly to a private staging directory:

```bash
umask 077
staging=$(mktemp -d)
scp "$SERVER_HOST:$CEREBRO_KEY_FILE" "$staging/fernet.key"
```

**Verify** the owner has saved **both the Fernet key and the owner password outside the server** in the password manager under the correct environment. Have them verify the stored key against the file, then run `rm -- "$staging/fernet.key" && rmdir "$staging"` on the workstation. Keep the hml and prod secrets separate. The verifier at `state/owner.verifier` is part of the backup.

**Stop** before building or starting the service until the owner confirms both secrets. Stop on an existing key or verifier during first-time provisioning.

## E. Build locally, start hml and enable private HTTPS

Build in hml from the selected release and start through the validated deployment helper:

```bash
guard
tag="cerebro-backend:git-$APP_SHA"
if [[ $ENVIRONMENT = hml ]]; then
  if docker image inspect "$tag" >/dev/null 2>&1; then
    echo 'Tag already exists. Verify prior release record with owner before reuse.' >&2; exit 1
  fi
  dc build backend
fi
image_id=$(docker image inspect --format '{{.Id}}' "$tag")
if [[ $ENVIRONMENT = prod ]]; then test "$image_id" = "$accepted_id"; fi
start_release
```

**Verify** the image was built from the pinned sources on this host. The only publication is the environment port on IPv4 loopback. Docker can implement it with firewall rules and no socket visible in `ss`. Check for unexpected wildcard, LAN or IPv6 listeners. Expect exactly two publications: the private and public ports. Enable the previously unused private HTTPS listener:

```bash
sudo tailscale serve --bg --https="$HTTPS_PORT" "http://127.0.0.1:$PRIVATE_PORT"
tailscale serve status
tailscale funnel status
test "$(curl -sS -o /dev/null -w '%{http_code}' "$PRIVATE_ORIGIN/mcp")" = 401
test "$(curl -sS -o /dev/null -w '%{http_code}' "$PRIVATE_ORIGIN/")" = 200
```

**Stop** on unhealthy containers, tag or binding mismatch, Funnel or other public routes to the private port, unexpected Serve target, certificate errors or failed HTTP checks. Never publish a container port off host loopback or enable Funnel.

## E2. Publish the public entrance through Cloudflare

The dedicated Cérebro tunnel publishes **only the public listener**. Never point a tunnel at the private port. Add one ingress rule per environment to the tunnel's configuration (locally managed `config.yml` shown; set the same values in the dashboard for a remotely managed tunnel). Leave `httpHostHeader` unset: the web UI accepts only requests whose `Host` matches `PUBLIC_ORIGIN`. [Tunnel configuration](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/configure-tunnels/local-management/configuration-file/)

```yaml
tunnel: <cerebro-tunnel-id>
credentials-file: /etc/cloudflared/<cerebro-tunnel-id>.json
ingress:
  - hostname: <hml-public-hostname>
    service: http://127.0.0.1:18766   # HML_PUBLIC_PORT
  - hostname: <prod-public-hostname>
    service: http://127.0.0.1:28766   # PROD_PUBLIC_PORT
  - service: http_status:404
```

Create the DNS route with `cloudflared tunnel route dns <cerebro-tunnel-id> <public-hostname>` and restart the service. In Cloudflare Zero Trust create **two** self-hosted Access applications per public hostname. [Access applications](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-public-app/) · [Application paths](https://developers.cloudflare.com/cloudflare-one/policies/access/app-paths/) · [Bypass](https://developers.cloudflare.com/cloudflare-one/policies/access/#bypass)

1. **Web UI.** Domain: exactly the public hostname, path empty. One **Allow** policy for the owner's identity with MFA. This protects every path except the one below.
2. **MCP.** Domain: exactly the same hostname, path exactly `mcp`. No wildcard in the hostname or path, no other paths and no query string. One **Bypass** policy including Everyone. The Cérebro bearer token protects this path. Bypass requests are not logged by Access.

Cérebro serves MCP only at the exact path `/mcp`. If Access also bypasses longer paths such as `/mcp/x` or `/mcpx`, Cérebro answers them with 404 and serves no UI, API or assets there. Verify from a host outside the tailnet, for each environment:

```bash
PUBLIC_ORIGIN='https://<public-hostname>'  # the environment under test
test "$(curl -sS -o /dev/null -w '%{http_code}' "$PUBLIC_ORIGIN/mcp")" = 401
for path in / /search /api/session /assets/ /mcp/ /mcp/x /mcpx; do
  read -r code location < <(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "$PUBLIC_ORIGIN$path")
  if [[ $code = 302 && $location == https://*.cloudflareaccess.com/* ]]; then echo "$path: Access"
  elif [[ $path = /mcp?* && $code = 404 ]]; then echo "$path: bypassed, Cérebro 404"
  else echo "$path: unexpected $code" >&2; false; fi
done
```

Expect `401` for unauthenticated MCP and an Access redirect for every UI, API and asset path. Then open `PUBLIC_ORIGIN` in the owner's browser. Pass Access, then sign in with the owner password. The public session reports ceiling `internal`, whatever `PUBLIC_CEILING` is, and offers no Recent changes, editing or Health; use the private origin for those.

**Stop** on any `200` without Access, a tunnel route to the private port, a wildcard bypass or a hostname shared with other services. **Accepted risk:** a leaked token works from the internet until it is revoked, up to its ceiling and scopes.

## F. Issue one token per client

Run each command separately in an unrecorded private terminal. Transfer its single displayed bearer directly to the owner's password manager for this environment.

```bash
dc exec -T backend cerebro token issue claude-code --ceiling internal --scope read --scope write --scope core
dc exec -T backend cerebro token issue codex --ceiling internal --scope read --scope write --scope core
dc exec -T backend cerebro token list
```

**Verify** separate Claude Code and Codex identities with all three scopes. The token ceiling alone limits what each client sees through the public entrance. The example grants `internal`; issue `--ceiling restricted` only to a client that needs restricted notes. Configure each client on the workstation using [client setup](client-setup.md).

**Stop** if a credential was exposed or lost. Revoke the affected token using its ID from the list (`dc exec -T backend cerebro token revoke "<token-id>"`) before issuing its replacement.

## G. Accept hml before promoting to prod

1. From WSL, follow the commands in [client setup](client-setup.md) with the hml public URL and use each configured client to call `cerebro_overview` and `cerebro_write_guide`. Check the correct environment and authenticated results. In hml ask Claude Code to create an internal P2 fixture in the `validation` area using the write guide. Ask Codex to read it by ID. Record the ID and confirm the Git attribution in Recent changes.
2. Open the private HTTPS origin in the owner's browser, sign in, read the fixture, inspect Recent changes and health. Use separate browser profiles for hml and prod, which share a hostname. Verify the tailnet access policy with the owner. Repeat the public UI checks in E2.
3. On the host run the following. After restart, repeat the authenticated MCP read and browser login/read. Check the same commit and data persisted.

```bash
dc stop backend
start_release
dc exec -T backend cerebro health
snapshot=$(backup)
test -f "$snapshot/state.sqlite"; test -f "$snapshot/owner.verifier"
test "$(cat "$snapshot/content-revision")" = "$(git -C "$CEREBRO_CONTENT_DIR" rev-parse HEAD)"
test "$(gh api "repos/$GITHUB_OWNER/cerebro-content/git/ref/heads/$ENVIRONMENT" --jq .object.sha)" = "$(cat "$snapshot/content-revision")"
find "$snapshot" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort
```

4. Expect exactly `content-revision`, `owner.verifier` and `state.sqlite`. Never copy quarantine or `index.sqlite` into backups. Install the bounded 18:10 backup and weekly Git maintenance jobs from the [backup procedure](../deploy/backup.md). Supply the checkout ending in `/content/repo`. Verify the backup job under its scheduled account. If it uses a dedicated mount, apply the mountpoint guard to every scheduled backup.

**Hml restore drill.** Reserve a maintenance window and an unused temporary loopback port. Use the hml snapshot containing the fixture. Restore into a fresh private tree using the commands under “Verify a restore” in the backup procedure:

```bash
test "$ENVIRONMENT" = hml
SNAPSHOT="$snapshot"
drill_root=$(mktemp -d "$ENV_DIR/restore-XXXXXXXX")
RESTORE_DIR="$drill_root/hml"
DRILL_PORT=38765  # check ss and docker ps as in A
test ! -e "$RESTORE_DIR"
gh auth setup-git --hostname github.com
```

After those restore commands succeed, retrieve the **hml** Fernet key from the owner's off-server escrow to a private temporary file. Set `ESCROW_FILE` to that file. Prepare the empty derived index and verify the escrowed key matches before taking hml offline.

```bash
install -d -m 0700 "$RESTORE_DIR"/{secrets,index}
install -m 0600 "$ESCROW_FILE" "$RESTORE_DIR/secrets/fernet.key"
cmp -s "$CEREBRO_KEY_FILE" "$RESTORE_DIR/secrets/fernet.key"
# Refuse to overwrite an unfinished drill's recovery record.
(set -C; printf '%s\n' "$APP_SHA" "$image_id" "$RESTORE_DIR" "$DRILL_PORT" > "$ENV_DIR/drill-recovery")
dc down
(
  export DATA_DIR="$RESTORE_DIR" PRIVATE_PORT="$DRILL_PORT"
  start_release
  sudo tailscale serve --bg --https="$HTTPS_PORT" "http://127.0.0.1:$PRIVATE_PORT"
  tailscale serve status
)
```

Sign in in a fresh browser session with the escrowed hml password and read the fixture by its recorded ID through both the browser and MCP. Restore normal hml even if a drill check fails. If Bash exited, open a fresh terminal as the same operator and run the installation variables block again with the original values. The private recovery record also works during a first install before hml has an accepted release:

```bash
select_env hml
mapfile -t recovery < "$ENV_DIR/drill-recovery"
test "${#recovery[@]}" -eq 4
use_release "${recovery[0]}"
image_id=${recovery[1]}; RESTORE_DIR=${recovery[2]}; DRILL_PORT=${recovery[3]}
test "$(docker image inspect --format '{{.Id}}' "cerebro-backend:git-$APP_SHA")" = "$image_id"
(DATA_DIR="$RESTORE_DIR"; PRIVATE_PORT="$DRILL_PORT"; dc down)
start_release
sudo tailscale serve --bg --https="$HTTPS_PORT" "http://127.0.0.1:$PRIVATE_PORT"
tailscale serve status
test "$(curl -sS -o /dev/null -w '%{http_code}' "$PRIVATE_ORIGIN/mcp")" = 401
```

Verify ordinary hml reads again, then run `rm -- "$ENV_DIR/drill-recovery"`. Stop and contact the owner if ordinary hml cannot be restored. Keep the private drill tree under the owner's retention policy. Production recovery is separate and requires owner approval.

**Verify** the owner accepts hml, its restored fixture, both clients and external secret custody. Record the accepted SHA/image pair **after acceptance only**:

```bash
record_release
```

**Stop** on any discrepancy or missing owner acceptance. Only then run `select_env prod` and repeat A–G (skip the hml-only restore drill) using prod's directories, branch, HTTPS port, credentials and backups. In A expect the recorded hml resources to be present. In C reuse the private GitHub repository. E uses the exact accepted local image without rebuilding. Use authorized production content for production checks.

## H. Update, rollback and uninstall

**Update.** Use the same operator account and serialize all maintenance for the chosen environment, including restores and release record updates. Start in hml. Load its accepted release, verify the image remains on disk and back up before changing anything:

```bash
select_env hml
read -r old_sha old_image < "$ENV_DIR/current"
use_release "$old_sha"
test "$(docker image inspect --format '{{.Id}}' "cerebro-backend:git-$old_sha")" = "$old_image"
backup
git -C "$APP_REPO" fetch origin main
candidate=$(git -C "$APP_REPO" rev-parse 'FETCH_HEAD^{commit}')
test "$candidate" != "$old_sha"
if [[ ! -d $REPOS/releases/$candidate ]]; then git -C "$APP_REPO" worktree add --detach "$REPOS/releases/$candidate" "$candidate"; fi
use_release "$candidate"
```

Run the build/start block in E, verify the existing Serve route, then repeat the hml checks in G and record acceptance. Keep the existing keys, verifier and client tokens. Promote only that accepted image:

```bash
select_env prod
read -r old_sha old_image < "$ENV_DIR/current"
use_release "$old_sha"
test "$(docker image inspect --format '{{.Id}}' "cerebro-backend:git-$old_sha")" = "$old_image"
backup
read -r sha accepted_id < "$PROJECT_ROOT/ambientes/hml/current"
use_release "$sha"
image_id=$(docker image inspect --format '{{.Id}}' "cerebro-backend:git-$APP_SHA")
test "$image_id" = "$accepted_id"
start_release
```

Verify prod as in G and run `record_release`. Retain both release worktrees, tags and image IDs. Do not run image pruning. If a candidate fails before recording acceptance, the environment's `current` still points to the last accepted release.

**Rollback.** Select the affected environment. Use `previous` after a recorded promotion, or `current` after an unaccepted candidate. Verify with the owner that the target release can read the current content and state. Preserve a backup before switching:

```bash
select_env hml  # select prod only for a production rollback
record=previous  # current for an unaccepted candidate
read -r sha image_id < "$ENV_DIR/$record"
use_release "$sha"
test "$(docker image inspect --format '{{.Id}}' "cerebro-backend:git-$APP_SHA")" = "$image_id"
backup
start_release
```

Verify private HTTPS, the public checks in E2, both MCP reads and owner login before `record_release`. **Stop** on missing images, incompatible schema or mismatched keys. Consult the owner about isolated recovery using the backup procedure before replacing any data.

**Uninstall.** Confirm the environment with the owner. Quiesce writes and take a final backup. Identify and remove only this environment's scheduled jobs in `crontab -e`.

```bash
select_env hml  # choose the approved environment
read -r sha image_id < "$ENV_DIR/current"
use_release "$sha"
crontab -l
crontab -e
sudo tailscale serve --https="$HTTPS_PORT" off
# Remove this environment's tunnel ingress rule, DNS route and both Access applications first.
dc stop backend
backup
dc down
tailscale serve status
docker ps --format '{{.Names}} {{.Ports}}'
ss -H -lnt "( sport = :$PRIVATE_PORT or sport = :$PUBLIC_PORT )"
```

**Verify** the chosen routes and publications are gone and the other environment still passes its HTTPS check. Retain release images, secrets, backups and bind-mounted data. **Stop** and obtain separate owner authorization for permanent deletion.
