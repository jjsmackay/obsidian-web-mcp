"""In-process write-guard seam: the pre-write counterpart of ``write_events``.

Lets an extension VETO a vault mutation before it touches disk -- a schema check, a
protected-path rule, a secrets scan. The write listener in ``write_events`` runs after
the write lands and so can only react; this seam runs before and can refuse. Core stays
a no-op callback list: with zero guards ``run_write_guards`` does nothing and the stock
server's behaviour is unchanged.

Unlike a listener, a guard FAILS CLOSED. A guard refuses a write by raising
``WriteRejected``; any other exception a guard raises is logged and ALSO refuses the
write, because a guard that crashes has not approved anything. A guard sees the
mutation as an operation plus the content that would land::

    from obsidian_vault_mcp.write_guards import WriteRejected, register_write_guard

    def guard(operation: str, path: str, content) -> None:
        # operation: "created" | "updated" | "moved"
        # path:      vault-relative path the write lands on (a move passes the
        #            destination)
        # content:   the full resulting text for "created"/"updated" (bytes for a
        #            binary write); the source file's text for a "moved" .md, else
        #            None
        if path.endswith(".md") and "status:" not in content:
            raise WriteRejected({"errors": ["missing status"]})

    register_write_guard(guard)

A rejected write returns the tool's usual error shape with ``"rejected": true`` and the
``WriteRejected`` payload merged in, so a client can read the guard's reasons and retry.
Guards are consulted once per file; a batch consults them per file and reports the
rejection in that file's result. Guards run synchronously in the request path, so keep
them cheap.
"""

import logging

logger = logging.getLogger(__name__)

# Registered at startup (before serving), consulted during request handling.
_write_guards: list = []


class WriteRejected(Exception):
    """Raised by a write guard to refuse a mutation.

    ``payload`` is a dict merged into the tool's error response (e.g. ``{"errors": [...]}``);
    a plain string becomes ``{"reason": <string>}``.
    """

    def __init__(self, payload=None):
        if payload is None:
            payload = {}
        elif not isinstance(payload, dict):
            payload = {"reason": str(payload)}
        self.payload = payload
        super().__init__(payload.get("reason") or "write rejected by guard")

    def response(self, path: str) -> dict:
        """The error response a tool returns for this rejection."""
        return {"error": str(self), "path": path, "rejected": True, **self.payload}


def register_write_guard(callback) -> None:
    """Register a ``callback(operation: str, path: str, content)`` consulted before a write.

    The callback refuses the write by raising ``WriteRejected``; returning normally approves
    it. Any other exception is treated as a refusal (fail closed). With no guards registered
    this seam is a true no-op on the stock server.
    """
    _write_guards.append(callback)


def has_write_guards() -> bool:
    """True when at least one guard is registered."""
    return bool(_write_guards)


def run_write_guards(operation: str, path: str, content) -> None:
    """Consult every guard in registration order; raise ``WriteRejected`` on the first refusal.

    Called by the core mutation tools after they have computed the content that would land
    and before they write it. Public so an extension that writes on its own path can gate
    those writes through the same guards.
    """
    for guard in _write_guards:
        try:
            guard(operation, path, content)
        except WriteRejected:
            raise
        except Exception as e:
            # A crashing guard has not approved the write: refuse rather than fall through.
            logger.error("Write guard %s failed for %s %s: %s", getattr(guard, "__name__", guard), operation, path, e)
            raise WriteRejected({"reason": f"write guard failed: {type(e).__name__}"}) from e
