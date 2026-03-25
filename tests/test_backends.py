"""Tests for database backends."""

import faulthandler
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, call

import pyodbc
import pytest

def _access_is_installed() -> bool:
    """Check if Microsoft Access is installed via registry (no launch)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Access.Application")
        return True
    except (OSError, ImportError):
        return False


_env_override = os.getenv("DB_MCP_RUN_ACCESS_INTEGRATION", "").lower()
if _env_override == "true":
    _RUN_ACCESS_INTEGRATION = True
elif _env_override == "false":
    _RUN_ACCESS_INTEGRATION = False
else:
    _RUN_ACCESS_INTEGRATION = _access_is_installed()


@contextmanager
def _suppress_com_seh():
    """Suppress Windows SEH tracebacks during COM teardown.

    When an out-of-process COM server (e.g. Access) has been Quit(), any
    subsequent Release() on stale COM proxies triggers RPC_E_DISCONNECTED
    (0x80010108) as a Windows Structured Exception.  pywin32 handles this
    correctly, but Python's faulthandler prints a scary "Windows fatal
    exception" traceback before the handler runs.  Disabling faulthandler
    around COM cleanup suppresses these harmless messages.
    """
    faulthandler.disable()
    try:
        yield
    finally:
        faulthandler.enable()

from db_inspector_mcp.backends.access_com import AccessCOMBackend, COM_AVAILABLE
from db_inspector_mcp.backends.access_odbc import AccessODBCBackend
from db_inspector_mcp.backends.base import DatabaseBackend
from db_inspector_mcp.backends.mssql import MSSQLBackend
from db_inspector_mcp.backends.postgres import PostgresBackend
from db_inspector_mcp.backends.registry import BackendRegistry


def test_backend_is_abstract():
    """Test that DatabaseBackend is abstract and cannot be instantiated."""
    with pytest.raises(TypeError):
        DatabaseBackend("test", 30)


def test_backend_sql_dialect_property():
    """Test that each backend has the correct sql_dialect property."""
    # MSSQL backend
    mssql = MSSQLBackend("test_connection_string", 30)
    assert mssql.sql_dialect == "mssql"
    
    # PostgreSQL backend
    postgres = PostgresBackend("test_connection_string", 30)
    assert postgres.sql_dialect == "postgres"
    
    # Access ODBC backend
    access_odbc = AccessODBCBackend("test_connection_string", 30)
    assert access_odbc.sql_dialect == "access"
    
    # Access COM backend
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    with patch('db_inspector_mcp.backends.access_com.win32com.client'):
        access_com = AccessCOMBackend(connection_string, 30)
        assert access_com.sql_dialect == "access"


def test_mssql_backend_initialization():
    """Test that MSSQL backend can be initialized."""
    backend = MSSQLBackend("test_connection_string", 30)
    assert backend.connection_string == "test_connection_string"
    assert backend.query_timeout_seconds == 30


def test_postgres_backend_initialization():
    """Test that Postgres backend can be initialized."""
    backend = PostgresBackend("test_connection_string", 30)
    assert backend.connection_string == "test_connection_string"
    assert backend.query_timeout_seconds == 30


def test_access_odbc_backend_initialization():
    """Test that Access ODBC backend can be initialized."""
    conn_str = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    backend = AccessODBCBackend(conn_str, 30)
    assert backend.connection_string == conn_str
    assert backend.query_timeout_seconds == 30


def test_registry_clear_closes_registered_backends():
    """Clearing the registry should call close() on all backend instances."""
    registry = BackendRegistry()
    backend1 = MSSQLBackend("conn1", 30)
    backend2 = PostgresBackend("conn2", 30)
    backend1.close = MagicMock()
    backend2.close = MagicMock()

    registry.register("one", backend1, set_as_default=True)
    registry.register("two", backend2)
    registry.clear()

    backend1.close.assert_called_once()
    backend2.close.assert_called_once()
    assert registry.list_backends() == []
    assert registry.get_default_name() is None


def test_registry_register_replacement_closes_previous_backend():
    """Registering a backend with an existing name closes the old instance."""
    registry = BackendRegistry()
    old_backend = MSSQLBackend("conn1", 30)
    new_backend = PostgresBackend("conn2", 30)
    old_backend.close = MagicMock()

    registry.register("default", old_backend, set_as_default=True)
    registry.register("default", new_backend, set_as_default=True)

    old_backend.close.assert_called_once()
    assert registry.get("default") is new_backend


def test_access_odbc_ttl_defaults():
    """Test that ODBC backend initialises TTL connection cache fields."""
    backend = AccessODBCBackend("test_connection_string", 30)
    assert backend._conn is None
    assert backend._close_timer is None
    assert backend._conn_ttl == 5.0  # default


def test_access_odbc_custom_ttl():
    """Test that a custom TTL can be set via __init__."""
    backend = AccessODBCBackend("test_connection_string", 30, connection_ttl_seconds=10.0)
    assert backend._conn_ttl == 10.0


def test_access_odbc_ttl_zero_connect_per_request():
    """With TTL=0 the backend falls back to connect-per-request."""
    backend = AccessODBCBackend("test_connection_string", 30, connection_ttl_seconds=0)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = (42,)

    with patch('db_inspector_mcp.backends.access_odbc.pyodbc') as mock_pyodbc:
        mock_pyodbc.connect.return_value = mock_conn

        result = backend.count_query_results("SELECT * FROM test")

        # Connection opened
        mock_pyodbc.connect.assert_called_once_with(
            backend.connection_string, timeout=30
        )
        # With TTL=0 the connection is closed immediately
        mock_conn.close.assert_called_once()
        assert result == 42
        # No cached connection left
        assert backend._conn is None


def test_access_odbc_connection_reused_within_ttl():
    """Two calls within the TTL window should reuse the same connection."""
    backend = AccessODBCBackend("test_connection_string", 30, connection_ttl_seconds=60)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = (42,)

    with patch('db_inspector_mcp.backends.access_odbc.pyodbc') as mock_pyodbc:
        mock_pyodbc.connect.return_value = mock_conn

        # First call — creates a fresh connection
        backend.count_query_results("SELECT * FROM test")
        assert mock_pyodbc.connect.call_count == 1

        # Second call — should reuse the cached connection
        backend.count_query_results("SELECT * FROM test")
        assert mock_pyodbc.connect.call_count == 1  # still 1

        # Connection should NOT be closed yet (TTL is 60 s)
        mock_conn.close.assert_not_called()

    # Cleanup: cancel the pending timer so it doesn't fire during other tests
    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_odbc_connection_closed_after_ttl_expires():
    """Connection should be closed once the TTL timer fires."""
    import time as _time

    backend = AccessODBCBackend("test_connection_string", 30, connection_ttl_seconds=0.15)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = (42,)

    with patch('db_inspector_mcp.backends.access_odbc.pyodbc') as mock_pyodbc:
        mock_pyodbc.connect.return_value = mock_conn

        backend.count_query_results("SELECT * FROM test")
        # Connection is still open right after the call
        mock_conn.close.assert_not_called()

        # Wait for the TTL timer to fire
        _time.sleep(0.4)

        # Timer should have closed the connection
        mock_conn.close.assert_called_once()
        assert backend._conn is None


def test_access_odbc_stale_connection_discarded_on_pyodbc_error():
    """If a pyodbc.Error is raised, the cached connection is discarded."""
    backend = AccessODBCBackend("test_connection_string", 30, connection_ttl_seconds=60)

    good_conn = MagicMock()
    good_cursor = MagicMock()
    good_conn.cursor.return_value = good_cursor
    good_cursor.fetchone.return_value = (42,)

    bad_conn = MagicMock()
    bad_cursor = MagicMock()
    bad_conn.cursor.return_value = bad_cursor
    bad_cursor.execute.side_effect = pyodbc.Error("HY000", "stale connection")

    with patch('db_inspector_mcp.backends.access_odbc.pyodbc') as mock_pyodbc:
        mock_pyodbc.Error = pyodbc.Error
        mock_pyodbc.connect.return_value = good_conn

        # First call succeeds — connection is cached
        result = backend.count_query_results("SELECT * FROM test")
        assert result == 42
        assert mock_pyodbc.connect.call_count == 1

        # Simulate a stale connection: next cursor raises pyodbc.Error
        good_conn.cursor.return_value = bad_cursor

        with pytest.raises(pyodbc.Error):
            backend.count_query_results("SELECT * FROM test")

        # The stale connection should have been discarded
        assert backend._conn is None
        good_conn.close.assert_called_once()

    # Cleanup
    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_odbc_connection_closed_on_non_pyodbc_error():
    """Non-pyodbc errors should NOT discard the cached connection."""
    backend = AccessODBCBackend("test_connection_string", 30, connection_ttl_seconds=60)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.execute.side_effect = ValueError("bad query")

    with patch('db_inspector_mcp.backends.access_odbc.pyodbc') as mock_pyodbc:
        mock_pyodbc.Error = pyodbc.Error
        mock_pyodbc.connect.return_value = mock_conn

        with pytest.raises(ValueError, match="bad query"):
            backend.count_query_results("SELECT * FROM bad_table")

        # Connection should still be cached (not discarded for non-pyodbc errors)
        assert backend._conn is mock_conn
        mock_conn.close.assert_not_called()

    # Cleanup
    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_backend_initialization():
    """Test that Access COM backend can be initialized."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    with patch('db_inspector_mcp.backends.access_com.win32com.client'):
        backend = AccessCOMBackend(connection_string, 30)
        assert backend.connection_string == connection_string
        assert backend.query_timeout_seconds == 30
        assert backend._odbc_backend is not None


