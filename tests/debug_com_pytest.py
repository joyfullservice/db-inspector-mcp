"""
Pytest-specific debug script to replicate the Windows fatal exception: code 0x80010108.

This reproduces the exact pattern from the test fixture that causes the issue.
The fatal exception typically occurs during pytest fixture teardown when
garbage collection tries to release COM objects after Access has quit.

Run with: python -m pytest tests/debug_com_pytest.py -v
"""

import gc
import os
import sys
import tempfile
import time
import uuid

import pytest

# Check if COM is available
try:
    import win32com.client
    COM_AVAILABLE = True
except ImportError:
    COM_AVAILABLE = False


@pytest.fixture
def temp_access_db_basic():
    """
    Basic fixture - quits Access, may cause fatal exception during teardown.
    """
    if not COM_AVAILABLE:
        pytest.skip("pywin32 not available")
    
    if sys.platform != "win32":
        pytest.skip("Windows only")
    
    temp_dir = tempfile.gettempdir()
    unique_id = uuid.uuid4().hex[:8]
    db_path = os.path.join(temp_dir, f"debug_pytest_basic_{unique_id}.accdb")
    
    # Create database
    app = win32com.client.Dispatch("Access.Application")
    app.NewCurrentDatabase(db_path)
    db = app.CurrentDb()
    
    # Create table
    dbLong = 4
    dbText = 10
    dbAutoIncrField = 16
    
    tbl = db.CreateTableDef("TestTable")
    fld_id = tbl.CreateField("ID", dbLong)
    fld_id.Attributes = dbAutoIncrField
    tbl.Fields.Append(fld_id)
    fld_name = tbl.CreateField("Name", dbText, 50)
    tbl.Fields.Append(fld_name)
    db.TableDefs.Append(tbl)
    
    # Close and quit
    app.CloseCurrentDatabase()
    app.Quit()
    
    # Wait for Access to close
    gc.collect()
    time.sleep(2)
    
    yield db_path
    
    # Cleanup
    gc.collect()
    time.sleep(1)
    
    lock_file = db_path.replace('.accdb', '.laccdb')
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
    except Exception:
        pass
    
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
    except Exception:
        pass


@pytest.fixture
def temp_access_db_explicit_cleanup():
    """
    Fixture with explicit COM cleanup - sets variables to None before quit.
    """
    if not COM_AVAILABLE:
        pytest.skip("pywin32 not available")
    
    if sys.platform != "win32":
        pytest.skip("Windows only")
    
    temp_dir = tempfile.gettempdir()
    unique_id = uuid.uuid4().hex[:8]
    db_path = os.path.join(temp_dir, f"debug_pytest_explicit_{unique_id}.accdb")
    
    # Create database
    app = win32com.client.Dispatch("Access.Application")
    app.NewCurrentDatabase(db_path)
    db = app.CurrentDb()
    
    # Create table
    dbLong = 4
    dbText = 10
    dbAutoIncrField = 16
    
    tbl = db.CreateTableDef("TestTable")
    fld_id = tbl.CreateField("ID", dbLong)
    fld_id.Attributes = dbAutoIncrField
    tbl.Fields.Append(fld_id)
    fld_name = tbl.CreateField("Name", dbText, 50)
    tbl.Fields.Append(fld_name)
    db.TableDefs.Append(tbl)
    
    # EXPLICIT CLEANUP: Set db to None before quitting
    app.CloseCurrentDatabase()
    db = None
    gc.collect()
    
    app.Quit()
    app = None
    gc.collect()
    
    time.sleep(2)
    
    yield db_path
    
    # Cleanup
    gc.collect()
    time.sleep(1)
    
    lock_file = db_path.replace('.accdb', '.laccdb')
    try:
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
def test_basic_fixture(temp_access_db_basic):
    """Test using basic fixture - may cause fatal exception on teardown."""
    from db_inspector_mcp.backends.access_com import AccessCOMBackend
    
    backend = AccessCOMBackend(temp_access_db_basic, 30)
    
    try:
        tables = backend.list_tables()
        assert any(t["name"] == "TestTable" for t in tables)
    finally:
        # This is the pattern that can cause issues:
        # Backend quits its Access instance, but pytest still holds references
        if backend._app is not None and backend._owns_app:
            try:
                backend._app.Quit()
            except Exception:
                pass


@pytest.mark.integration
@pytest.mark.skipif(not COM_AVAILABLE, reason="Access COM not available")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_explicit_cleanup_fixture(temp_access_db_explicit_cleanup):
    """Test using explicit cleanup fixture."""
    from db_inspector_mcp.backends.access_com import AccessCOMBackend
    
    backend = AccessCOMBackend(temp_access_db_explicit_cleanup, 30)
    
    try:
        tables = backend.list_tables()
        assert any(t["name"] == "TestTable" for t in tables)
    finally:
        # Use backend's close method which respects ownership
        backend.close()


@pytest.mark.integration
@pytest.mark.skipif(not COM_AVAILABLE, reason="Access COM not available")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_no_cleanup(temp_access_db_basic):
    """Test that deliberately doesn't clean up - let garbage collection handle it."""
    from db_inspector_mcp.backends.access_com import AccessCOMBackend
    
    backend = AccessCOMBackend(temp_access_db_basic, 30)
    
    tables = backend.list_tables()
    assert any(t["name"] == "TestTable" for t in tables)
    
    # Deliberately don't call close() or clean up
    # This leaves it to garbage collection, which may cause fatal exception


@pytest.mark.integration
@pytest.mark.skipif(not COM_AVAILABLE, reason="Access COM not available")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_multiple_operations(temp_access_db_basic):
    """Test multiple operations in sequence."""
    from db_inspector_mcp.backends.access_com import AccessCOMBackend
    
    backend = AccessCOMBackend(temp_access_db_basic, 30)
    
    try:
        # Multiple calls should reuse the same connection
        tables1 = backend.list_tables()
        tables2 = backend.list_tables()
        views = backend.list_views()
        
        assert tables1 == tables2
        assert any(t["name"] == "TestTable" for t in tables1)
    finally:
        backend.close()
