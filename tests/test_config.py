"""Tests for configuration module."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from db_inspector_mcp.config import (
    _find_project_root,
    _resolve_connection_string_paths,
    build_registry_from_env,
    config_from_env,
    env_files_changed,
    get_backend,
    load_config,
    parse_workspace_env,
    record_env_mtimes,
)
from db_inspector_mcp.backends.registry import BackendRegistry


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


def test_config_from_env_defaults():
    """Test that config_from_env uses defaults for missing keys."""
    config = config_from_env({})
    assert config["DB_MCP_QUERY_TIMEOUT_SECONDS"] == 30
    assert config["DB_MCP_ALLOW_DATA_ACCESS"] == "false"
    assert config["DB_MCP_VERIFY_READONLY"] == "true"


def test_load_config_from_env(tmp_path, monkeypatch):
    """Test that load_config reads from .env via parse_workspace_env."""
    (tmp_path / ".env").write_text(
        "DB_MCP_DATABASE=postgres\n"
        "DB_MCP_CONNECTION_STRING=dbname=test\n"
        "DB_MCP_QUERY_TIMEOUT_SECONDS=60\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)
    config = load_config()
    assert config["DB_MCP_DATABASE"] == "postgres"
    assert config["DB_MCP_CONNECTION_STRING"] == "dbname=test"
    assert config["DB_MCP_QUERY_TIMEOUT_SECONDS"] == 60


def test_get_backend_missing_backend(tmp_path, monkeypatch):
    """Test that missing backend raises error."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)
    with pytest.raises(ValueError, match="DB_MCP_DATABASE"):
        get_backend()


