"""Tests for configuration module."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import db_inspector_mcp.config as config_module
from db_inspector_mcp.config import (
    _check_env_reload,
    _find_project_root,
    _record_env_mtimes,
    _resolve_connection_string_paths,
    _snapshot_backend_env,
    get_backend,
    load_config,
)


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


class TestResolveConnectionStringPaths:
    """Tests for _resolve_connection_string_paths relative-path resolution."""

    def test_absolute_dbq_unchanged(self, tmp_path):
        """An absolute DBQ= path is returned as-is."""
        conn = r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\data\my.accdb;"
        result = _resolve_connection_string_paths(conn, "access_odbc", tmp_path)
        assert result == conn

    def test_relative_dbq_resolved(self, tmp_path):
        """A relative DBQ= path is resolved against base_dir."""
        (tmp_path / "my.accdb").touch()
        conn = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=my.accdb;"
        result = _resolve_connection_string_paths(conn, "access_odbc", tmp_path)
        expected_path = str((tmp_path / "my.accdb").resolve())
        assert f"DBQ={expected_path};" in result
        assert "Driver=" in result

    def test_relative_dbq_dot_slash(self, tmp_path):
        """DBQ=.\\subdir\\db.accdb is resolved correctly."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "db.accdb").touch()
        conn = r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=.\subdir\db.accdb;"
        result = _resolve_connection_string_paths(conn, "access_odbc", tmp_path)
        expected_path = str((tmp_path / "subdir" / "db.accdb").resolve())
        assert f"DBQ={expected_path};" in result

    def test_relative_dbq_parent_dir(self, tmp_path):
        """DBQ=..\\db.accdb is resolved correctly."""
        (tmp_path / "db.accdb").touch()
        child = tmp_path / "project"
        child.mkdir()
        conn = r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=..\db.accdb;"
        result = _resolve_connection_string_paths(conn, "access_odbc", child)
        expected_path = str((tmp_path / "db.accdb").resolve())
        assert f"DBQ={expected_path};" in result

    def test_relative_dbq_forward_slashes(self, tmp_path):
        """Forward slashes in DBQ= are handled on all platforms."""
        subdir = tmp_path / "data"
        subdir.mkdir()
        (subdir / "my.accdb").touch()
        conn = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=data/my.accdb;"
        result = _resolve_connection_string_paths(conn, "access_odbc", tmp_path)
        expected_path = str((tmp_path / "data" / "my.accdb").resolve())
        assert f"DBQ={expected_path};" in result

    def test_bare_relative_path_resolved(self, tmp_path):
        """A bare relative file path (no DBQ=, no Driver=) is resolved."""
        (tmp_path / "test.accdb").touch()
        result = _resolve_connection_string_paths("test.accdb", "access_odbc", tmp_path)
        assert result == str((tmp_path / "test.accdb").resolve())

    def test_bare_absolute_path_unchanged(self, tmp_path):
        """A bare absolute file path is returned as-is."""
        conn = r"C:\full\path\to\database.accdb"
        result = _resolve_connection_string_paths(conn, "access_odbc", tmp_path)
        assert result == conn

    def test_non_access_backend_unchanged(self, tmp_path):
        """Non-Access backends pass through without modification."""
        conn = "Server=localhost;Database=mydb;"
        assert _resolve_connection_string_paths(conn, "sqlserver", tmp_path) == conn
        assert _resolve_connection_string_paths(conn, "postgres", tmp_path) == conn

    def test_access_com_resolved(self, tmp_path):
        """Resolution works for access_com backend too."""
        (tmp_path / "my.accdb").touch()
        conn = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=my.accdb;"
        result = _resolve_connection_string_paths(conn, "access_com", tmp_path)
        expected_path = str((tmp_path / "my.accdb").resolve())
        assert f"DBQ={expected_path};" in result

    def test_missing_file_warning(self, tmp_path, capsys):
        """A warning is emitted when the resolved path does not exist."""
        conn = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=nonexistent.accdb;"
        _resolve_connection_string_paths(conn, "access_odbc", tmp_path)
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_existing_file_no_warning(self, tmp_path, capsys):
        """No warning is emitted when the resolved path exists."""
        (tmp_path / "exists.accdb").touch()
        conn = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=exists.accdb;"
        _resolve_connection_string_paths(conn, "access_odbc", tmp_path)
        captured = capsys.readouterr()
        assert "does not exist" not in captured.err

    def test_dbq_case_insensitive(self, tmp_path):
        """DBQ matching is case-insensitive (dbq=, Dbq=, etc.)."""
        (tmp_path / "my.accdb").touch()
        conn = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};dbq=my.accdb;"
        result = _resolve_connection_string_paths(conn, "access_odbc", tmp_path)
        expected_path = str((tmp_path / "my.accdb").resolve())
        assert expected_path in result


