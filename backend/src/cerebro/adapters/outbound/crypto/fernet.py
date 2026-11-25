"""``Cipher`` port over Fernet (authenticated symmetric encryption) with ``MultiFernet`` key rotation."""

from collections.abc import Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from cerebro.adapters.outbound.crypto.errors import InvalidKeyError
from cerebro.application.errors import DecryptionError

_FAILURE = "payload could not be decrypted (wrong key or tampered data)"


def generate_key() -> bytes:
    """A new random Fernet key (urlsafe base64, 32 bytes of entropy)."""
    return Fernet.generate_key()


class FernetCipher:
    """The first key encrypts; every key decrypts. Keys are held only inside ``MultiFernet`` and never shown."""

    __slots__ = ("_multi", "_size")

    def __init__(self, keys: Sequence[bytes]) -> None:
        if len(keys) == 0:
            raise InvalidKeyError("at least one Fernet key is required")
        fernets: list[Fernet] = []
        for index, key in enumerate(keys, start=1):
            try:
                fernets.append(Fernet(key))
            except ValueError, TypeError:
                raise InvalidKeyError(f"key #{index} is not a valid Fernet key") from None
        self._multi = MultiFernet(fernets)
        self._size = len(fernets)

    def __repr__(self) -> str:
        return f"FernetCipher(keys={self._size})"

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._multi.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        try:
            return self._multi.decrypt(ciphertext)
        except InvalidToken, TypeError, ValueError:
            raise DecryptionError(_FAILURE) from None

    def rotate(self, ciphertext: bytes) -> bytes:
        """Re-encrypt a token under the first key (for the future ``re-encrypt`` command)."""
        try:
            return self._multi.rotate(ciphertext)
        except InvalidToken, TypeError, ValueError:
            raise DecryptionError(_FAILURE) from None
