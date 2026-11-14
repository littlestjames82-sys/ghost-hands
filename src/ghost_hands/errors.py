"""Shared error type for Ghost Hands."""


class HandsError(Exception):
    """Raised when a driver, decider, or runner cannot proceed honestly.

    Ghost Hands fails loudly with this error instead of guessing: a missing
    optional dependency, a missing API key in the environment, a malformed
    model reply, or an action that targets an element that does not exist.
    """


class StaleTargetError(HandsError):
    """v1.12 (P0-1): the target node is *gone* — disconnected, hidden, or
    no longer resolvable — as opposed to merely re-pointed. Raised by the
    Chromium driver's pre-action revalidation. The message always carries
    the "target missing" marker so the Runner's existing heal path treats
    it exactly like the positional drivers' missing-target errors: the
    heal may re-resolve the element by identity on a fresh perception."""


class UnhealableTargetError(HandsError):
    """v1.12 (P0-1): the target's identity was lost on an action that must
    never be re-resolved to a different element — one bearing a secret
    placeholder, or an upload (``set_file``). The approval for such an
    action was bound to the element the approver saw; healing it onto
    another node would silently change what was approved. The Runner
    raises this instead of healing, so the run stops and the action can
    be re-approved against a fresh perception."""
