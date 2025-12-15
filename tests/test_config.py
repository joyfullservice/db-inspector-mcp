"""Tests for configuration module."""

import os
from unittest.mock import patch

import pytest

from db_inspector_mcp.config import get_backend, load_config


def test_load_config_defaults():
    """Test that config loads with defaults."""
    with patch("db_inspector_mcp.config._load_env_files"):
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
            assert config["DB_MCP_QUERY_TIMEOUT_SECONDS"] == 30
            assert config["DB_MCP_ALLOW_DATA_ACCESS"] == "false"
            assert config["DB_MCP_VERIFY_READONLY"] == "true"


def test_load_config_from_env():
    """Test that config loads from environment variables."""
    with patch("db_inspector_mcp.config._load_env_files"):
        with patch.dict(
            os.environ,
            {
                "DB_MCP_DATABASE": "postgres",
                "DB_MCP_CONNECTION_STRING": "dbname=test",
                "DB_MCP_QUERY_TIMEOUT_SECONDS": "60",
            },
            clear=True,
        ):
            config = load_config()
            assert config["DB_MCP_DATABASE"] == "postgres"
            assert config["DB_MCP_CONNECTION_STRING"] == "dbname=test"
            assert config["DB_MCP_QUERY_TIMEOUT_SECONDS"] == 60


def test_get_backend_missing_backend():
    """Test that missing backend raises error."""
    with patch("db_inspector_mcp.config._load_env_files"):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="DB_MCP_DATABASE"):
                get_backend()


def test_get_backend_missing_connection_string():
    """Test that missing connection string raises error."""
    with patch("db_inspector_mcp.config._load_env_files"):
        with patch.dict(os.environ, {"DB_MCP_DATABASE": "sqlserver"}, clear=True):
            with pytest.raises(ValueError, match="DB_MCP_CONNECTION_STRING"):
                get_backend()


def test_get_backend_invalid_backend():
    """Test that invalid backend raises error."""
    with patch("db_inspector_mcp.config._load_env_files"):
        with patch.dict(
            os.environ,
            {"DB_MCP_DATABASE": "invalid", "DB_MCP_CONNECTION_STRING": "test"},
            clear=True,
        ):
            with pytest.raises(ValueError, match="Unsupported backend"):
                get_backend()


def test_get_backend_sqlserver():
    """Test that SQL Server backend is created."""
    with patch("db_inspector_mcp.config._load_env_files"):
        with patch.dict(
            os.environ,
            {"DB_MCP_DATABASE": "sqlserver", "DB_MCP_CONNECTION_STRING": "test"},
            clear=True,
        ):
            backend = get_backend()
            assert backend.__class__.__name__ == "MSSQLBackend"


def test_get_backend_postgres():
    """Test that PostgreSQL backend is created."""
    with patch("db_inspector_mcp.config._load_env_files"):
        with patch.dict(
            os.environ,
            {"DB_MCP_DATABASE": "postgres", "DB_MCP_CONNECTION_STRING": "dbname=test"},
            clear=True,
        ):
            backend = get_backend()
            assert backend.__class__.__name__ == "PostgresBackend"


def test_get_backend_access_odbc():
    """Test that Access ODBC backend is created."""
    with patch.dict(
        os.environ,
        {
            "DB_MCP_DATABASE": "access_odbc",
            "DB_MCP_CONNECTION_STRING": "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;",
        },
        clear=True,
    ):
        from db_inspector_mcp.config import _create_backend
        backend = _create_backend("access_odbc", "test_connection_string", 30)
        assert backend.__class__.__name__ == "AccessODBCBackend"


def test_get_backend_access_com():
    """Test that Access COM backend is created."""
    with patch.dict(
        os.environ,
        {
            "DB_MCP_DATABASE": "access_com",
            "DB_MCP_CONNECTION_STRING": "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;",
        },
        clear=True,
    ):
        from db_inspector_mcp.config import _create_backend
        with patch('db_inspector_mcp.backends.access_com.win32com.client'):
            backend = _create_backend("access_com", "test_connection_string", 30)
            assert backend.__class__.__name__ == "AccessCOMBackend"