def test_access_com_no_ownership_tracking():
    """Test that COM backend does not have ownership tracking fields."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    with patch('db_inspector_mcp.backends.access_com.win32com.client'):
        backend = AccessCOMBackend(connection_string, 30)
        # These ownership fields should no longer exist
        assert not hasattr(backend, '_owns_app')
        assert not hasattr(backend, '_owns_db')
        assert not hasattr(backend, '_db_opened_via_getobject')
        # Cached database should not exist (per-request now)
        assert not hasattr(backend, '_db')


def test_access_com_close_releases_references_without_quit():
    """Test that close() releases references and never calls Access.Quit()."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    with patch('db_inspector_mcp.backends.access_com.win32com.client'):
        backend = AccessCOMBackend(connection_string, 30)
        backend._odbc_backend.close = MagicMock()
        backend._app = MagicMock()
        backend._close_timer = MagicMock()

        app_ref = backend._app
        timer_ref = backend._close_timer
        backend.close()

        backend._odbc_backend.close.assert_called_once()
        timer_ref.cancel.assert_called_once()
        app_ref.Quit.assert_not_called()
        assert backend._app is None


def test_access_com_backend_without_pywin32():
    """Test that Access COM backend raises error when pywin32 is not available."""
    with patch('db_inspector_mcp.backends.access_com.COM_AVAILABLE', False):
        connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
        with pytest.raises(ImportError, match="pywin32 is required"):
            AccessCOMBackend(connection_string, 30)


