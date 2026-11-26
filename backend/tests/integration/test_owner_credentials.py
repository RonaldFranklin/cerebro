import os
from pathlib import Path

import pytest

from cerebro.adapters.outbound.crypto.owner import OwnerCredentials
from tests.fakes.owner_credentials import FakeOwnerCredentials


@pytest.fixture(params=["fake", "real"])
def credentials(request: pytest.FixtureRequest, tmp_path: Path) -> OwnerCredentials | FakeOwnerCredentials:
    if request.param == "fake":
        return FakeOwnerCredentials()
    return OwnerCredentials(tmp_path / "owner")


def test_verifier_contract(credentials: OwnerCredentials | FakeOwnerCredentials) -> None:
    assert credentials.generation() is None
    assert credentials.verify(b"first") is None
    credentials.provision(b"first")
    generation = credentials.generation()
    assert generation
    assert credentials.verify(b"first") == generation
    assert credentials.verify(b"wrong") is None
    credentials.provision(b"first")
    assert credentials.generation() != generation
    credentials.provision(b"second")
    assert credentials.verify(b"first") is None
    assert credentials.verify(b"second") == credentials.generation()


@pytest.mark.parametrize("password", [b"", b"x" * 1025])
def test_invalid_password_size(credentials: OwnerCredentials | FakeOwnerCredentials, password: bytes) -> None:
    with pytest.raises(ValueError, match="password size"):
        credentials.provision(password)
    assert credentials.verify(password) is None


def test_protected_storage(tmp_path: Path) -> None:
    path = tmp_path / "owner"
    verifier = OwnerCredentials(path)
    verifier.provision(b"example")
    assert path.stat().st_mode & 0o777 == 0o600
    original = path.read_bytes()
    assert b"example" not in original
    path.chmod(0o640)
    assert verifier.generation() is None
    path.chmod(0o600)
    path.write_bytes(b"?" + original[1:])
    assert verifier.verify(b"example") is None
    path.write_bytes(original + b"extra")
    assert verifier.generation() is None
    path.unlink()
    target = tmp_path / "target"
    target.write_bytes(original)
    path.symlink_to(target)
    assert verifier.generation() is None
    path.unlink()
    os.mkfifo(path, 0o600)
    assert verifier.generation() is None


def test_loose_directory(tmp_path: Path) -> None:
    verifier = OwnerCredentials(tmp_path / "owner")
    tmp_path.chmod(0o750)
    with pytest.raises(PermissionError, match="private permissions"):
        verifier.provision(b"example")
    assert verifier.generation() is None
    assert list(tmp_path.iterdir()) == []


def test_occupied_hash_slot_fails_without_hashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    verifier = OwnerCredentials(tmp_path / "owner")
    verifier.provision(b"example")

    def unexpected_hash(password: bytes, salt: bytes) -> bytes:
        pytest.fail("Busy verifier must reject before hashing")

    monkeypatch.setattr("cerebro.adapters.outbound.crypto.owner._derive", unexpected_hash)
    with (tmp_path / "owner.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert verifier.verify(b"example") is None
        assert verifier.generation() is not None


def test_rotation_failure_keeps_previous_verifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = OwnerCredentials(tmp_path / "owner")
    verifier.provision(b"example")
    generation = verifier.generation()

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated failure"):
        verifier.provision(b"new")
    assert verifier.verify(b"example") == generation
    assert {p.name for p in tmp_path.iterdir()} == {"owner", "owner.lock"}
