"""``TokenHash``: the SHA-256 digest of an agent token secret, kept from leaking by construction."""

import hmac
import re
from typing import Final, NoReturn, Self, final

from cerebro.domain.errors import InvalidValueError

_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")
REDACTED: Final = "<redacted>"


@final
class TokenHash:
    """A validated lowercase SHA-256 hex digest that never shows itself.

    ``repr``, ``str`` and ``format`` (so f-strings and logging) give ``<redacted>``. It is deliberately **not** a
    dataclass: ``dataclasses.asdict`` would recurse into a dataclass and copy the hex out, whereas for this class
    it falls back to ``copy.deepcopy``, which returns the same immutable object, whose representation is
    redacted. Pickling is refused so the digest cannot be serialised by accident. The only way to read the hex is
    the explicit ``hexdigest`` property, meant for the stores that persist it.

    Equality is constant-time (``hmac.compare_digest``). ``AgentToken`` excludes this field from its own
    ``__eq__``/``__hash__``.
    """

    __slots__ = ("_hexdigest",)
    _hexdigest: str

    def __init__(self, hexdigest: str) -> None:
        if not isinstance(hexdigest, str) or _SHA256_HEX.fullmatch(hexdigest) is None:
            # Never echo the received value: it may be a plaintext secret passed by mistake.
            raise InvalidValueError("token_hash", "expected a lowercase SHA-256 hex digest")
        object.__setattr__(self, "_hexdigest", hexdigest)

    @property
    def hexdigest(self) -> str:
        return self._hexdigest

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("TokenHash is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("TokenHash is immutable")

    def __repr__(self) -> str:
        return REDACTED

    def __str__(self) -> str:
        return REDACTED

    def __format__(self, format_spec: str) -> str:
        return REDACTED

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TokenHash):
            return NotImplemented
        return hmac.compare_digest(self._hexdigest.encode(), other._hexdigest.encode())

    def __hash__(self) -> int:
        return hash(self._hexdigest)

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: object) -> Self:
        return self

    def __reduce__(self) -> NoReturn:
        raise TypeError("TokenHash cannot be pickled")

    def __getstate__(self) -> NoReturn:
        raise TypeError("TokenHash cannot be pickled")