def test_access_com_get_query_by_name():
    """Test that Access COM backend can get query by name."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    
    class MockQueryDef:
        def __init__(self):
            self.Name = "TestQuery"
            self.SQL = "SELECT * FROM TestTable"
            self.Type = 0  # Select query
    
    mock_query_def = MockQueryDef()
    
    mock_query_defs = MagicMock(side_effect=lambda name: mock_query_def if name == "TestQuery" else None)
    
    mock_db = MagicMock()
    mock_db.QueryDefs = mock_query_defs
    
    mock_dbe = MagicMock()
    mock_dbe.OpenDatabase.return_value = mock_db
    
    mock_app = MagicMock()
    mock_app.DBEngine = mock_dbe
    mock_app.CurrentDb.return_value = None
    mock_app.hWndAccessApp.return_value = 0
    
    with patch('db_inspector_mcp.backends.access_com.win32com.client') as mock_win32com, \
         patch('db_inspector_mcp.backends.access_com.gencache') as mock_gencache:
        mock_win32com.GetObject.side_effect = Exception("No existing database")
        mock_gencache.EnsureDispatch.return_value = mock_app
        
        backend = AccessCOMBackend(connection_string, 30)
        result = backend.get_query_by_name("TestQuery")
        
        assert result["name"] == "TestQuery"
        assert result["sql"] == "SELECT * FROM TestTable"
        assert result["type"] == "Select"
        
        mock_db.Close.assert_called_once()
    
    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_dao_database_closes_on_error():
    """Test that DAO database is closed even when an error occurs."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    
    mock_db = MagicMock()
    mock_db.QueryDefs.side_effect = Exception("COM error")
    
    mock_dbe = MagicMock()
    mock_dbe.OpenDatabase.return_value = mock_db
    
    mock_app = MagicMock()
    mock_app.DBEngine = mock_dbe
    mock_app.CurrentDb.return_value = None
    mock_app.hWndAccessApp.return_value = 0
    
    with patch('db_inspector_mcp.backends.access_com.win32com.client') as mock_win32com, \
         patch('db_inspector_mcp.backends.access_com.gencache') as mock_gencache:
        mock_win32com.GetObject.side_effect = Exception("No existing database")
        mock_gencache.EnsureDispatch.return_value = mock_app
        
        backend = AccessCOMBackend(connection_string, 30)
        
        with pytest.raises(RuntimeError):
            backend.get_query_by_name("NonExistent")
        
        mock_db.Close.assert_called_once()
    
    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_dao_database_uses_currentdb_when_available():
    """Test that DAO database uses CurrentDb() when Access has our DB open."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"
    mock_current_db.OpenRecordset.side_effect = Exception("MSysObjects denied")
    mock_table_def = MagicMock()
    mock_table_def.Name = "TestTable"
    mock_current_db.TableDefs = [mock_table_def]
    
    mock_app = MagicMock()
    mock_app.CurrentDb.return_value = mock_current_db
    mock_app.hWndAccessApp.return_value = 0
    
    with patch('db_inspector_mcp.backends.access_com.win32com.client') as mock_win32com, \
         patch('db_inspector_mcp.backends.access_com.gencache') as mock_gencache:
        mock_win32com.GetObject.side_effect = Exception("No existing database")
        mock_gencache.EnsureDispatch.return_value = mock_app
        
        backend = AccessCOMBackend(connection_string, 30)
        tables = backend.list_tables()
        
        assert len(tables) == 1
        assert tables[0]["name"] == "TestTable"
        
        mock_current_db.Close.assert_not_called()
    
    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_list_views():
    """Test that Access COM backend lists views without SQL."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    
    mock_query_def1 = MagicMock()
    mock_query_def1.Name = "Query1"
    mock_query_def2 = MagicMock()
    mock_query_def2.Name = "Query2"
    
    mock_query_defs = MagicMock()
    mock_query_defs.__iter__ = MagicMock(return_value=iter([mock_query_def1, mock_query_def2]))
    
    mock_db = MagicMock()
    mock_db.QueryDefs = mock_query_defs
    mock_db.OpenRecordset.side_effect = Exception("MSysObjects denied")
    
    mock_dbe = MagicMock()
    mock_dbe.OpenDatabase.return_value = mock_db
    
    mock_app = MagicMock()
    mock_app.DBEngine = mock_dbe
    mock_app.CurrentDb.return_value = None
    mock_app.hWndAccessApp.return_value = 0
    
    with patch('db_inspector_mcp.backends.access_com.win32com.client') as mock_win32com, \
         patch('db_inspector_mcp.backends.access_com.gencache') as mock_gencache:
        mock_win32com.GetObject.side_effect = Exception("No existing database")
        mock_gencache.EnsureDispatch.return_value = mock_app
        
        backend = AccessCOMBackend(connection_string, 30)
        views = backend.list_views()
        
        assert len(views) == 2
        assert views[0]["name"] == "Query1"
        assert views[0]["definition"] is None
        assert views[1]["name"] == "Query2"
        assert views[1]["definition"] is None
        
        mock_db.Close.assert_called_once()
    
    if backend._close_timer is not None:
        backend._close_timer.cancel()


