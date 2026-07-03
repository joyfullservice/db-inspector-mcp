"""Process-level runtime metadata for MCP server instances."""

from __future__ import annotations

import os
import weakref
from typing import Any
from uuid import uuid4

# Stable for the lifetime of this server process (used in spike / resolution logs).
BOOT_ID: str = str(uuid4())

# GC-safe per-session identity. Keyed on the live session object so a session
# keeps ONE serial for its whole lifetime — unlike id(), which is a memory
# address that can be recycled after GC and produce false "same session"
# matches across calls (the flaw in the first HTTP spike).
_session_serials: "weakref.WeakKeyDictionary[Any, str]" = weakref.WeakKeyDictionary()
_live_sessions: "weakref.WeakSet[Any]" = weakref.WeakSet()


def session_identity(session: Any) -> dict[str, Any]:
    """Return a stable per-session serial, newness, and live session count.

    - ``serial``: stable uuid for this session object's lifetime (GC-safe).
    - ``is_new``: True the first time this object is seen.
    - ``live_count``: number of distinct session objects currently alive.

    Two Cursor windows sharing one session => same serial and live_count 1.
    Distinct per-window sessions => different serials and live_count >= 2.
    """
    try:
        is_new = session not in _session_serials
        if is_new:
            _session_serials[session] = uuid4().hex
        _live_sessions.add(session)
        return {
            "serial": _session_serials[session],
            "is_new": is_new,
            "live_count": len(_live_sessions),
        }
    except TypeError:
        # Session object is not weak-referenceable; fall back to id().
        return {"serial": None, "is_new": None, "live_count": None}


def read_mcp_session_id(ctx: Any) -> dict[str, str | None]:
    """Read transport-level mcp-session-id / protocol headers if present.

    Populated only on the streamable-HTTP transport, where the client echoes
    ``mcp-session-id`` on every request after initialize. Returns ``None``
    values on stdio (no per-request HTTP headers).
    """
    result: dict[str, str | None] = {
        "mcp_session_id": None,
        "mcp_protocol_version": None,
    }
    try:
        request = ctx.request_context.request
    except Exception:
        return result
    headers = getattr(request, "headers", None)
    if headers is None:
        return result
    try:
        result["mcp_session_id"] = headers.get("mcp-session-id")
        result["mcp_protocol_version"] = headers.get("mcp-protocol-version")
    except Exception:
        pass
    return result


def server_pid() -> int:
    """Return the OS process id of this server."""
    return os.getpid()


def configured_transport() -> str:
    """Return 'stdio' (the only supported transport).

    Streamable-HTTP was tested during the 2026-06-23 spike and found to offer
    no per-window session isolation under Cursor's shared MCP process. The
    transport plumbing was removed; see DECISIONS.md and
    docs/CURSOR_SHARED_MCP.md for the full investigation.
    """
    return "stdio"
