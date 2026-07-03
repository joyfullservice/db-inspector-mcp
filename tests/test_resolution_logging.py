"""Tests for workspace resolution logging."""

import json
from pathlib import Path

from db_inspector_mcp.resolution_logging import ResolutionInfo, append_resolution_record


def test_append_resolution_record_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_MCP_LOG_DIR", str(tmp_path))
    record = ResolutionInfo(
        workspace_root=str(tmp_path / "project"),
        resolved_via="agent_supplied",
        session_id=42,
        client_roots=["C:/wrong"],
        tool="db_list_databases",
    )
    append_resolution_record(record)

    log_file = tmp_path / "resolution.jsonl"
    assert log_file.exists()
    line = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert line["event"] == "workspace_resolution"
    assert line["resolved_via"] == "agent_supplied"
    assert line["session_id"] == 42
    assert line["tool"] == "db_list_databases"
    assert "boot_id" in line
    assert "pid" in line
