"""Tests for configuration module."""

import os
from unittest.mock import patch

import pytest

from db_inspector_mcp.config import get_backend, load_config


def test_load_config_defaults():
    """Test that config loads with defaults."""
    with patch.dict(os.environ, {}, clear=True):
        config = load_config()
        assert config["DB_QUERY_TIMEOUT_SECONDS"] == 30
        assert config["DB_ALLOW_DATA_ACCESS"] == "false"
        assert config["DB_VERIFY_READONLY"] == "true"


def test_load_config_from_env():
    """Test that config loads from environment variables."""
    with patch.dict(
        os.environ,
        {
            "DB_BACKEND": "postgres",
            "DB_CONNECTION_STRING": "dbname=test",
            "DB_QUERY_TIMEOUT_SECONDS": "60",
        },
        clear=True,
    ):
        config = load_config()
        assert config["DB_BACKEND"] == "postgres"
        assert config["DB_CONNECTION_STRING"] == "dbname=test"
        assert config["DB_QUERY_TIMEOUT_SECONDS"] == 60


def test_get_backend_missing_backend():
    """Test that missing backend raises error."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="DB_BACKEND"):
            get_backend()


def test_get_backend_missing_connection_string():
    """Test that missing connection string raises error."""
    with patch.dict(os.environ, {"DB_BACKEND": "sqlserver"}, clear=True):
        with pytest.raises(ValueError, match="DB_CONNECTION_STRING"):
            get_backend()


def test_get_backend_invalid_backend():
    """Test that invalid backend raises error."""
    with patch.dict(
        os.environ,
        {"DB_BACKEND": "invalid", "DB_CONNECTION_STRING": "test"},
        clear=True,
    ):
        with pytest.raises(ValueError, match="Unsupported backend"):
            get_backend()


def test_get_backend_sqlserver():
    """Test that SQL Server backend is created."""
    with patch.dict(
        os.environ,
        {"DB_BACKEND": "sqlserver", "DB_CONNECTION_STRING": "test"},
        clear=True,
    ):
        backend = get_backend()
        assert backend.__class__.__name__ == "MSSQLBackend"


def test_get_backend_postgres():
    """Test that PostgreSQL backend is created."""
    with patch.dict(
        os.environ,
        {"DB_BACKEND": "postgres", "DB_CONNECTION_STRING": "dbname=test"},
        clear=True,
    ):
        backend = get_backend()
        assert backend.__class__.__name__ == "PostgresBackend"

