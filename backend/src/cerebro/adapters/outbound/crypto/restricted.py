"""Encrypted restricted-note file format (ADR-0008), as pure functions.

Layout (bytes)::

    ---\\n
    id: <note-id>\\n
    sensitivity: restricted\\n
    encryption: fernet-v1\\n
    ---\\n
    <Fernet token>\\n

The header is plaintext, so it is not authenticated by Fernet. To stop someone (or a bug) from swapping the token
of one restricted note into the file of another, the encrypted payload starts with ``<note-id>\\n`` followed by the
real content, and ``decode_restricted`` checks that this id equals the header id. Everything else (title,
summary, body) is inside the token.

The header is parsed by hand and strictly: exactly these three keys in exactly this order, a valid ``NoteId``,
the known ``encryption`` value, ASCII only. Anything else raises ``RestrictedFormatError`` with a message that
never echoes file content. Input is capped at ``MAX_RESTRICTED_BYTES`` before any parsing.
"""

from cerebro.adapters.outbound.crypto.errors import RestrictedFormatError
from cerebro.application.errors import DecryptionError
from cerebro.application.ports import Cipher
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import NoteId

ENCRYPTION_SCHEME = "fernet-v1"
MAX_RESTRICTED_BYTES = 2 * 1024 * 1024
_DELIMITER = b"---"
_ORDER_ERROR = "restricted note header must have the keys id, sensitivity, encryption in this order"


def encode_restricted(note_id: NoteId, plaintext: bytes, cipher: Cipher) -> bytes:
    """The file content for a restricted note whose full original text is ``plaintext``."""
    token = cipher.encrypt(note_id.value.encode("ascii") + b"\n" + plaintext)
    header = f"---\nid: {note_id.value}\nsensitivity: restricted\nencryption: {ENCRYPTION_SCHEME}\n---\n".encode(
        "ascii"
    )
    data = header + token + b"\n"
    if len(data) > MAX_RESTRICTED_BYTES:
        raise RestrictedFormatError(f"restricted note is larger than {MAX_RESTRICTED_BYTES} bytes")
    return data


def decode_restricted(data: bytes, cipher: Cipher) -> tuple[NoteId, bytes]:
    """The header id and the decrypted content. Raises ``RestrictedFormatError`` for a malformed file and
    ``DecryptionError`` for a wrong key, tampering or a token that belongs to another note id."""
    if len(data) > MAX_RESTRICTED_BYTES:
        raise RestrictedFormatError(f"restricted note is larger than {MAX_RESTRICTED_BYTES} bytes")
    parts = data.split(b"\n", 5)
    if len(parts) != 6:
        raise RestrictedFormatError("restricted note header is incomplete")
    delimiter, id_line, sensitivity_line, encryption_line, closing, rest = parts
    try:
        id_text = id_line.decode("ascii")
        sensitivity_text = sensitivity_line.decode("ascii")
        encryption_text = encryption_line.decode("ascii")
    except UnicodeDecodeError:
        raise RestrictedFormatError("restricted note header is not ASCII text") from None
    if delimiter != _DELIMITER or closing != _DELIMITER:
        raise RestrictedFormatError("restricted note header is not delimited by '---' lines")
    if not (
        id_text.startswith("id: ")
        and sensitivity_text.startswith("sensitivity: ")
        and encryption_text.startswith("encryption: ")
    ):
        raise RestrictedFormatError(_ORDER_ERROR)
    if sensitivity_text != "sensitivity: restricted":
        raise RestrictedFormatError("restricted note header must declare sensitivity: restricted")
    if encryption_text != f"encryption: {ENCRYPTION_SCHEME}":
        raise RestrictedFormatError("restricted note uses an unsupported encryption scheme")
    try:
        header_id = NoteId(id_text[len("id: ") :])
    except InvalidValueError:
        raise RestrictedFormatError("restricted note header has an invalid id") from None
    token = rest.removesuffix(b"\n")
    if not token or b"\n" in token or b"\r" in token:
        raise RestrictedFormatError("restricted note must contain exactly one encrypted token")
    payload = cipher.decrypt(token)
    prefix = header_id.value.encode("ascii") + b"\n"
    if not payload.startswith(prefix):
        raise DecryptionError("encrypted payload does not belong to this note id")
    return header_id, payload[len(prefix) :]
