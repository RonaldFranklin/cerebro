"""Run the host backup command with a synthetic private remote. No test calls GitHub."""

import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "backup.py"


@dataclass
class Backup:
    content: Path
    state: Path
    remote: Path
    copies: Path
    env: dict[str, str]

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--content",
                str(self.content),
                "--state",
                str(self.state),
                "--destination",
                str(self.copies),
                "--repository",
                "example/cerebro-content",
                "--branch",
                "hml",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )


@pytest.fixture
def backup(tmp_path: Path) -> Iterator[Backup]:
    content, remote, state, copies = (tmp_path / name for name in ("content", "remote.git", "state", "backups"))
    git = shutil.which("git")
    assert git
    subprocess.run([git, "init", "-q", "-b", "hml", str(content)], check=True)
    subprocess.run([git, "init", "-q", "--bare", str(remote)], check=True)
    (content / "committed.md").write_text("fixture")
    subprocess.run([git, "-C", str(content), "add", "committed.md"], check=True)
    subprocess.run(
        [
            git,
            "-C",
            str(content),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=a@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    (content / "draft.md").write_text("uncommitted")
    state.mkdir()
    (state / "writer.lock").touch()
    (state / "owner.verifier").write_text("synthetic verifier")
    (state / "index.sqlite").write_text("derived")
    (state / "quarantine-fixture").mkdir()
    (state / "quarantine-fixture" / "worktree.tar").write_text("local-only")
    commands = tmp_path / "bin"
    commands.mkdir()
    gh = commands / "gh"
    gh.write_text("#!/bin/sh\nprintf '%s\\n' \"$BACKUP_TEST_METADATA\"\n")
    gh.chmod(0o700)
    git_wrapper = commands / "git"
    git_wrapper.write_text(
        '#!/bin/sh\nexec "$BACKUP_TEST_GIT" -c "url.$BACKUP_TEST_REMOTE.insteadOf=$BACKUP_TEST_URL" "$@"\n'
    )
    git_wrapper.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "BACKUP_TEST_GIT": git,
        "BACKUP_TEST_REMOTE": str(remote),
        "BACKUP_TEST_URL": "https://github.com/example/cerebro-content.git",
        "BACKUP_TEST_METADATA": '{"nameWithOwner":"example/cerebro-content","visibility":"PRIVATE"}',
    }
    with closing(sqlite3.connect(state / "state.sqlite")) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE saved(value TEXT)")
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("INSERT INTO saved VALUES ('committed in WAL')")
        db.commit()
        yield Backup(content, state, remote, copies, env)


def test_backup_preserves_wal_and_pushes_only_the_recorded_content_commit(backup: Backup) -> None:
    result = backup.run()
    assert result.returncode == 0, result.stderr
    (snapshot,) = backup.copies.iterdir()
    assert {p.name for p in snapshot.iterdir()} == {"content-revision", "state.sqlite", "owner.verifier"}
    assert snapshot.stat().st_mode & 0o077 == 0
    assert (snapshot / "owner.verifier").read_text() == "synthetic verifier"
    with closing(sqlite3.connect(snapshot / "state.sqlite")) as db:
        assert db.execute("SELECT value FROM saved").fetchall() == [("committed in WAL",)]
    revision = (snapshot / "content-revision").read_text().strip()
    git = ["git", "--git-dir", str(backup.remote)]
    assert subprocess.check_output([*git, "rev-parse", "refs/heads/hml"], text=True).strip() == revision
    assert subprocess.check_output([*git, "ls-tree", "--name-only", revision], text=True).splitlines() == [
        "committed.md"
    ]
    assert subprocess.check_output([*git, "for-each-ref", "--format=%(refname)"], text=True).splitlines() == [
        "refs/heads/hml"
    ]


@pytest.mark.parametrize(
    "failure", ["public", "redirected", "push", "branch", "pending", "sqlite-link", "verifier-link"]
)
def test_failed_backup_preserves_the_last_snapshot(backup: Backup, failure: str) -> None:
    result = backup.run()
    assert result.returncode == 0, result.stderr
    previous = set(backup.copies.iterdir())
    if failure == "public":
        backup.env["BACKUP_TEST_METADATA"] = '{"visibility":"PUBLIC","nameWithOwner":"example/cerebro-content"}'
    elif failure == "redirected":
        backup.env["BACKUP_TEST_METADATA"] = '{"visibility":"PRIVATE","nameWithOwner":"other/cerebro-content"}'
    elif failure == "branch":
        subprocess.run(["git", "-C", str(backup.content), "branch", "-m", "prod"], check=True)
    elif failure == "pending":
        pending = backup.copies / ".pending"
        pending.mkdir()
        (pending / "sentinel").write_text("owner inspection")
        previous.add(pending)
    elif failure == "push":
        backup.env["BACKUP_TEST_REMOTE"] += ".missing"
    else:
        leaf = backup.state / ("state.sqlite" if failure == "sqlite-link" else "owner.verifier")
        leaf.rename(leaf.with_suffix(".old"))
        leaf.symlink_to(backup.state / "index.sqlite")
    assert backup.run().returncode != 0
    assert set(backup.copies.iterdir()) == previous


@pytest.mark.parametrize(
    "excluded",
    [
        "index.sqlite",
        "index.sqlite-shm",
        "nested/state.sqlite",
        "state.sqlite-wal",
        "owner.verifier",
        "nested/quarantine-fixture/worktree.tar",
    ],
)
def test_refuses_a_checkout_with_excluded_artifacts_in_history(backup: Backup, excluded: str) -> None:
    artifact = backup.content / excluded
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("local-only")
    git = ["git", "-C", str(backup.content), "-c", "user.name=Fixture", "-c", "user.email=a@example.invalid"]
    subprocess.run([*git, "add", excluded], check=True)
    subprocess.run([*git, "commit", "-qm", "accidental inclusion"], check=True)
    subprocess.run([*git, "rm", "--quiet", excluded], check=True)
    subprocess.run([*git, "commit", "-qm", "removed from working tree"], check=True)
    assert backup.run().returncode != 0
    assert subprocess.check_output(["git", "--git-dir", str(backup.remote), "for-each-ref"]) == b""


def test_overlapping_backups_reserve_one_destination(backup: Backup) -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        with (backup.state / "writer.lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            first = executor.submit(backup.run)
            deadline = time.monotonic() + 5
            while not (backup.copies / ".pending").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (backup.copies / ".pending").exists()
            assert backup.run().returncode != 0
        assert first.result().returncode == 0
    assert len(list(backup.copies.iterdir())) == 1


def test_a_completed_backup_records_its_time_and_revision_for_health(backup: Backup) -> None:
    started = datetime.now(UTC)
    assert backup.run().returncode == 0
    status = backup.state / "last-backup.json"
    assert status.stat().st_mode & 0o777 == 0o600
    record = json.loads(status.read_text())
    assert record.keys() == {"completed_at", "content_revision"}
    (snapshot,) = backup.copies.iterdir()
    assert record["content_revision"] == (snapshot / "content-revision").read_text().strip()
    assert started <= datetime.fromisoformat(record["completed_at"]) <= datetime.now(UTC)
    # The status is not part of the backup, and no temporary file stays behind.
    assert not (snapshot / status.name).exists()
    assert not [p for p in backup.state.iterdir() if p.name.startswith(".last-backup")]


def test_a_failed_backup_keeps_the_earlier_record(backup: Backup) -> None:
    status = backup.state / "last-backup.json"
    status.write_text("earlier record")
    backup.env["BACKUP_TEST_METADATA"] = '{"nameWithOwner":"example/cerebro-content","visibility":"PUBLIC"}'
    assert backup.run().returncode == 1
    assert status.read_text() == "earlier record"


def test_a_link_in_place_of_the_record_is_replaced_and_never_followed(backup: Backup) -> None:
    target = backup.state.parent / "elsewhere"
    target.write_text("untouched")
    (backup.state / "last-backup.json").symlink_to(target)
    assert backup.run().returncode == 0
    assert target.read_text() == "untouched"
    assert not (backup.state / "last-backup.json").is_symlink()


def test_a_snapshot_stays_successful_when_the_record_cannot_be_written(backup: Backup) -> None:
    (backup.state / "last-backup.json").mkdir()
    result = backup.run()
    assert result.returncode == 0, result.stderr
    assert "could not record" in result.stderr
    assert len(list(backup.copies.iterdir())) == 1
