"""Tests for server runtime metadata."""

import os

from db_inspector_mcp.server_runtime import (
    BOOT_ID,
    configured_transport,
    read_mcp_session_id,
    server_pid,
    session_identity,
)


class _FakeSession:
    """Weak-referenceable stand-in for an MCP ServerSession."""


def test_boot_id_is_non_empty():
    assert BOOT_ID
    assert len(BOOT_ID) >= 32


def test_server_pid_matches_os():
    assert server_pid() == os.getpid()


def test_configured_transport_defaults_stdio(monkeypatch):
    monkeypatch.delenv("DB_MCP_TRANSPORT", raising=False)
    assert configured_transport() == "stdio"


def test_configured_transport_always_stdio(monkeypatch):
    """HTTP transport was removed after the spike; transport is always stdio."""
    monkeypatch.setenv("DB_MCP_TRANSPORT", "http")
    assert configured_transport() == "stdio"


def test_session_identity_stable_per_object():
    a = _FakeSession()
    b = _FakeSession()

    first = session_identity(a)
    again = session_identity(a)
    other = session_identity(b)

    assert first["serial"] == again["serial"]
    assert first["is_new"] is True
    assert again["is_new"] is False
    assert other["serial"] != first["serial"]
    assert other["live_count"] >= 2


def test_read_mcp_session_id_returns_none_on_stdio():
    class _StdioCtx:
        @property
        def request_context(self):
            raise ValueError("Context is not available outside of a request")

    assert read_mcp_session_id(_StdioCtx()) == {
        "mcp_session_id": None,
        "mcp_protocol_version": None,
    }


def test_read_mcp_session_id_reads_http_headers():
    class _Headers:
        def __init__(self, data):
            self._data = data

        def get(self, key):
            return self._data.get(key)

    class _Request:
        headers = _Headers(
            {"mcp-session-id": "abc123", "mcp-protocol-version": "2025-03-26"},
        )

    class _ReqCtx:
        request = _Request()

    class _HttpCtx:
        request_context = _ReqCtx()

    result = read_mcp_session_id(_HttpCtx())
    assert result["mcp_session_id"] == "abc123"
    assert result["mcp_protocol_version"] == "2025-03-26"
