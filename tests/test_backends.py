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
    mock_query_defs = MagicMock(side_effect=lambda name: mock_query_def if name == "TestQuery" else None)
    
    # Mock database returned by DBEngine.OpenDatabase()
    mock_db = MagicMock()
    mock_db.QueryDefs = mock_query_defs
    
    # Mock DBEngine
    mock_dbe = MagicMock()
    mock_dbe.OpenDatabase.return_value = mock_db
    
    mock_app = MagicMock()
    mock_app.DBEngine = mock_dbe
    
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
    
    # Mock database returned by DBEngine.OpenDatabase()
    mock_db = MagicMock()
    mock_db.QueryDefs = mock_query_defs
    
    # Mock DBEngine
    mock_dbe = MagicMock()
    mock_dbe.OpenDatabase.return_value = mock_db
    
    mock_app = MagicMock()
    mock_app.DBEngine = mock_dbe
    
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
    import uuid
    
    # Create temporary database file with unique name
    temp_dir = tempfile.gettempdir()
    unique_id = uuid.uuid4().hex[:8]
    db_path = os.path.join(temp_dir, f"test_db_{os.getpid()}_{unique_id}.accdb")
    
    app = None
    try:
        # Create new Access database
        app = win32com.client.Dispatch("Access.Application")
        app.NewCurrentDatabase(db_path)
        db = app.CurrentDb()
        
        # Create table using DAO TableDefs (more reliable than SQL DDL)
        # DAO constants
        dbAutoIncrField = 16  # Field attribute for AutoIncrement
        dbLong = 4            # Long integer type
        dbText = 10           # Text type
        dbDouble = 7          # Double type
        
        tbl = db.CreateTableDef("TestTable")
        
        # Create ID field (AutoIncrement)
        fld_id = tbl.CreateField("ID", dbLong)
        fld_id.Attributes = dbAutoIncrField
        tbl.Fields.Append(fld_id)
        
        # Create Name field (Text)
        fld_name = tbl.CreateField("Name", dbText, 50)
        tbl.Fields.Append(fld_name)
        
        # Create Amount field (Double) - avoid "Value" as it's a reserved word
        fld_amount = tbl.CreateField("Amount", dbDouble)
        tbl.Fields.Append(fld_amount)
        
        # Append table to database
        db.TableDefs.Append(tbl)
        
        # Insert some test data using DoCmd
        app.DoCmd.SetWarnings(False)
        app.DoCmd.RunSQL("INSERT INTO TestTable (Name, Amount) VALUES ('Test1', 100)")
        app.DoCmd.RunSQL("INSERT INTO TestTable (Name, Amount) VALUES ('Test2', 200)")
        app.DoCmd.SetWarnings(True)
        
        # Create a test query
        query_sql = "SELECT * FROM TestTable WHERE Amount > 150"
        db.CreateQueryDef("TestQuery", query_sql)
        
        # Close the database and quit the setup Access instance
        # The test will create its own Access instance
        app.CloseCurrentDatabase()
        app.Quit()
        app = None
        
        # Wait for Access to fully release the file
        import time
        import gc
        gc.collect()  # Force garbage collection to release COM objects
        time.sleep(2)
        
        yield db_path
        
    except Exception as e:
        pytest.skip(f"Could not create test database: {e}")
        
    finally:
        # Cleanup: close Access if still open (only if setup failed mid-way)
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        
        # Wait for file release
        import time
        import gc
        gc.collect()
        time.sleep(1)
        
        # Delete the database file and lock file
        try:
            lock_file = db_path.replace('.accdb', '.laccdb')
            if os.path.exists(lock_file):
                os.remove(lock_file)
        except Exception:
            pass
        
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
        try:
            app.CloseCurrentDatabase()
        except Exception:
            pass  # Database may already be closed
        try:
            app.Quit()
        except Exception:
            pass  # App may already be quit


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

