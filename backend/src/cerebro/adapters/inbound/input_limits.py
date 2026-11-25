"""Input byte budgets shared by inbound adapters."""

MAX_INPUT_BYTES = 65536
MAX_QUERY_BYTES = 1024
MAX_FIELD_BYTES = 256


def bounded_text(text: str, maximum: int) -> str:
    if len(text.encode("utf-8")) > maximum:
        raise ValueError("field exceeds byte limit")
    return text
