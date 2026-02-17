"""Tests for configuration module."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from db_inspector_mcp.config import _find_project_root, get_backend, load_config


class TestFindProjectRoot:
    """Tests for _find_project_root with DB_MCP_PROJECT_DIR and CWD-based detection."""

    def test_explicit_project_dir(self, tmp_path):
        """DB_MCP_PROJECT_DIR is used when set and the directory exists."""
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        with patch.dict(os.environ, {"DB_MCP_PROJECT_DIR": str(project_dir)}, clear=False):
            result = _find_project_root()
            assert result == project_dir.resolve()

    def test_explicit_project_dir_nonexistent(self, tmp_path, capsys):
        """A warning is printed and fallback is used when the directory doesn't exist."""
        bad_path = str(tmp_path / "does_not_exist")
        with patch.dict(os.environ, {"DB_MCP_PROJECT_DIR": bad_path}, clear=False):
            result = _find_project_root()
            assert result != Path(bad_path).resolve()
            captured = capsys.readouterr()
            assert "non-existent directory" in captured.err

    def test_no_explicit_project_dir(self):
        """Without DB_MCP_PROJECT_DIR, normal search behaviour is used."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DB_MCP_PROJECT_DIR", None)
            result = _find_project_root()
            assert isinstance(result, Path)

    def test_finds_env_from_cwd(self, tmp_path, monkeypatch):
        """When CWD contains a .env file, it is detected as the project root."""
        (tmp_path / ".env").write_text("DB_MCP_DATABASE=sqlserver\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)
        result = _find_project_root()
        assert result == tmp_path.resolve()

    def test_finds_env_in_parent(self, tmp_path, monkeypatch):
        """When CWD is a subdirectory, the search walks upward to find .env."""
        (tmp_path / ".env").write_text("DB_MCP_DATABASE=sqlserver\n")
        subdir = tmp_path / "src" / "subpkg"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)
        result = _find_project_root()
        assert result == tmp_path.resolve()

    def test_explicit_overrides_cwd(self, tmp_path, monkeypatch):
        """DB_MCP_PROJECT_DIR takes precedence over CWD-based search."""
        cwd_project = tmp_path / "cwd_project"
        cwd_project.mkdir()
        (cwd_project / ".env").write_text("DB_MCP_DATABASE=postgres\n")

        explicit_project = tmp_path / "explicit_project"
        explicit_project.mkdir()

        monkeypatch.chdir(cwd_project)
        monkeypatch.setenv("DB_MCP_PROJECT_DIR", str(explicit_project))
        result = _find_project_root()
        assert result == explicit_project.resolve()


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

