"""Back up one deployed environment from the Linux host. Run as the service file owner."""

import argparse
import fcntl
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
from contextlib import ExitStack, closing
from datetime import datetime, timezone
from pathlib import Path


def git(*args: str, cwd: Path) -> str:
    # The backup transport uses only its own temporary repository and HTTPS credentials from gh.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1"
    )
    return subprocess.check_output(["git", *args], cwd=cwd, env=env, text=True).strip()


def sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record(state: Path, revision: str) -> None:
    """Leave the time and revision of a successful backup where the service's Health report reads them.

    Written beside the old record and renamed over it, so a link or a half-written file is never followed or read.
    """
    text = json.dumps(
        {"completed_at": datetime.now(timezone.utc).isoformat(), "content_revision": revision}, sort_keys=True
    )
    pending = state / f".last-backup-{secrets.token_hex(8)}"
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(text + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(state / "last-backup.json")
        sync(state)
    finally:
        pending.unlink(missing_ok=True)


def backup(content: Path, state: Path, destination: Path, repository: str, branch: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/cerebro-content", repository):
        raise ValueError("expected OWNER/cerebro-content")
    if destination.is_relative_to(content) or destination.is_relative_to(state):
        raise ValueError("choose a backup directory outside content and state")
    if not stat.S_ISDIR((content / ".git").lstat().st_mode):
        raise ValueError("expected the content checkout")
    source = state / "state.sqlite"
    verifier = state / "owner.verifier"
    if not all(stat.S_ISREG(p.lstat().st_mode) for p in (source, verifier)):
        raise ValueError("expected regular state.sqlite and owner.verifier files")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    work = destination / ".pending"
    with ExitStack() as cleanup:
        try:
            work.mkdir(mode=0o700)
        except FileExistsError:
            raise ValueError("backup in progress or interrupted: inspect .pending before retrying") from None
        cleanup.callback(shutil.rmtree, work)
        snapshot = work / "snapshot"
        snapshot.mkdir(mode=0o700)
        # The same lock serializes service writes. Release it before contacting the remote.
        with (state / "writer.lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            ref = f"refs/heads/{branch}"
            if git("symbolic-ref", "--quiet", "HEAD", cwd=content) != ref:
                raise ValueError("content checkout branch does not match the backup environment")
            revision = git("rev-parse", "--verify", f"{ref}^{{commit}}", cwd=content)
            with (
                closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as live,
                closing(sqlite3.connect(snapshot / "state.sqlite")) as saved,
            ):
                live.backup(saved)
                saved.execute("PRAGMA journal_mode=DELETE")
                if saved.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("SQLite backup failed quick_check")
            shutil.copyfile(verifier, snapshot / "owner.verifier", follow_symlinks=False)
            (snapshot / "content-revision").write_text(revision + "\n", encoding="ascii")
        for file in snapshot.iterdir():
            sync(file)
        sync(snapshot)
        # Avoid reading remotes, push URL rewrites and hooks from the content checkout. Borrow committed
        # objects while pushing the recorded revision. Nothing copies the working tree or .git metadata.
        transport = work / "transport.git"
        algorithm = git("rev-parse", "--show-object-format", cwd=content)
        git("init", "--bare", "--quiet", "--template=", f"--object-format={algorithm}", str(transport), cwd=work)
        (transport / "objects/info/alternates").write_bytes(os.fsencode(content / ".git/objects") + b"\n")
        if git(
            "rev-list",
            "-1",
            "--full-history",
            revision,
            "--",
            ":(glob)**/state.sqlite",
            ":(glob)**/state.sqlite-*",
            ":(glob)**/owner.verifier",
            ":(glob)**/index.sqlite",
            ":(glob)**/index.sqlite-*",
            ":(glob)**/usage.sqlite",
            ":(glob)**/usage.sqlite-*",
            ":(glob)**/quarantine-*/**",
            cwd=transport,
        ):
            raise ValueError("content history includes a local-only backup exclusion")
        metadata = json.loads(
            subprocess.check_output(
                ["gh", "repo", "view", f"github.com/{repository}", "--json", "nameWithOwner,visibility"],
                text=True,
            )
        )
        if metadata.get("visibility") != "PRIVATE" or metadata["nameWithOwner"].casefold() != repository.casefold():
            raise ValueError("GitHub did not confirm the requested private repository")
        git(
            "-c",
            "http.followRedirects=false",
            "-c",
            "credential.helper=!gh auth git-credential",
            "push",
            "--no-follow-tags",
            "--no-verify",
            f"https://github.com/{repository}.git",
            f"{revision}:refs/heads/{branch}",
            cwd=transport,
        )
        completed = destination / (stamp + "-" + secrets.token_hex(4))
        snapshot.rename(completed)
        sync(destination)
        try:
            record(state, revision)
        except OSError as error:
            # The snapshot and the push succeeded. Health keeps the older record, whose age gives the warning.
            print(f"Backup complete, but could not record it for Health: {error}", file=sys.stderr)
        return completed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--repository", required=True, help="GitHub OWNER/cerebro-content")
    parser.add_argument("--branch", required=True, choices=("hml", "prod"))
    args = parser.parse_args()
    os.umask(0o077)
    try:
        snapshot = backup(
            args.content.resolve(), args.state.resolve(), args.destination.resolve(), args.repository, args.branch
        )
    except (OSError, sqlite3.Error, subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(f"Backup failed: {error}", file=sys.stderr)
        return 1
    print(snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