def test_get_backend_missing_connection_string(tmp_path, monkeypatch):
    """Test that missing connection string raises error."""
    (tmp_path / ".env").write_text("DB_MCP_DATABASE=sqlserver\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)
    with pytest.raises(ValueError, match="DB_MCP_CONNECTION_STRING"):
        get_backend()


def test_get_backend_invalid_backend(tmp_path, monkeypatch):
    """Test that invalid backend raises error."""
    (tmp_path / ".env").write_text(
        "DB_MCP_DATABASE=invalid\nDB_MCP_CONNECTION_STRING=test\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)
    with pytest.raises(ValueError, match="Unsupported backend"):
        get_backend()


def test_get_backend_sqlserver(tmp_path, monkeypatch):
    """Test that SQL Server backend is created."""
    (tmp_path / ".env").write_text(
        "DB_MCP_DATABASE=sqlserver\nDB_MCP_CONNECTION_STRING=test\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)
    backend = get_backend()
    assert backend.__class__.__name__ == "MSSQLBackend"


def test_get_backend_postgres(tmp_path, monkeypatch):
    """Test that PostgreSQL backend is created."""
    (tmp_path / ".env").write_text(
        "DB_MCP_DATABASE=postgres\nDB_MCP_CONNECTION_STRING=dbname=test\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DB_MCP_PROJECT_DIR", raising=False)
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
        backend = _create_backend("access_odbc", "test_connection_string", 30, {})
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
            backend = _create_backend("access_com", "test_connection_string", 30, {})
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


class TestParseWorkspaceEnv:
    """Tests for parse_workspace_env without mutating os.environ."""

    def test_parse_does_not_mutate_os_environ(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("DB_MCP_DATABASE=sqlserver\n")
        before = dict(os.environ)
        parse_workspace_env(tmp_path)
        assert os.environ == before

    def test_env_local_overrides_env(self, tmp_path):
        (tmp_path / ".env").write_text("DB_MCP_ALLOW_DATA_ACCESS=false\n")
        (tmp_path / ".env.local").write_text("DB_MCP_ALLOW_DATA_ACCESS=true\n")
        env_map = parse_workspace_env(tmp_path)
        assert env_map["DB_MCP_ALLOW_DATA_ACCESS"] == "true"

    def test_process_env_overrides_dotenv(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("DB_MCP_DATABASE=sqlserver\n")
        monkeypatch.setenv("DB_MCP_DATABASE", "postgres")
        env_map = parse_workspace_env(tmp_path)
        assert env_map["DB_MCP_DATABASE"] == "postgres"


class TestEnvMtimeTracking:
    """Tests for per-workspace env file mtime tracking."""

    def test_record_env_mtimes_stores_existing_files(self, tmp_path):
        (tmp_path / ".env").write_text("X=1\n")
        mtimes = record_env_mtimes(tmp_path)
        assert len(mtimes) == 1

    def test_unchanged_mtime(self, tmp_path):
        (tmp_path / ".env").write_text("X=1\n")
        mtimes = record_env_mtimes(tmp_path)
        assert not env_files_changed(tmp_path, mtimes)

    def test_mtime_change_detected(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("X=1\n")
        mtimes = record_env_mtimes(tmp_path)
        original_mtime = env_file.stat().st_mtime
        os.utime(str(env_file), (original_mtime + 1, original_mtime + 1))
        assert env_files_changed(tmp_path, mtimes)


class TestBuildRegistryFromEnv:
    """Tests for isolated registry construction from env maps."""

    def test_two_workspaces_distinct_registries(self, tmp_path):
        ws_a = tmp_path / "project_a"
        ws_b = tmp_path / "project_b"
        ws_a.mkdir()
        ws_b.mkdir()
        (ws_a / ".env").write_text(
            "DB_MCP_SYNC_DATABASE=sqlserver\n"
            "DB_MCP_SYNC_CONNECTION_STRING=conn_a\n"
        )
        (ws_b / ".env").write_text(
            "DB_MCP_OFFLINE_DATABASE=postgres\n"
            "DB_MCP_OFFLINE_CONNECTION_STRING=conn_b\n"
        )

        reg_a = build_registry_from_env(parse_workspace_env(ws_a), ws_a)
        reg_b = build_registry_from_env(parse_workspace_env(ws_b), ws_b)

        assert reg_a.list_backends() == ["sync"]
        assert reg_b.list_backends() == ["offline"]
        assert reg_a is not reg_b


class TestInitializeBackendsResilience:
    """initialize_backends must isolate per-backend construction failures."""

    @staticmethod
    def _clear_db_mcp_env(monkeypatch):
        for key in list(os.environ):
            if key.startswith("DB_MCP_"):
                monkeypatch.delenv(key, raising=False)

    def test_one_bad_backend_does_not_block_others(self, tmp_path, monkeypatch):
        """A single failed backend must not prevent the others from registering."""
        from db_inspector_mcp.config import initialize_backends
        from db_inspector_mcp.backends.registry import get_registry
        from db_inspector_mcp.backends.base import DatabaseBackend

        self._clear_db_mcp_env(monkeypatch)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DB_MCP_GOOD_DATABASE", "sqlserver")
        monkeypatch.setenv("DB_MCP_GOOD_CONNECTION_STRING", "good-conn")
        monkeypatch.setenv("DB_MCP_BAD_DATABASE", "sqlserver")
        monkeypatch.setenv("DB_MCP_BAD_CONNECTION_STRING", "bad-conn")

        get_registry().clear()

        def fake_create(backend_type, conn, timeout, env_map):
            if "bad" in conn:
                raise RuntimeError("driver missing")
            return MagicMock(spec=DatabaseBackend)

        try:
            with patch("db_inspector_mcp.config._create_backend", side_effect=fake_create):
                registry = initialize_backends()
            names = registry.list_backends()
            assert "good" in names
            assert "bad" not in names
        finally:
            get_registry().clear()

    def test_all_backends_fail_raises_and_leaves_empty_registry(self, tmp_path, monkeypatch):
        """When every backend fails, raise ValueError and leave no partial registry."""
        from db_inspector_mcp.config import initialize_backends
        from db_inspector_mcp.backends.registry import get_registry

        self._clear_db_mcp_env(monkeypatch)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DB_MCP_BAD_DATABASE", "sqlserver")
        monkeypatch.setenv("DB_MCP_BAD_CONNECTION_STRING", "bad-conn")

        get_registry().clear()

        try:
            with patch(
                "db_inspector_mcp.config._create_backend",
                side_effect=RuntimeError("driver missing"),
            ):
                with pytest.raises(ValueError, match="No database backends could be initialized"):
                    initialize_backends()
            assert get_registry().list_backends() == []
        finally:
            get_registry().clear()

