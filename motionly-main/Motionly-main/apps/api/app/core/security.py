from secrets import compare_digest


def constant_time_token_matches(provided: str | None, expected: str | None) -> bool:
    """Compare configured service tokens without content-dependent early exits."""
    if provided is None or expected is None:
        return False
    return compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
