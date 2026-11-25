"""Errors of the crypto adapter. Messages never contain key material, plaintext or ciphertext."""


class InvalidKeyError(ValueError):
    """Key material is missing or is not a valid Fernet key."""


class KeyFileError(InvalidKeyError):
    """A key file is unsafe (permissions, type) or malformed. The message names the path, never the contents."""


class RestrictedFormatError(ValueError):
    """A restricted-note file does not follow the ``fernet-v1`` format of ADR-0008."""
