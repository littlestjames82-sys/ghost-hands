"""Shared error type for Ghost Hands."""


class HandsError(Exception):
    """Raised when a driver, decider, or runner cannot proceed honestly.

    Ghost Hands fails loudly with this error instead of guessing: a missing
    optional dependency, a missing API key in the environment, a malformed
    model reply, or an action that targets an element that does not exist.
    """