# =============================================================================
# DAO fallback tests for VBA UDF support (Access COM backend)
# =============================================================================

def _make_dao_recordset(columns, rows):
    """Create a mock DAO Recordset with the given columns and rows.

    Args:
        columns: list of (name, dao_type) tuples
        rows: list of lists of values (one inner list per row)

    Returns:
        MagicMock configured as a DAO Recordset
    """
    rs = MagicMock()
    rs.Fields.Count = len(columns)

    fields = []
    for i, (name, dao_type) in enumerate(columns):
        field = MagicMock()
        field.Name = name
        field.Type = dao_type
        field.Required = False
        field.Size = 50
        fields.append(field)

    # Fields(i) and Fields("name") both need to work
    field_by_name = {f.Name: f for f in fields}

    def field_accessor(key):
        if isinstance(key, int):
            return fields[key]
        return field_by_name[key]

    rs.Fields.side_effect = field_accessor

    # EOF / MoveNext: iterate through rows then signal EOF
    row_iter = iter(rows)
    current_row = [None]  # mutable container

    def advance():
        try:
            current_row[0] = next(row_iter)
            for i, val in enumerate(current_row[0]):
                fields[i].Value = val
        except StopIteration:
            current_row[0] = None

    # Load first row
    if rows:
        current_row[0] = rows[0]
        for i, val in enumerate(rows[0]):
            fields[i].Value = val
        remaining = iter(rows[1:])
    else:
        remaining = iter([])

    # Track position for EOF
    position = {"idx": 0, "total": len(rows)}

    def move_next():
        position["idx"] += 1

    # EOF returns True when we've gone past the last row
    type(rs).EOF = property(lambda self: position["idx"] >= position["total"])
    rs.MoveNext = move_next

    # When MoveNext is called, update field values
    original_move_next = rs.MoveNext

    def move_next_with_values():
        position["idx"] += 1
        if position["idx"] < position["total"]:
            for i, val in enumerate(rows[position["idx"]]):
                fields[i].Value = val

    rs.MoveNext = move_next_with_values

    return rs


def _make_com_backend_with_currentdb(mock_current_db):
    """Create an AccessCOMBackend wired to use the given mock CurrentDb.

    Returns (backend, mock_app).  Caller must cancel backend._close_timer.

    The helper also patches ``_find_existing_instance`` so that worker
    threads spawned by ``_run_dao_with_timeout`` can find the mock app
    without a real Running Object Table.
    """
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"

    mock_app = MagicMock()
    mock_app.CurrentDb.return_value = mock_current_db
    mock_app.hWndAccessApp.return_value = 0
    mock_current_db.Name = "C:\\test.accdb"

    with patch('db_inspector_mcp.backends.access_com.win32com.client') as mock_win32com, \
         patch('db_inspector_mcp.backends.access_com.gencache') as mock_gencache:
        mock_win32com.GetObject.side_effect = Exception("No existing database")
        mock_gencache.EnsureDispatch.return_value = mock_app
        backend = AccessCOMBackend(connection_string, 30)

    # Pre-set the app so _get_access_app uses it directly
    backend._app = mock_app
    # Mock _find_existing_instance so worker threads in _run_dao_with_timeout
    # can locate the mock app without a real ROT.
    backend._find_existing_instance = lambda: mock_app
    return backend, mock_app


