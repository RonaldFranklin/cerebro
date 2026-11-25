"""Integration tests of the crypto adapter: ``FernetCipher``, key files and the restricted-note format."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from cerebro.adapters.outbound.crypto import (
    FernetCipher,
    InvalidKeyError,
    KeyFileError,
    RestrictedFormatError,
    decode_restricted,
    encode_restricted,
    generate_key,
    load_keys,
)
from cerebro.application.errors import DecryptionError
from cerebro.application.ports import Cipher
from cerebro.domain.note import NoteId
from tests.contract.stores_contract import CipherContract


class TestFernetCipherContract(CipherContract):
    @pytest.fixture
    def cipher(self) -> Cipher:
        return FernetCipher([generate_key()])


class TestFernetCipher:
    def test_ciphertext_differs_from_plaintext_and_between_calls(self) -> None:
        cipher = FernetCipher([generate_key()])
        first, second = cipher.encrypt(b"secret"), cipher.encrypt(b"secret")
        assert first != b"secret"
        assert first != second

    def test_wrong_key_raises_decryption_error(self) -> None:
        token = FernetCipher([generate_key()]).encrypt(b"secret")
        with pytest.raises(DecryptionError):
            FernetCipher([generate_key()]).decrypt(token)

    def test_non_bytes_ciphertext_raises_decryption_error(self) -> None:
        with pytest.raises(DecryptionError):
            FernetCipher([generate_key()]).decrypt("text")  # type: ignore[arg-type]

    def test_old_key_still_decrypts_after_rotation_of_the_key_list(self) -> None:
        old, new = generate_key(), generate_key()
        token = FernetCipher([old]).encrypt(b"payload")
        assert FernetCipher([new, old]).decrypt(token) == b"payload"
        with pytest.raises(DecryptionError):
            FernetCipher([new]).decrypt(token)

    def test_first_key_encrypts(self) -> None:
        old, new = generate_key(), generate_key()
        token = FernetCipher([new, old]).encrypt(b"payload")
        assert FernetCipher([new]).decrypt(token) == b"payload"
        with pytest.raises(DecryptionError):
            FernetCipher([old]).decrypt(token)

    def test_rotate_reencrypts_under_the_first_key(self) -> None:
        old, new = generate_key(), generate_key()
        token = FernetCipher([old]).encrypt(b"payload")
        rotated = FernetCipher([new, old]).rotate(token)
        assert FernetCipher([new]).decrypt(rotated) == b"payload"

    def test_rotate_rejects_token_under_an_unknown_key(self) -> None:
        token = FernetCipher([generate_key()]).encrypt(b"payload")
        with pytest.raises(DecryptionError):
            FernetCipher([generate_key()]).rotate(token)

    def test_rotate_rejects_tampered_token(self) -> None:
        cipher = FernetCipher([generate_key()])
        with pytest.raises(DecryptionError):
            cipher.rotate(b"garbage")

    def test_decryption_error_does_not_leak_material(self) -> None:
        key = generate_key()
        token = FernetCipher([key]).encrypt(b"top-secret-plaintext")
        with pytest.raises(DecryptionError) as raised:
            FernetCipher([generate_key()]).decrypt(token)
        message = str(raised.value)
        assert token.decode() not in message
        assert "top-secret-plaintext" not in message
        assert key.decode() not in message

    def test_at_least_one_key_is_required(self) -> None:
        with pytest.raises(InvalidKeyError):
            FernetCipher([])

    @pytest.mark.parametrize("bad", [b"", b"short", b"x" * 44, b"!" * 44])
    def test_invalid_key_is_refused_without_echoing_it(self, bad: bytes) -> None:
        with pytest.raises(InvalidKeyError) as raised:
            FernetCipher([generate_key(), bad])
        assert "#2" in str(raised.value)
        if bad:
            assert bad.decode() not in str(raised.value)

    def test_a_single_key_passed_bare_is_refused(self) -> None:
        with pytest.raises(InvalidKeyError):
            FernetCipher(generate_key())  # type: ignore[arg-type]

    def test_repr_and_str_show_no_key_material(self) -> None:
        key = generate_key()
        cipher = FernetCipher([key])
        for text in (repr(cipher), str(cipher)):
            assert key.decode() not in text
            assert text == "FernetCipher(keys=1)"
        assert not hasattr(cipher, "__dict__")


class TestLoadKeys:
    @pytest.fixture
    def directory(self, tmp_path: Path) -> Path:
        secrets = tmp_path / "secrets"
        secrets.mkdir(mode=0o700)
        secrets.chmod(0o700)
        return secrets

    def _write(self, directory: Path, text: str, mode: int = 0o600) -> Path:
        path = directory / "keys"
        path.write_text(text)
        path.chmod(mode)
        return path

    def test_reads_keys_in_order_skipping_blanks_and_comments(self, directory: Path) -> None:
        first, second = generate_key(), generate_key()
        path = self._write(directory, f"# current\n{first.decode()}\n\n  # old\n{second.decode()}  \n")
        assert load_keys(path) == (first, second)
        assert load_keys(str(path)) == (first, second)

    def test_generated_key_roundtrips_through_a_file(self, directory: Path) -> None:
        key = generate_key()
        path = self._write(directory, key.decode() + "\n")
        assert FernetCipher(load_keys(path)).decrypt(FernetCipher([key]).encrypt(b"x")) == b"x"

    @pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666, 0o602])
    def test_file_more_open_than_0600_is_refused(self, directory: Path, mode: int) -> None:
        key = generate_key()
        path = self._write(directory, key.decode() + "\n", mode)
        with pytest.raises(KeyFileError) as raised:
            load_keys(path)
        assert str(path) in str(raised.value)
        assert key.decode() not in str(raised.value)

    @pytest.mark.parametrize("mode", [0o600, 0o400])
    def test_owner_only_file_is_accepted(self, directory: Path, mode: int) -> None:
        key = generate_key()
        assert load_keys(self._write(directory, key.decode() + "\n", mode)) == (key,)

    @pytest.mark.parametrize("mode", [0o755, 0o750, 0o770, 0o701])
    def test_directory_more_open_than_0700_is_refused(self, directory: Path, mode: int) -> None:
        path = self._write(directory, generate_key().decode() + "\n")
        directory.chmod(mode)
        try:
            with pytest.raises(KeyFileError, match="directory"):
                load_keys(path)
        finally:
            directory.chmod(0o700)

    def test_symlink_is_refused(self, directory: Path) -> None:
        target = self._write(directory, generate_key().decode() + "\n")
        link = directory / "link"
        link.symlink_to(target)
        with pytest.raises(KeyFileError):
            load_keys(link)

    def test_directory_and_missing_file_are_refused(self, directory: Path) -> None:
        (directory / "keys").mkdir()
        with pytest.raises(KeyFileError):
            load_keys(directory / "keys")
        with pytest.raises(KeyFileError):
            load_keys(directory / "missing")

    def test_fifo_is_refused_without_blocking(self, directory: Path) -> None:
        fifo = directory / "fifo"
        os.mkfifo(fifo, 0o600)
        with pytest.raises(KeyFileError):
            load_keys(fifo)

    @pytest.mark.parametrize("text", ["", "\n\n", "# only a comment\n"])
    def test_file_without_keys_is_refused(self, directory: Path, text: str) -> None:
        with pytest.raises(KeyFileError, match="no keys"):
            load_keys(self._write(directory, text))

    def test_malformed_line_is_refused_without_echoing_it(self, directory: Path) -> None:
        good = generate_key().decode()
        bad = "not-a-valid-key-but-very-secret"
        with pytest.raises(KeyFileError) as raised:
            load_keys(self._write(directory, f"{good}\n{bad}\n"))
        assert "line 2" in str(raised.value)
        assert bad not in str(raised.value)
        assert good not in str(raised.value)

    def test_non_utf8_and_oversize_files_are_refused(self, directory: Path) -> None:
        path = directory / "keys"
        path.write_bytes(b"\xff\xfe\x00")
        path.chmod(0o600)
        with pytest.raises(KeyFileError):
            load_keys(path)
        path.write_bytes(b"#" * (64 * 1024 + 1))
        with pytest.raises(KeyFileError, match="larger"):
            load_keys(path)


class TestKeyLineStrictness:
    @pytest.fixture
    def directory(self, tmp_path: Path) -> Path:
        secrets = tmp_path / "secrets"
        secrets.mkdir(mode=0o700)
        secrets.chmod(0o700)
        return secrets

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda k: k[:10] + "!" + k[10:],
            lambda k: k[:10] + " " + k[10:],
            lambda k: k[:10] + "\x00" + k[10:],
            lambda k: k[:10] + "\u00e7" + k[10:],
            lambda k: k[:10] + "\t" + k[10:],
            lambda k: k + "=",
        ],
    )
    def test_junk_in_a_key_line_is_refused(self, directory: Path, mutate: Callable[[str], str]) -> None:
        bad = mutate(generate_key().decode())
        path = directory / "keys"
        path.write_text(bad + "\n", encoding="utf-8")
        path.chmod(0o600)
        with pytest.raises(KeyFileError) as raised:
            load_keys(path)
        assert "line 1" in str(raised.value)
        assert bad not in str(raised.value)

    def test_foreign_owner_is_refused(self, directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = directory / "keys"
        path.write_text(generate_key().decode() + "\n")
        path.chmod(0o600)
        monkeypatch.setattr(os, "geteuid", lambda: os.stat(path).st_uid + 4242)
        if os.stat(path).st_uid == 0:
            pytest.skip("running as root")
        with pytest.raises(KeyFileError, match="not owned"):
            load_keys(path)

    def test_root_owned_is_accepted(self, directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        key = generate_key()
        path = directory / "keys"
        path.write_text(key.decode() + "\n")
        path.chmod(0o600)
        real = os.fstat
        monkeypatch.setattr(os, "geteuid", lambda: 4242)

        class Root:
            def __init__(self, info: os.stat_result) -> None:
                self.st_mode, self.st_size, self.st_uid = info.st_mode, info.st_size, 0

        monkeypatch.setattr(os, "fstat", lambda fd: Root(real(fd)))
        assert load_keys(path) == (key,)


class TestRestrictedFormat:
    @pytest.fixture
    def cipher(self) -> FernetCipher:
        return FernetCipher([generate_key()])

    NOTE = NoteId("server-ssh-access")

    def test_roundtrip_and_layout(self, cipher: FernetCipher) -> None:
        body = "---\ntitle: Acesso\n---\nconteúdo secreto".encode()
        data = encode_restricted(self.NOTE, body, cipher)
        assert data.startswith(b"---\nid: server-ssh-access\nsensitivity: restricted\nencryption: fernet-v1\n---\n")
        assert b"secreto" not in data
        assert decode_restricted(data, cipher) == (self.NOTE, body)

    def test_tokens_differ_between_encodings(self, cipher: FernetCipher) -> None:
        assert encode_restricted(self.NOTE, b"x", cipher) != encode_restricted(self.NOTE, b"x", cipher)

    def test_accepts_a_token_without_trailing_newline(self, cipher: FernetCipher) -> None:
        data = encode_restricted(self.NOTE, b"x", cipher)
        assert decode_restricted(data.removesuffix(b"\n"), cipher) == (self.NOTE, b"x")

    def test_swapping_ciphertexts_between_files_is_detected(self, cipher: FernetCipher) -> None:
        data_a = encode_restricted(NoteId("note-a"), b"secret of a", cipher)
        data_b = encode_restricted(NoteId("note-b"), b"secret of b", cipher)
        token_b = data_b.split(b"---\n", 2)[2]
        header_a = data_a[: len(data_a) - len(data_a.split(b"---\n", 2)[2])]
        with pytest.raises(DecryptionError):
            decode_restricted(header_a + token_b, cipher)

    def test_changing_the_header_id_is_detected(self, cipher: FernetCipher) -> None:
        data = encode_restricted(self.NOTE, b"x", cipher).replace(b"server-ssh-access", b"another-note")
        with pytest.raises(DecryptionError):
            decode_restricted(data, cipher)

    def test_wrong_key_and_tampered_token(self, cipher: FernetCipher) -> None:
        data = encode_restricted(self.NOTE, b"x", cipher)
        with pytest.raises(DecryptionError):
            decode_restricted(data, FernetCipher([generate_key()]))

    @pytest.mark.parametrize("offset", [8, 16, 24, 32, 48, 64, 80])
    def test_tampered_token_is_a_decryption_error(self, cipher: FernetCipher, offset: int) -> None:
        # Swap one base64 character of the token for another valid one: the format stays valid, the HMAC must fail.
        data = encode_restricted(self.NOTE, b"x", cipher)
        token_start = data.index(b"---\n", 4) + len(b"---\n")
        position = token_start + offset
        assert data[position : position + 1] not in (b"\n", b"=")
        tampered = bytearray(data)
        tampered[position] = ord("B") if tampered[position] != ord("B") else ord("C")
        with pytest.raises(DecryptionError):
            decode_restricted(bytes(tampered), cipher)

    def _valid(self, cipher: FernetCipher) -> bytes:
        return encode_restricted(self.NOTE, b"x", cipher)

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            (b"id: server-ssh-access\n", b"id: Bad_Id\n"),
            (b"id: server-ssh-access\n", b"id: \n"),
            (b"encryption: fernet-v1\n", b"encryption: fernet-v2\n"),
            (b"sensitivity: restricted\n", b"sensitivity: public\n"),
            (b"sensitivity: restricted\n", b"sensitivity: restricted\nextra: 1\n"),
            (b"sensitivity: restricted\nencryption: fernet-v1\n", b"encryption: fernet-v1\nsensitivity: restricted\n"),
            (b"sensitivity: restricted\n", b""),
            (b"id: server-ssh-access\n", b"id: server-ssh-access\nid: server-ssh-access\n"),
            (b"id: server-ssh-access\n", "id: servidor-ação\n".encode()),
            (b"id: server-ssh-access\n", b"id: server-ssh-access\r\n"),
            (b"id: server-ssh-access\n", b"id: \xff\n"),
            (b"---\nid", b"--- \nid"),
            (b"id: server-ssh-access\n", b"id: " + b"a" * 81 + b"\n"),
        ],
    )
    def test_malformed_headers_are_rejected(self, cipher: FernetCipher, old: bytes, new: bytes) -> None:
        data = self._valid(cipher).replace(old, new, 1)
        with pytest.raises(RestrictedFormatError) as raised:
            decode_restricted(data, cipher)
        message = str(raised.value)
        assert "a" * 81 not in message
        for fragment in ("Bad_Id", "fernet-v2", "extra", "servidor", "public"):
            assert fragment not in message
        assert "server-ssh-access" not in message

    @pytest.mark.parametrize("data", [b"", b"---\n", b"plain markdown\n", b"---\nid: a\n---\n"])
    def test_incomplete_files_are_rejected(self, cipher: FernetCipher, data: bytes) -> None:
        with pytest.raises(RestrictedFormatError):
            decode_restricted(data, cipher)

    def test_multiple_tokens_or_empty_token_are_rejected(self, cipher: FernetCipher) -> None:
        data = self._valid(cipher)
        with pytest.raises(RestrictedFormatError):
            decode_restricted(data + data.split(b"---\n", 2)[2], cipher)
        header = data[: len(data) - len(data.split(b"---\n", 2)[2])]
        with pytest.raises(RestrictedFormatError):
            decode_restricted(header, cipher)

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda t: t + b" ",
            lambda t: t[:20] + b" " + t[20:],
            lambda t: t[:20] + b"\x00" + t[20:],
            lambda t: t[:20] + "ç".encode() + t[20:],
            lambda t: t[:20] + b"\xff" + t[20:],
            lambda t: t[:20] + b"\t" + t[20:],
            lambda t: t + b"===",
        ],
    )
    def test_token_must_be_strict_urlsafe_base64(self, cipher: FernetCipher, mutate: Callable[[bytes], bytes]) -> None:
        data = self._valid(cipher)
        header, token = data.split(b"---\n", 2)[:2], data.split(b"---\n", 2)[2].rstrip(b"\n")
        prefix = b"---\n".join([b"", header[1]]) + b"---\n"
        with pytest.raises(RestrictedFormatError):
            decode_restricted(prefix + mutate(token) + b"\n", cipher)

    def test_decode_with_rotated_key_list_and_reencode_keeps_binding(self) -> None:
        old, new = generate_key(), generate_key()
        data = encode_restricted(self.NOTE, b"body", FernetCipher([old]))
        rotated_cipher = FernetCipher([new, old])
        assert decode_restricted(data, rotated_cipher) == (self.NOTE, b"body")
        assert decode_restricted(data, FernetCipher([new, old])) == (self.NOTE, b"body")
        with pytest.raises(DecryptionError):
            decode_restricted(data, FernetCipher([new]))
        header, token = data.rsplit(b"---\n", 1)
        rotated = header + b"---\n" + rotated_cipher.rotate(token.rstrip(b"\n")) + b"\n"
        assert decode_restricted(rotated, FernetCipher([new])) == (self.NOTE, b"body")
        swapped = rotated.replace(b"server-ssh-access", b"other-note")
        with pytest.raises(DecryptionError):
            decode_restricted(swapped, FernetCipher([new]))

    def test_oversize_is_rejected_both_ways(self, cipher: FernetCipher) -> None:
        with pytest.raises(RestrictedFormatError, match="larger"):
            decode_restricted(b"x" * (2 * 1024 * 1024 + 1), cipher)
        with pytest.raises(RestrictedFormatError, match="larger"):
            encode_restricted(self.NOTE, b"x" * (2 * 1024 * 1024), cipher)