class TestEnvHotReload:
    """Tests for .env file hot-reload via mtime checking."""

    @pytest.fixture(autouse=True)
    def _reset_config_state(self):
        """Reset module-level config state before and after each test."""
        config_module._env_loaded = False
        config_module._is_reload = False
        config_module._project_root = None
        config_module._env_mtimes.clear()
        yield
        config_module._env_loaded = False
        config_module._is_reload = False
        config_module._project_root = None
        config_module._env_mtimes.clear()

    def test_unchanged_mtime_does_not_reload(self, tmp_path, monkeypatch):
        """When .env mtime is unchanged, _check_env_reload returns False."""
        env_file = tmp_path / ".env"
        env_file.write_text("DB_MCP_ALLOW_DATA_ACCESS=false\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)

        config_module._env_loaded = True
        config_module._project_root = tmp_path
        _record_env_mtimes()

        assert not _check_env_reload()
        assert config_module._env_loaded is True

    def test_mtime_change_triggers_reload(self, tmp_path, monkeypatch):
        """When .env mtime changes, _check_env_reload resets _env_loaded."""
        env_file = tmp_path / ".env"
        env_file.write_text("DB_MCP_ALLOW_DATA_ACCESS=false\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)

        config_module._env_loaded = True
        config_module._project_root = tmp_path
        _record_env_mtimes()

        # Simulate a file edit by bumping the mtime forward
        original_mtime = env_file.stat().st_mtime
        os.utime(str(env_file), (original_mtime + 1, original_mtime + 1))

        assert _check_env_reload() is True
        assert config_module._env_loaded is False
        assert config_module._is_reload is True

    def test_permission_flag_hot_reload(self, tmp_path, monkeypatch):
        """Editing a permission flag in .env is picked up by load_config."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DB_MCP_DATABASE=sqlserver\n"
            "DB_MCP_CONNECTION_STRING=test\n"
            "DB_MCP_ALLOW_DATA_ACCESS=false\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)

        # First load
        config = load_config()
        assert config["DB_MCP_ALLOW_DATA_ACCESS"] == "false"

        # Edit .env and bump mtime
        original_mtime = env_file.stat().st_mtime
        env_file.write_text(
            "DB_MCP_DATABASE=sqlserver\n"
            "DB_MCP_CONNECTION_STRING=test\n"
            "DB_MCP_ALLOW_DATA_ACCESS=true\n"
        )
        os.utime(str(env_file), (original_mtime + 1, original_mtime + 1))

        # Second load should pick up the change
        config = load_config()
        assert config["DB_MCP_ALLOW_DATA_ACCESS"] == "true"

    def test_deleted_env_file_does_not_crash(self, tmp_path, monkeypatch):
        """Deleting .env after initial load does not crash the mtime check."""
        env_file = tmp_path / ".env"
        env_file.write_text("DB_MCP_ALLOW_DATA_ACCESS=false\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)

        config_module._env_loaded = True
        config_module._project_root = tmp_path
        _record_env_mtimes()
        assert len(config_module._env_mtimes) == 1

        # Delete the file
        env_file.unlink()

        # Should not crash — just return False (file gone, OSError caught)
        assert _check_env_reload() is False

    def test_env_local_mtime_change_triggers_reload(self, tmp_path, monkeypatch):
        """.env.local mtime change also triggers a reload."""
        env_file = tmp_path / ".env"
        env_file.write_text("DB_MCP_ALLOW_DATA_ACCESS=false\n")
        env_local = tmp_path / ".env.local"
        env_local.write_text("DB_MCP_ALLOW_PREVIEW=false\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)

        config_module._env_loaded = True
        config_module._project_root = tmp_path
        _record_env_mtimes()
        assert len(config_module._env_mtimes) == 2

        # Only touch .env.local
        original_mtime = env_local.stat().st_mtime
        os.utime(str(env_local), (original_mtime + 1, original_mtime + 1))

        assert _check_env_reload() is True

    def test_snapshot_backend_env(self, monkeypatch):
        """_snapshot_backend_env captures database-related env vars."""
        monkeypatch.setenv("DB_MCP_DATABASE", "sqlserver")
        monkeypatch.setenv("DB_MCP_CONNECTION_STRING", "test_conn")
        monkeypatch.setenv("DB_MCP_LEGACY_DATABASE", "postgres")
        monkeypatch.setenv("DB_MCP_LEGACY_CONNECTION_STRING", "legacy_conn")
        monkeypatch.setenv("DB_MCP_ALLOW_DATA_ACCESS", "true")

        snap = _snapshot_backend_env()
        assert "DB_MCP_DATABASE" in snap
        assert "DB_MCP_CONNECTION_STRING" in snap
        assert "DB_MCP_LEGACY_DATABASE" in snap
        assert "DB_MCP_LEGACY_CONNECTION_STRING" in snap
        # Permission flags should NOT be captured
        assert "DB_MCP_ALLOW_DATA_ACCESS" not in snap

    def test_record_env_mtimes_stores_existing_files(self, tmp_path):
        """_record_env_mtimes stores mtimes for files that exist."""
        (tmp_path / ".env").write_text("X=1\n")
        config_module._project_root = tmp_path

        _record_env_mtimes()
        env_path = str(tmp_path / ".env")
        assert env_path in config_module._env_mtimes

        # .env.local doesn't exist, should not be tracked
        env_local_path = str(tmp_path / ".env.local")
        assert env_local_path not in config_module._env_mtimes

    def test_no_mtimes_means_no_reload(self):
        """With empty mtimes dict, _check_env_reload returns False."""
        config_module._env_loaded = True
        config_module._env_mtimes.clear()
        assert _check_env_reload() is False

    def test_hot_reload_resets_logging(self, tmp_path, monkeypatch):
        """reset_logging() is called when .env is reloaded."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DB_MCP_DATABASE=sqlserver\n"
            "DB_MCP_CONNECTION_STRING=test\n"
            "DB_MCP_ENABLE_LOGGING=false\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)

        load_config()

        original_mtime = env_file.stat().st_mtime
        env_file.write_text(
            "DB_MCP_DATABASE=sqlserver\n"
            "DB_MCP_CONNECTION_STRING=test\n"
            "DB_MCP_ENABLE_LOGGING=true\n"
        )
        os.utime(str(env_file), (original_mtime + 1, original_mtime + 1))

        with patch("db_inspector_mcp.usage_logging.reset_logging") as mock_reset:
            load_config()
            mock_reset.assert_called_once()

    def test_hot_reload_resets_logging_without_backend_change(self, tmp_path, monkeypatch):
        """reset_logging() is called even when only logging config changes
        (no backend connection string changes).
        """
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DB_MCP_DATABASE=sqlserver\n"
            "DB_MCP_CONNECTION_STRING=test\n"
            "DB_MCP_ENABLE_LOGGING=false\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)

        load_config()

        original_mtime = env_file.stat().st_mtime
        env_file.write_text(
            "DB_MCP_DATABASE=sqlserver\n"
            "DB_MCP_CONNECTION_STRING=test\n"
            "DB_MCP_ENABLE_LOGGING=true\n"
        )
        os.utime(str(env_file), (original_mtime + 1, original_mtime + 1))

        with patch("db_inspector_mcp.usage_logging.reset_logging") as mock_reset:
            config = load_config()
            mock_reset.assert_called_once()
            assert config["DB_MCP_ENABLE_LOGGING"] is True

    def test_hot_reload_backend_change_clears_registry(self, tmp_path, monkeypatch):
        """Backend env changes trigger registry.clear() during hot-reload."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DB_MCP_DATABASE=sqlserver\n"
            "DB_MCP_CONNECTION_STRING=conn1\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)

        # Initial load establishes baseline env snapshot and mtimes.
        load_config()

        original_mtime = env_file.stat().st_mtime
        env_file.write_text(
            "DB_MCP_DATABASE=postgres\n"
            "DB_MCP_CONNECTION_STRING=conn2\n"
        )
        os.utime(str(env_file), (original_mtime + 1, original_mtime + 1))

        mock_registry = MagicMock()
        with patch("db_inspector_mcp.config.get_registry", return_value=mock_registry), \
             patch("db_inspector_mcp.config.initialize_backends"), \
             patch("db_inspector_mcp.main._verify_readonly"):
            load_config()

        mock_registry.clear.assert_called_once()

    def test_db_tool_calls_load_config(self, tmp_path, monkeypatch):
        """A tool decorated with @db_tool calls load_config() before
        executing the tool body.
        """
        env_file = tmp_path / ".env"
        env_file.write_text("DB_MCP_DATABASE=sqlserver\nDB_MCP_CONNECTION_STRING=test\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)

        from unittest.mock import MagicMock

        with patch("db_inspector_mcp.tools.load_config") as mock_load, \
             patch("db_inspector_mcp.tools.get_registry") as mock_reg:
            registry = MagicMock()
            backend = MagicMock()
            backend.count_query_results.return_value = 42
            backend.sql_dialect = "mssql"
            registry.get.return_value = backend
            registry.list_backends.return_value = ["default"]
            mock_reg.return_value = registry

            from db_inspector_mcp.tools import db_count_query_results
            db_count_query_results("SELECT 1")
            mock_load.assert_called()