def test_access_com_dao_fallback_on_undefined_function():
    """ODBC 'undefined function' error triggers DAO fallback for count."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    rs = _make_dao_recordset([("cnt", 4)], [[42]])
    mock_current_db.OpenRecordset.return_value = rs

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    # Make ODBC raise a UDF error
    backend._odbc_backend.count_query_results = MagicMock(
        side_effect=Exception("(-3025) undefined function 'MyVBAFunc' in expression")
    )

    result = backend.count_query_results("SELECT MyVBAFunc(ID) FROM TestTable")
    assert result == 42

    # Verify ODBC was tried first
    backend._odbc_backend.count_query_results.assert_called_once()
    # Verify DAO recordset was used and closed
    rs.Close.assert_called_once()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_dao_fallback_on_too_few_parameters():
    """ODBC 'too few parameters' error triggers DAO fallback for preview."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    rs = _make_dao_recordset(
        [("ID", 4), ("Name", 10)],
        [[1, "Alice"], [2, "Bob"]],
    )
    mock_current_db.OpenRecordset.return_value = rs

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    backend._odbc_backend.preview = MagicMock(
        side_effect=Exception("Too few parameters. Expected 1. (-3025)")
    )

    result = backend.preview("SELECT MyUDF(Name) FROM TestTable", max_rows=10)
    assert len(result) == 2
    assert result[0]["ID"] == 1
    assert result[1]["Name"] == "Bob"

    backend._odbc_backend.preview.assert_called_once()
    rs.Close.assert_called_once()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_msys_query_routed_directly_to_dao():
    """Queries referencing MSys* tables skip ODBC and go straight to DAO."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    rs = _make_dao_recordset([("cnt", 4)], [[100]])
    mock_current_db.OpenRecordset.return_value = rs

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    backend._odbc_backend.count_query_results = MagicMock()

    result = backend.count_query_results(
        "SELECT COUNT(*) FROM MSysObjects WHERE Type=1"
    )
    assert result == 100

    # ODBC should NOT have been called at all
    backend._odbc_backend.count_query_results.assert_not_called()
    rs.Close.assert_called_once()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_msys_get_query_columns_routed_to_dao():
    """get_query_columns on MSys* tables goes directly to DAO."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    rs = _make_dao_recordset([("Name", 10), ("Type", 3)], [])
    mock_current_db.OpenRecordset.return_value = rs

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    backend._odbc_backend.get_query_columns = MagicMock()

    result = backend.get_query_columns(
        "SELECT [Name], [Type] FROM MSysObjects WHERE [Type] IN (1,5)"
    )
    assert any(c["name"] == "Name" for c in result)

    backend._odbc_backend.get_query_columns.assert_not_called()
    rs.Close.assert_called_once()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_dao_fallback_on_no_read_permission():
    """ODBC 'no read permission' on non-MSys tables still triggers DAO fallback."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    rs = _make_dao_recordset([("cnt", 4)], [[42]])
    mock_current_db.OpenRecordset.return_value = rs

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    backend._odbc_backend.count_query_results = MagicMock(
        side_effect=Exception(
            "Record(s) cannot be read; no read permission on 'SomeTable'"
        )
    )

    result = backend.count_query_results(
        "SELECT COUNT(*) FROM SomeTable"
    )
    assert result == 42

    # ODBC was tried first, then DAO fallback kicked in
    backend._odbc_backend.count_query_results.assert_called_once()
    rs.Close.assert_called_once()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_no_dao_fallback_on_syntax_error():
    """Non-UDF errors (e.g. syntax errors) propagate without DAO retry."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    backend._odbc_backend.count_query_results = MagicMock(
        side_effect=Exception("Syntax error in query expression")
    )

    with pytest.raises(Exception, match="Syntax error"):
        backend.count_query_results("SELECT BAD SYNTAX")

    # DAO should NOT have been called
    mock_current_db.OpenRecordset.assert_not_called()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_dao_fallback_also_fails():
    """When both ODBC and DAO fail, the DAO error is raised."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"
    mock_current_db.OpenRecordset.side_effect = Exception("DAO: unknown column 'Bogus'")

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    backend._odbc_backend.count_query_results = MagicMock(
        side_effect=Exception("undefined function 'MyFunc'")
    )

    with pytest.raises(Exception, match="DAO: unknown column"):
        backend.count_query_results("SELECT MyFunc(Bogus) FROM TestTable")

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_odbc_success_skips_dao():
    """When ODBC succeeds, DAO CurrentDb is never touched for queries."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    backend._odbc_backend.count_query_results = MagicMock(return_value=99)

    result = backend.count_query_results("SELECT * FROM TestTable")
    assert result == 99

    # DAO should NOT have been called for query execution
    mock_current_db.OpenRecordset.assert_not_called()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_dao_fallback_get_query_columns():
    """DAO fallback works for get_query_columns with correct Field metadata."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    # TOP 0 recordset — no rows, just field metadata
    rs = _make_dao_recordset(
        [("ID", 4), ("FullName", 10), ("Active", 1)],
        [],
    )
    mock_current_db.OpenRecordset.return_value = rs

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    backend._odbc_backend.get_query_columns = MagicMock(
        side_effect=Exception("undefined function 'FormatName'")
    )

    columns = backend.get_query_columns("SELECT FormatName(First, Last) FROM T")
    assert len(columns) == 3
    assert columns[0]["name"] == "ID"
    assert columns[0]["type"] == "Long"
    assert columns[1]["name"] == "FullName"
    assert columns[1]["type"] == "Text"
    assert columns[2]["name"] == "Active"
    assert columns[2]["type"] == "Boolean"

    rs.Close.assert_called_once()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_dao_fallback_sum_query_column():
    """DAO fallback works for sum_query_column."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    rs = _make_dao_recordset([("sum_val", 7)], [[1234.56]])
    mock_current_db.OpenRecordset.return_value = rs

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    backend._odbc_backend.sum_query_column = MagicMock(
        side_effect=Exception("Too few parameters. Expected 1.")
    )

    result = backend.sum_query_column("SELECT CalcAmount(ID) AS amt FROM T", "amt")
    assert result == 1234.56

    rs.Close.assert_called_once()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_dao_fallback_measure_query():
    """DAO fallback works for measure_query with timing."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    rs = _make_dao_recordset(
        [("ID", 4), ("Val", 7)],
        [[1, 10.0], [2, 20.0]],
    )
    mock_current_db.OpenRecordset.return_value = rs

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)

    backend._odbc_backend.measure_query = MagicMock(
        side_effect=Exception("undefined function 'CalcVal'")
    )

    result = backend.measure_query("SELECT CalcVal(X) FROM T", max_rows=100)
    assert result["row_count"] == 2
    assert result["hit_limit"] is False
    assert "execution_time_ms" in result

    rs.Close.assert_called_once()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


# =============================================================================
# DAO timeout tests
# =============================================================================


def test_access_com_dao_timeout_after_open_recordset():
    """DAO raises TimeoutError when OpenRecordset exceeds the timeout.

    The worker thread blocks in OpenRecordset longer than the timeout,
    so the main thread's join() expires and raises TimeoutError.
    """
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    rs = _make_dao_recordset([("ID", 4)], [[1]])

    def slow_open_recordset(*args, **kwargs):
        time.sleep(0.5)
        return rs

    mock_current_db.OpenRecordset.side_effect = slow_open_recordset

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)
    backend.query_timeout_seconds = 0.15

    with pytest.raises(TimeoutError, match="exceeded.*timeout"):
        backend._dao_execute("SELECT * FROM BigTable")

    # Worker is still blocked — recordset hasn't been closed yet
    # (the worker thread will eventually close it when it completes)

    if backend._close_timer is not None:
        backend._close_timer.cancel()

    # Allow the worker thread to finish so it doesn't leak into other tests
    if backend._active_worker is not None:
        backend._active_worker.join(timeout=2)


def test_access_com_dao_timeout_during_row_iteration():
    """DAO raises TimeoutError when row iteration exceeds the timeout.

    The worker thread blocks during slow row reads, and the main
    thread's join() expires.
    """
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    many_rows = [[i] for i in range(1000)]
    rs = _make_dao_recordset([("ID", 4)], many_rows)

    original_move_next = rs.MoveNext

    def slow_move_next():
        time.sleep(0.02)
        original_move_next()

    rs.MoveNext = slow_move_next
    mock_current_db.OpenRecordset.return_value = rs

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)
    backend.query_timeout_seconds = 0.15

    with pytest.raises(TimeoutError, match="exceeded.*timeout"):
        backend._dao_execute("SELECT * FROM BigTable")

    if backend._close_timer is not None:
        backend._close_timer.cancel()

    if backend._active_worker is not None:
        backend._active_worker.join(timeout=2)


def test_access_com_dao_no_timeout_on_fast_query():
    """DAO completes normally when query finishes within the timeout."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    rs = _make_dao_recordset([("ID", 4), ("Name", 10)], [[1, "Alice"], [2, "Bob"]])
    mock_current_db.OpenRecordset.return_value = rs

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)
    backend.query_timeout_seconds = 10

    col_names, rows = backend._dao_execute("SELECT * FROM TestTable")
    assert col_names == ["ID", "Name"]
    assert len(rows) == 2
    assert rows[0] == [1, "Alice"]

    rs.Close.assert_called_once()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_access_com_dao_get_query_columns_timeout():
    """_dao_get_query_columns raises TimeoutError on slow OpenRecordset."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    rs = _make_dao_recordset([("ID", 4)], [[1]])

    def slow_open_recordset(*args, **kwargs):
        time.sleep(0.5)
        return rs

    mock_current_db.OpenRecordset.side_effect = slow_open_recordset

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)
    backend.query_timeout_seconds = 0.15

    with pytest.raises(TimeoutError, match="exceeded.*timeout"):
        backend._dao_get_query_columns("SELECT * FROM BigTable")

    if backend._close_timer is not None:
        backend._close_timer.cancel()

    if backend._active_worker is not None:
        backend._active_worker.join(timeout=2)


def test_access_com_dao_active_worker_guard():
    """Concurrent DAO calls are refused when a previous worker is still blocked."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    def slow_open_recordset(*args, **kwargs):
        time.sleep(2.0)
        return _make_dao_recordset([("ID", 4)], [[1]])

    mock_current_db.OpenRecordset.side_effect = slow_open_recordset

    backend, _ = _make_com_backend_with_currentdb(mock_current_db)
    backend.query_timeout_seconds = 0.1

    # First call times out, leaving _active_worker alive
    with pytest.raises(TimeoutError):
        backend._dao_execute("SELECT * FROM BigTable")

    assert backend._active_worker is not None
    assert backend._active_worker.is_alive()

    # Second call should be refused immediately
    with pytest.raises(RuntimeError, match="previous DAO query is still running"):
        backend._dao_execute("SELECT 1")

    if backend._close_timer is not None:
        backend._close_timer.cancel()

    if backend._active_worker is not None:
        backend._active_worker.join(timeout=3)


