"""Encryption of restricted notes: Fernet cipher, key loading and the encrypted file format (ADR-0008)."""

from cerebro.adapters.outbound.crypto.errors import InvalidKeyError, KeyFileError, RestrictedFormatError
from cerebro.adapters.outbound.crypto.fernet import FernetCipher, generate_key
from cerebro.adapters.outbound.crypto.keys import load_keys
from cerebro.adapters.outbound.crypto.restricted import decode_restricted, encode_restricted

__all__ = [
    "FernetCipher",
    "InvalidKeyError",
    "KeyFileError",
    "RestrictedFormatError",
    "decode_restricted",
    "encode_restricted",
    "generate_key",
    "load_keys",
]
