"""The last-backup record is read from the state directory without trusting its contents."""

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cerebro.adapters.outbound.filesystem.backup_status import MAX_BYTES, BackupStatusFile
from cerebro.application.dto import LastBackup

GOOD = {"completed_at": "2026-11-27T03:00:00.123456+00:00", "content_revision": "a" * 40}


def status(tmp_path: Path) -> LastBackup:
    return BackupStatusFile(tmp_path / "last-backup.json").last()


def test_a_missing_record_was_never_made(tmp_path: Path) -> None:
    assert status(tmp_path) == LastBackup(state="never")
    assert BackupStatusFile(tmp_path / "absent" / "last-backup.json").last() == LastBackup(state="never")


@pytest.mark.parametrize("revision", ["a" * 40, "0" * 64])
def test_a_complete_record_is_returned(tmp_path: Path, revision: str) -> None:
    (tmp_path / "last-backup.json").write_text(json.dumps({**GOOD, "content_revision": revision}) + "\n")
    assert status(tmp_path) == LastBackup(
        state="recorded", completed_at=datetime(2026, 11, 27, 3, 0, 0, 123456, tzinfo=UTC), content_revision=revision
    )


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not json",
        b"[]",
        b"\xff\xfe",
        b"[" * 600,
        json.dumps({**GOOD, "extra": "value"}).encode(),
        json.dumps({"completed_at": GOOD["completed_at"]}).encode(),
        json.dumps({**GOOD, "completed_at": 5}).encode(),
        json.dumps({**GOOD, "completed_at": "2026-11-27T03:00:00"}).encode(),
        json.dumps({**GOOD, "completed_at": "2026-11-27T03:00:00+02:00"}).encode(),
        json.dumps({**GOOD, "completed_at": "yesterday"}).encode(),
        json.dumps({**GOOD, "content_revision": "HEAD"}).encode(),
        json.dumps({**GOOD, "content_revision": "A" * 40}).encode(),
        b'{"completed_at": "2026-11-27T03:00:00+00:00", "content_revision": "'
        + b"a" * 40
        + b'", "content_revision": "'
        + b"b" * 40
        + b'"}',
        json.dumps(GOOD).encode() + b" " * MAX_BYTES,
    ],
)
def test_a_damaged_record_is_unreadable_and_nothing_of_it_is_returned(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "last-backup.json").write_bytes(content)
    assert status(tmp_path) == LastBackup(state="unreadable")


def _link(path: Path) -> None:
    target = path.parent / "target"
    target.write_text(json.dumps(GOOD))
    path.symlink_to(target)


@pytest.mark.parametrize("make", [_link, lambda path: path.mkdir(), os.mkfifo])
def test_anything_but_a_regular_file_is_unreadable(tmp_path: Path, make: Callable[[Path], object]) -> None:
    make(tmp_path / "last-backup.json")
    assert status(tmp_path) == LastBackup(state="unreadable")


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_file_without_read_permission_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "last-backup.json"
    path.write_text(json.dumps(GOOD))
    path.chmod(0)
    assert status(tmp_path) == LastBackup(state="unreadable")