# =============================================================================
# Guard tests — prevent OpenCurrentDatabase on user instances
# =============================================================================


def test_create_fresh_instance_reuses_existing_with_our_db():
    """EnsureDispatch returning an instance with our DB reuses it without OpenCurrentDatabase."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"

    mock_app = MagicMock()
    mock_existing_db = MagicMock()
    mock_existing_db.Name = "C:\\test.accdb"
    mock_app.CurrentDb.return_value = mock_existing_db

    with patch('db_inspector_mcp.backends.access_com.win32com.client'), \
         patch('db_inspector_mcp.backends.access_com.gencache') as mock_gencache:
        mock_gencache.EnsureDispatch.return_value = mock_app
        backend = AccessCOMBackend(connection_string, 30)
        result = backend._create_fresh_instance()

    assert result is mock_app
    mock_app.OpenCurrentDatabase.assert_not_called()
    assert not backend._we_created_app

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_create_fresh_instance_refuses_different_db():
    """EnsureDispatch returning an instance with a DIFFERENT DB raises RuntimeError."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"

    mock_app = MagicMock()
    mock_other_db = MagicMock()
    mock_other_db.Name = "C:\\Users\\someone\\other.accdb"
    mock_app.CurrentDb.return_value = mock_other_db

    with patch('db_inspector_mcp.backends.access_com.win32com.client'), \
         patch('db_inspector_mcp.backends.access_com.gencache') as mock_gencache:
        mock_gencache.EnsureDispatch.return_value = mock_app
        backend = AccessCOMBackend(connection_string, 30)

        with pytest.raises(RuntimeError, match="existing instance"):
            backend._create_fresh_instance()

    mock_app.OpenCurrentDatabase.assert_not_called()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_create_fresh_instance_opens_on_genuinely_new():
    """EnsureDispatch returning a fresh (no DB) instance safely opens our database."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"

    mock_app = MagicMock()
    mock_app.CurrentDb.return_value = None

    with patch('db_inspector_mcp.backends.access_com.win32com.client'), \
         patch('db_inspector_mcp.backends.access_com.gencache') as mock_gencache:
        mock_gencache.EnsureDispatch.return_value = mock_app
        backend = AccessCOMBackend(connection_string, 30)
        result = backend._create_fresh_instance()

    assert result is mock_app
    mock_app.OpenCurrentDatabase.assert_called_once_with("C:\\test.accdb", False)
    assert backend._we_created_app

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_ensure_current_db_refuses_on_user_instance():
    """_ensure_current_db raises when the instance isn't ours and DB doesn't match."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    backend, mock_app = _make_com_backend_with_currentdb(mock_current_db)
    backend._we_created_app = False

    # Make CurrentDb return a different database
    other_db = MagicMock()
    other_db.Name = "C:\\other.accdb"
    mock_app.CurrentDb.return_value = other_db

    with pytest.raises(RuntimeError, match="belongs to the user"):
        backend._ensure_current_db(mock_app)

    mock_app.OpenCurrentDatabase.assert_not_called()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_ensure_current_db_allows_on_our_instance():
    """_ensure_current_db calls OpenCurrentDatabase when we created the instance."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    backend, mock_app = _make_com_backend_with_currentdb(mock_current_db)
    backend._we_created_app = True

    # Make CurrentDb return a different database
    other_db = MagicMock()
    other_db.Name = "C:\\other.accdb"
    mock_app.CurrentDb.return_value = other_db

    backend._ensure_current_db(mock_app)

    mock_app.OpenCurrentDatabase.assert_called_once()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


def test_ensure_current_db_noop_when_already_open():
    """_ensure_current_db is a no-op when CurrentDb already matches."""
    mock_current_db = MagicMock()
    mock_current_db.Name = "C:\\test.accdb"

    backend, mock_app = _make_com_backend_with_currentdb(mock_current_db)
    backend._we_created_app = False  # Doesn't matter — DB already matches

    backend._ensure_current_db(mock_app)

    mock_app.OpenCurrentDatabase.assert_not_called()

    if backend._close_timer is not None:
        backend._close_timer.cancel()


# =============================================================================
# Integration tests for Access COM backend
# These tests require Access to be installed and will be skipped if not available.
#
# Fixtures use module scope so Access is launched once and quit once for the
# entire module.  Individual tests create fresh AccessCOMBackend instances
# that attach to the running Access via ROT scan.
#
# SAFETY: Tests must NEVER close a user's open database.  The fixture uses
# DispatchEx to create an isolated Access process — Dispatch() can attach
# to a user's running instance, and Quit() on that would close their work.
# _release_test_backend() only releases the COM reference and cancels the
# TTL timer; it never calls Quit() or CloseCurrentDatabase().
# =============================================================================


def _paths_match(path1: str | None, path2: str | None) -> bool:
    """Case-insensitive normalized path comparison."""
    if not path1 or not path2:
        return False
    try:
        return os.path.normcase(os.path.abspath(path1)) == os.path.normcase(os.path.abspath(path2))
    except Exception:
        return path1.lower() == path2.lower()


def _get_current_db_path(app) -> str | None:
    """Get CurrentDb path from an Access app, if any."""
    try:
        current_db = app.CurrentDb()
        if current_db is None:
            return None
        return str(current_db.Name)
    except Exception:
        return None


@pytest.fixture(scope="module")
def access_app():
    """Module-scoped isolated Access Application for integration tests.

    Uses DispatchEx to guarantee a new, dedicated COM server process —
    Dispatch() can attach to an already-running user instance, and calling
    Quit() on that would close the user's work.
    """
    if not COM_AVAILABLE:
        pytest.skip("pywin32 not available")
    if sys.platform != "win32":
        pytest.skip("Access COM tests only run on Windows")

    import ctypes
    import win32com.client

    app = win32com.client.DispatchEx("Access.Application")
    try:
        app.UserControl = False
    except Exception:
        pass
    try:
        ctypes.windll.user32.ShowWindow(app.hWndAccessApp(), 5)  # SW_SHOW
    except Exception:
        pass

    yield app

    # Disable faulthandler permanently before quit — stale COM proxies will
    # trigger RPC_E_DISCONNECTED (0x80010108) during Python's GC shutdown,
    # long after this block exits.  Leaving faulthandler off prevents the
    # harmless "Windows fatal exception" traceback at process exit.
    faulthandler.disable()
    try:
        app.Quit()
    except Exception:
        pass


@pytest.fixture(scope="module")
def temp_access_db(access_app):
    """Module-scoped test database — created once, deleted after all tests.

    The database is left open as CurrentDb in `access_app` so that
    backends can attach via GetObject without launching a new instance.
    """
    import gc
    import time
    import uuid

    app = access_app
    temp_dir = tempfile.gettempdir()
    unique_id = uuid.uuid4().hex[:8]
    db_path = os.path.join(temp_dir, f"test_db_{os.getpid()}_{unique_id}.accdb")

    try:
        app.NewCurrentDatabase(db_path)
        db = app.CurrentDb()

        # DAO constants
        dbAutoIncrField = 16
        dbLong = 4
        dbText = 10
        dbDouble = 7

        tbl = db.CreateTableDef("TestTable")

        fld_id = tbl.CreateField("ID", dbLong)
        fld_id.Attributes = dbAutoIncrField
        tbl.Fields.Append(fld_id)

        fld_name = tbl.CreateField("Name", dbText, 50)
        tbl.Fields.Append(fld_name)

        fld_amount = tbl.CreateField("Amount", dbDouble)
        tbl.Fields.Append(fld_amount)

        db.TableDefs.Append(tbl)

        app.DoCmd.SetWarnings(False)
        app.DoCmd.RunSQL("INSERT INTO TestTable (Name, Amount) VALUES ('Test1', 100)")
        app.DoCmd.RunSQL("INSERT INTO TestTable (Name, Amount) VALUES ('Test2', 200)")
        app.DoCmd.SetWarnings(True)

        db.CreateQueryDef("TestQuery", "SELECT * FROM TestTable WHERE Amount > 150")

        del fld_id, fld_name, fld_amount, tbl, db

    except Exception as e:
        pytest.skip(f"Could not create test database: {e}")

    yield db_path

    # Teardown: only close CurrentDb when it is our test DB.
    with _suppress_com_seh():
        try:
            current_db = _get_current_db_path(app)
            if _paths_match(current_db, db_path):
                app.CloseCurrentDatabase()
        except Exception:
            pass

    gc.collect()
    time.sleep(1)

    for path in (db_path.replace('.accdb', '.laccdb'), db_path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def _release_test_backend(backend) -> None:
    """Cancel the TTL timer and release the backend's COM reference.

    SAFETY: Never calls Quit() or CloseCurrentDatabase() — only releases
    the in-process COM pointer so the backend no longer holds a reference
    to the Access instance.  The fixture owns the instance lifecycle.
    """
    if backend._close_timer is not None:
        backend._close_timer.cancel()
    with _suppress_com_seh():
        backend._app = None


@pytest.mark.integration
@pytest.mark.skipif(not COM_AVAILABLE, reason="Access COM not available")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
@pytest.mark.skipif(
    not _RUN_ACCESS_INTEGRATION,
    reason="Access not installed (or DB_MCP_RUN_ACCESS_INTEGRATION=false)",
)
def test_access_com_getobject_existing_database(temp_access_db):
    """Test that a backend attaches to an already-open database via GetObject."""
    backend = AccessCOMBackend(temp_access_db, 30)

    try:
        tables = backend.list_tables()
        table_names = [t["name"] for t in tables]
        assert "TestTable" in table_names

        views = backend.list_views()
        view_names = [v["name"] for v in views]
        assert "TestQuery" in view_names

        query = backend.get_query_by_name("TestQuery")
        assert query["name"] == "TestQuery"
        assert "TestTable" in query["sql"]
        assert query["type"] == "Select"
    finally:
        _release_test_backend(backend)


@pytest.mark.integration
@pytest.mark.skipif(not COM_AVAILABLE, reason="Access COM not available")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
@pytest.mark.skipif(
    not _RUN_ACCESS_INTEGRATION,
    reason="Access not installed (or DB_MCP_RUN_ACCESS_INTEGRATION=false)",
)
def test_access_com_backend_connects_and_queries(temp_access_db):
    """Test that a fresh backend can connect and query the database."""
    backend = AccessCOMBackend(temp_access_db, 30)

    try:
        tables = backend.list_tables()
        table_names = [t["name"] for t in tables]
        assert "TestTable" in table_names
    finally:
        _release_test_backend(backend)


@pytest.mark.integration
@pytest.mark.skipif(not COM_AVAILABLE, reason="Access COM not available")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
@pytest.mark.skipif(
    not _RUN_ACCESS_INTEGRATION,
    reason="Access not installed (or DB_MCP_RUN_ACCESS_INTEGRATION=false)",
)
def test_access_com_no_lock_between_operations(temp_access_db):
    """Test that no .laccdb lock file persists between COM operations."""
    import time

    backend = AccessCOMBackend(temp_access_db, 30)
    lock_file = temp_access_db.replace('.accdb', '.laccdb')

    try:
        tables = backend.list_tables()
        assert len(tables) > 0

        time.sleep(0.5)

        if not _access_has_db_open(backend, temp_access_db):
            assert not os.path.exists(lock_file), \
                "Lock file (.laccdb) should not persist between operations"
    finally:
        _release_test_backend(backend)


def _access_has_db_open(backend, db_path):
    """Check if the backend's Access Application has the database open."""
    if backend._app is None:
        return False
    try:
        current_db = backend._app.CurrentDb()
        return current_db is not None
    except Exception:
        return False
