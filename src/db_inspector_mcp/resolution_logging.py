"""Persistent workspace resolution diagnostics."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .server_runtime import BOOT_ID, configured_transport, server_pid

_lock = threading.Lock()


def _default_log_dir() -> Path:
    return Path.home() / ".db-inspector-mcp" / "logs"


def _log_path(name: str) -> Path:
    override = os.getenv("DB_MCP_LOG_DIR", "").strip()
    base = Path(override) if override else _default_log_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / name


@dataclass
class ResolutionInfo:
    """Outcome of resolving a tool call to a workspace root."""

    workspace_root: str
    resolved_via: str
    session_id: int
    client_roots: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    transport: str = field(default_factory=configured_transport)
    boot_id: str = field(default_factory=lambda: BOOT_ID)
    pid: int = field(default_factory=server_pid)
    mcp_session_repr: str | None = None
    mcp_session_id: str | None = None
    session_serial: str | None = None
    live_session_count: int | None = None
    tool: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def append_resolution_record(
    record: ResolutionInfo,
    *,
    spike: bool = False,
) -> None:
    """Append one JSON line to resolution.jsonl (and optionally spike.jsonl)."""
    payload = {
        **record.to_dict(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "workspace_resolution",
    }
    line = json.dumps(payload, default=str) + "\n"
    targets = [_log_path("resolution.jsonl")]
    if spike or os.getenv("DB_MCP_SPIKE_LOGGING", "").lower() == "true":
        targets.append(_log_path("spike.jsonl"))
    with _lock:
        for path in targets:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
