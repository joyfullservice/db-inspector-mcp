"""Tests for database backends."""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from db_inspector_mcp.backends.access_com import AccessCOMBackend, COM_AVAILABLE
from db_inspector_mcp.backends.access_odbc import AccessODBCBackend
from db_inspector_mcp.backends.base import DatabaseBackend
from db_inspector_mcp.backends.mssql import MSSQLBackend
from db_inspector_mcp.backends.postgres import PostgresBackend


def test_backend_is_abstract():
    """Test that DatabaseBackend is abstract and cannot be instantiated."""
    with pytest.raises(TypeError):
        DatabaseBackend("test", 30)


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
    backend = AccessODBCBackend("test_connection_string", 30)
    assert backend.connection_string == "test_connection_string"
    assert backend.query_timeout_seconds == 30


def test_access_com_backend_initialization():
    """Test that Access COM backend can be initialized."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    with patch('db_inspector_mcp.backends.access_com.win32com.client'):
        backend = AccessCOMBackend(connection_string, 30)
        assert backend.connection_string == connection_string
        assert backend.query_timeout_seconds == 30
        assert backend._odbc_backend is not None


def test_access_com_backend_without_pywin32():
    """Test that Access COM backend raises error when pywin32 is not available."""
    with patch('db_inspector_mcp.backends.access_com.COM_AVAILABLE', False):
        connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
        with pytest.raises(ImportError, match="pywin32 is required"):
            AccessCOMBackend(connection_string, 30)


def test_access_com_get_query_by_name():
    """Test that Access COM backend can get query by name."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    
    # Create a simple object to mock QueryDef (not MagicMock to avoid attribute issues)
    class MockQueryDef:
        def __init__(self):
            self.Name = "TestQuery"
            self.SQL = "SELECT * FROM TestTable"
            self.Type = 0  # Select query
    
    mock_query_def = MockQueryDef()
    
    # Create a proper mock for QueryDefs collection
    # In COM, QueryDefs is accessed with parentheses: db.QueryDefs(query_name)
    # Use side_effect to make it return the query_def when called
    mock_query_defs = MagicMock(side_effect=lambda name: mock_query_def if name == "TestQuery" else None)
    
    mock_db = MagicMock()
    mock_db.QueryDefs = mock_query_defs
    
    mock_app = MagicMock()
    mock_app.CurrentDb.return_value = mock_db
    
    with patch('db_inspector_mcp.backends.access_com.win32com.client') as mock_win32com:
        # Make GetObject raise an exception so it falls back to Dispatch
        mock_win32com.GetObject.side_effect = Exception("No existing database")
        mock_win32com.Dispatch.return_value = mock_app
        
        backend = AccessCOMBackend(connection_string, 30)
        result = backend.get_query_by_name("TestQuery")
        
        assert result["name"] == "TestQuery"
        assert result["sql"] == "SELECT * FROM TestTable"
        assert result["type"] == "Select"


def test_access_com_list_views():
    """Test that Access COM backend lists views without SQL."""
    connection_string = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\test.accdb;"
    
    # Mock COM objects
    mock_query_def1 = MagicMock()
    mock_query_def1.Name = "Query1"
    mock_query_def2 = MagicMock()
    mock_query_def2.Name = "Query2"
    
    mock_query_defs = MagicMock()
    mock_query_defs.__iter__ = MagicMock(return_value=iter([mock_query_def1, mock_query_def2]))
    
    mock_db = MagicMock()
    mock_db.QueryDefs = mock_query_defs
    
    mock_app = MagicMock()
    mock_app.CurrentDb.return_value = mock_db
    
    with patch('db_inspector_mcp.backends.access_com.win32com.client') as mock_win32com:
        # Make GetObject raise an exception so it falls back to Dispatch
        mock_win32com.GetObject.side_effect = Exception("No existing database")
        mock_win32com.Dispatch.return_value = mock_app
        
        backend = AccessCOMBackend(connection_string, 30)
        views = backend.list_views()
        
        assert len(views) == 2
        assert views[0]["name"] == "Query1"
        assert views[0]["definition"] is None  # SQL not extracted
        assert views[1]["name"] == "Query2"
        assert views[1]["definition"] is None


# =============================================================================
# Integration tests for Access COM backend
# These tests require Access to be installed and will be skipped if not available
# =============================================================================

@pytest.fixture
def temp_access_db():
    """
    Create a temporary Access database for testing.
    
    This fixture:
    - Creates a temporary .accdb file
    - Opens it in Access and creates a test table and query
    - Yields the database path
    - Cleans up by closing Access and deleting the file
    """
    if not COM_AVAILABLE:
        pytest.skip("pywin32 not available")
    
    if sys.platform != "win32":
        pytest.skip("Access COM tests only run on Windows")
    
    import win32com.client
    
    # Create temporary database file
    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, f"test_db_{os.getpid()}.accdb")
    
    app = None
    try:
        # Create new Access database
        app = win32com.client.Dispatch("Access.Application")
        app.NewCurrentDatabase(db_path)
        db = app.CurrentDb()
        
        # Create a test table
        db.Execute(
            "CREATE TABLE TestTable (ID AUTOINCREMENT PRIMARY KEY, Name TEXT(50), Value NUMBER)"
        )
        
        # Insert some test data
        db.Execute("INSERT INTO TestTable (Name, Value) VALUES ('Test1', 100)")
        db.Execute("INSERT INTO TestTable (Name, Value) VALUES ('Test2', 200)")
        
        # Create a test query
        query_sql = "SELECT * FROM TestTable WHERE Value > 150"
        db.CreateQueryDef("TestQuery", query_sql)
        
        # Close the database but keep Access open to test GetObject
        app.CloseCurrentDatabase()
        
        yield db_path
        
    except Exception as e:
        pytest.skip(f"Could not create test database: {e}")
        
    finally:
        # Cleanup: close Access and delete database file
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        
        # Delete the database file
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
        except Exception:
            pass


@pytest.mark.integration
@pytest.mark.skipif(not COM_AVAILABLE, reason="Access COM not available")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_access_com_getobject_existing_database(temp_access_db):
    """Test that GetObject can reference an existing open database."""
    import win32com.client
    
    # Open the database in Access
    app = win32com.client.Dispatch("Access.Application")
    app.OpenCurrentDatabase(temp_access_db)
    
    try:
        # Now create backend - it should use GetObject to get the existing database
        backend = AccessCOMBackend(temp_access_db, 30)
        
        # Verify it can access the database
        tables = backend.list_tables()
        table_names = [t["name"] for t in tables]
        assert "TestTable" in table_names
        
        # Verify it can access the query
        views = backend.list_views()
        view_names = [v["name"] for v in views]
        assert "TestQuery" in view_names
        
        # Get the query details
        query = backend.get_query_by_name("TestQuery")
        assert query["name"] == "TestQuery"
        assert "TestTable" in query["sql"]
        assert query["type"] == "Select"
        
    finally:
        app.CloseCurrentDatabase()
        app.Quit()


@pytest.mark.integration
@pytest.mark.skipif(not COM_AVAILABLE, reason="Access COM not available")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_access_com_with_closed_database(temp_access_db):
    """Test that backend opens database if it's not already open."""
    backend = AccessCOMBackend(temp_access_db, 30)
    
    try:
        # Should be able to access the database even though it wasn't open
        tables = backend.list_tables()
        table_names = [t["name"] for t in tables]
        assert "TestTable" in table_names
    finally:
        # Cleanup: close the Access instance created by the backend
        if backend._app is not None:
            try:
                backend._app.Quit()
            except Exception:
                pass

