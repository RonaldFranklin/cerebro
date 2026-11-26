# Back up an environment

Run this on the Docker host as the account that owns the deployed content and state files. The host needs
Python 3.10 or newer with SQLite, Git, and a GitHub CLI login with write access to the private content repository.
The service container needs no GitHub credentials. Populate these variables from the environment's installation
record. Check that the content checkout and state directory are the mounts for the **same** Compose project;
keep its backup destination separate from the other environment. The content branch configured at initialization
must equal the environment name. The same private repository holds independent `hml` and `prod` branches.

```sh
APP_DIR='<absolute-application-checkout>'
ENVIRONMENT='hml'  # use prod for production
ENV_DATA_DIR='<absolute-data-directory-for-this-environment>'
CONTENT_DIR="$ENV_DATA_DIR/content/repo"
STATE_DIR="$ENV_DATA_DIR/state"
BACKUP_DIR='<absolute-private-backup-directory-for-this-environment>'
GITHUB_OWNER='<github-owner>'

install -d -m 0700 "$BACKUP_DIR"
python3 "$APP_DIR/deploy/backup.py" --content "$CONTENT_DIR" --state "$STATE_DIR" \
  --destination "$BACKUP_DIR" --repository "$GITHUB_OWNER/cerebro-content" --branch "$ENVIRONMENT"
```

A directory on the state disk protects against application failures but shares that disk's failure domain.
For disk-loss recovery, use separate private storage for completed snapshots. When using a mounted backup
volume, put `mountpoint -q "<expected-backup-mount>" &&` before the backup command in the shell and cron.
This prevents falling back to the underlying filesystem. Exercise the restore from that storage.

The command prints a new snapshot directory only after GitHub confirms private visibility and the push succeeds.
Each completed snapshot holds exactly `state.sqlite` (SQLite's online backup, including committed WAL data),
`owner.verifier` (the password verifier), and `content-revision` (the commit to restore). Start the service and
provision the owner's password before the first backup. Keep backups private and keep the Fernet key in the
owner's separate offline custody. The verifier is sensitive; retain the owner's password in the password manager.

The push sends the recorded commit and its ancestors to the chosen environment branch. Missing credentials,
public repositories, excluded files in commit history, and divergent branches fail the job. A failed job leaves
earlier completed snapshots available. The command refuses to start if a hidden `.pending` directory
already exists. Check for an active backup. After a timeout or power loss, confirm no backup is running
and ask the owner to inspect and move the interrupted directory aside before retrying. Git pushes never include
untracked checkout or Git metadata. The state backup reads an explicit list of files. Keep all `quarantine-*`
directories, quarantine scratch data, and the derived `index.sqlite` out of any external filesystem backup.

## Schedule

Replace the placeholders with absolute paths and install one line per environment using the backup account's
`crontab -e`. Ensure this account's cron PATH includes its verified `git` and `gh` executables. The example starts
at 18:10 server local time, limits runtime to 30 minutes, and does not catch up missed executions after boot.

```cron
PATH=/usr/local/bin:/usr/bin:/bin
10 18 * * * timeout --kill-after=1m 30m python3 "<absolute-application-checkout>/deploy/backup.py" --content "<absolute-content-checkout>" --state "<absolute-state-directory>" --destination "<absolute-private-backup-directory>" --repository "<github-owner>/cerebro-content" --branch hml
```

The service disables automatic Git garbage collection during note writes. Schedule ordinary Git maintenance
separately under the content owner, away from the shutdown interval. Keep Git's normal grace period.

```cron
30 17 * * 6 timeout --kill-after=1m 30m git -C "<absolute-content-checkout>" -c gc.autoDetach=false gc --auto
```

## Verify a restore

Use an empty private directory and an authenticated host Git client (`gh auth setup-git --hostname github.com`).
For the hml drill, choose a snapshot produced from hml fixtures or a sanitized hml checkout, use only its hml
Fernet key, and set `ENVIRONMENT=hml`. Point `SNAPSHOT` at its completed snapshot directory and `RESTORE_DIR`
at a fresh per-environment path ending in `/hml` (for example `<private-drill-parent>/hml`). A production recovery requires the owner's approval, production access controls, its own private
restore location and its separate production key.

```sh
umask 077
mkdir -m 0700 "$RESTORE_DIR" &&
mkdir -m 0700 "$RESTORE_DIR/content" &&
git clone --branch "$ENVIRONMENT" --single-branch "https://github.com/$GITHUB_OWNER/cerebro-content.git" "$RESTORE_DIR/content/repo" &&
git -C "$RESTORE_DIR/content/repo" reset --keep "$(cat "$SNAPSHOT/content-revision")" &&
mkdir -m 0700 "$RESTORE_DIR/state" &&
install -m 0600 "$SNAPSHOT/state.sqlite" "$SNAPSHOT/owner.verifier" "$RESTORE_DIR/state/" &&
python3 -c 'import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); sys.exit(db.execute("PRAGMA quick_check").fetchall() != [("ok",)])' "$RESTORE_DIR/state/state.sqlite"
```

Stop on a nonzero exit. For the hml drill, start an isolated hml instance using these fixture files, the hml key,
a new empty index directory and distinct loopback ports. Verify authenticated login and representative fixture reads.
Keep production snapshots and their key exclusively within the production recovery environment.
