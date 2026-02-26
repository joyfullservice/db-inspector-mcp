"""Tests for database backends."""

import faulthandler
import os
import sys
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, call

import pyodbc
import pytest


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


def test_access_com_no_close_method():
    """Test that COM backend does not have a close() method that quits Access."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    with patch('db_inspector_mcp.backends.access_com.win32com.client'):
        backend = AccessCOMBackend(connection_string, 30)
        # close() should not be defined on the backend
        assert not hasattr(backend, 'close')


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
# Integration tests for Access COM backend
# These tests require Access to be installed and will be skipped if not available.
#
# Fixtures use module scope so Access is launched once and quit once for the
# entire module.  Individual tests create fresh AccessCOMBackend instances
# that attach to the running Access via GetObject.
# =============================================================================

@pytest.fixture(scope="module")
def access_app():
    """Module-scoped Access Application — launched once, quit after all tests."""
    if not COM_AVAILABLE:
        pytest.skip("pywin32 not available")
    if sys.platform != "win32":
        pytest.skip("Access COM tests only run on Windows")

    import ctypes
    import win32com.client

    app = win32com.client.Dispatch("Access.Application")
    try:
        ctypes.windll.user32.ShowWindow(app.hWndAccessApp(), 5)  # SW_SHOW
    except Exception:
        pass

    yield app

    with _suppress_com_seh():
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

    # Teardown: close the database and delete the file
    with _suppress_com_seh():
        try:
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
    """Cancel the TTL timer and release the backend's COM reference."""
    if backend._close_timer is not None:
        backend._close_timer.cancel()
    with _suppress_com_seh():
        backend._app = None


@pytest.mark.integration
@pytest.mark.skipif(not COM_AVAILABLE, reason="Access COM not available")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
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
