"""
Debug script to replicate the Windows fatal exception: code 0x80010108
during COM object cleanup.

This error ("The object invoked has disconnected from its clients") occurs
when Python tries to release COM objects after the associated Access
process has been terminated.

Run this script to see the issue and experiment with solutions.
"""

import gc
import os
import sys
import tempfile
import time
import uuid

# Check if COM is available
try:
    import win32com.client
    import pythoncom
    COM_AVAILABLE = True
except ImportError:
    print("pywin32 not available - install with: pip install pywin32")
    sys.exit(1)


def create_test_database():
    """Create a temporary Access database for testing."""
    temp_dir = tempfile.gettempdir()
    unique_id = uuid.uuid4().hex[:8]
    db_path = os.path.join(temp_dir, f"debug_cleanup_{unique_id}.accdb")
    
    print(f"Creating database: {db_path}")
    
    app = win32com.client.Dispatch("Access.Application")
    app.NewCurrentDatabase(db_path)
    db = app.CurrentDb()
    
    # Create a simple table using DAO
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
    
    # Create a query
    db.CreateQueryDef("TestQuery", "SELECT * FROM TestTable")
    
    app.CloseCurrentDatabase()
    app.Quit()
    
    # Force release of COM objects
    del db
    del app
    gc.collect()
    
    time.sleep(1)  # Wait for Access to fully close
    
    print("Database created successfully")
    return db_path


def scenario_1_basic_cleanup(db_path):
    """
    Scenario 1: Basic cleanup - just quit Access and let Python cleanup.
    This typically causes the fatal exception during garbage collection.
    """
    print("\n" + "=" * 60)
    print("SCENARIO 1: Basic cleanup (causes fatal exception)")
    print("=" * 60)
    
    app = win32com.client.Dispatch("Access.Application")
    app.OpenCurrentDatabase(db_path)
    
    # Use DBEngine to open database (like our backend does)
    dbe = app.DBEngine
    db = dbe.OpenDatabase(db_path, False, False)
    
    print(f"  Database opened: {db.Name}")
    print(f"  Tables: {db.TableDefs.Count}")
    
    # Now cleanup - this order can cause issues
    print("  Closing database...")
    db.Close()
    
    print("  Quitting Access...")
    app.Quit()
    
    # Delete references - garbage collection will try to release COM objects
    # but the Access process is already gone, causing the fatal exception
    print("  Deleting references (this may cause fatal exception on GC)...")
    del db
    del dbe
    del app
    
    print("  Forcing garbage collection...")
    gc.collect()
    
    print("  Waiting for cleanup...")
    time.sleep(2)
    
    print("  Scenario 1 complete")


def scenario_2_explicit_release(db_path):
    """
    Scenario 2: Explicit COM release before quitting Access.
    Uses pythoncom to explicitly release COM objects.
    """
    print("\n" + "=" * 60)
    print("SCENARIO 2: Explicit COM release")
    print("=" * 60)
    
    app = win32com.client.Dispatch("Access.Application")
    app.OpenCurrentDatabase(db_path)
    
    dbe = app.DBEngine
    db = dbe.OpenDatabase(db_path, False, False)
    
    print(f"  Database opened: {db.Name}")
    
    # Cleanup in reverse order with explicit release
    print("  Closing database...")
    db.Close()
    
    # Explicitly release COM objects BEFORE quitting Access
    print("  Releasing db COM object...")
    db = None
    gc.collect()
    
    print("  Releasing dbe COM object...")
    dbe = None
    gc.collect()
    
    print("  Quitting Access...")
    app.Quit()
    
    print("  Releasing app COM object...")
    app = None
    gc.collect()
    
    print("  Waiting for cleanup...")
    time.sleep(2)
    
    print("  Scenario 2 complete")


def scenario_3_no_quit(db_path):
    """
    Scenario 3: Don't quit Access, just close database.
    Let Access stay running (like when user has it open).
    """
    print("\n" + "=" * 60)
    print("SCENARIO 3: No quit (leave Access running)")
    print("=" * 60)
    
    app = win32com.client.Dispatch("Access.Application")
    app.OpenCurrentDatabase(db_path)
    
    dbe = app.DBEngine
    db = dbe.OpenDatabase(db_path, False, False)
    
    print(f"  Database opened: {db.Name}")
    
    # Only close the database, don't quit Access
    print("  Closing database only (not quitting Access)...")
    db.Close()
    
    # Clear references but don't quit
    db = None
    dbe = None
    # Note: NOT calling app.Quit()
    app = None
    
    gc.collect()
    
    print("  Waiting...")
    time.sleep(2)
    
    print("  Scenario 3 complete")
    print("  NOTE: Access is still running! Close it manually.")


def scenario_4_dispatch_dynamic(db_path):
    """
    Scenario 4: Use DispatchEx instead of Dispatch.
    Creates a new process each time which may handle cleanup better.
    """
    print("\n" + "=" * 60)
    print("SCENARIO 4: DispatchEx (separate process)")
    print("=" * 60)
    
    # DispatchEx creates the COM object in its own process
    app = win32com.client.DispatchEx("Access.Application")
    app.OpenCurrentDatabase(db_path)
    
    dbe = app.DBEngine
    db = dbe.OpenDatabase(db_path, False, False)
    
    print(f"  Database opened: {db.Name}")
    
    print("  Closing and quitting...")
    db.Close()
    db = None
    dbe = None
    
    app.Quit()
    app = None
    
    gc.collect()
    time.sleep(2)
    
    print("  Scenario 4 complete")


def scenario_5_try_except_wrapper(db_path):
    """
    Scenario 5: Wrap all COM operations in try-except.
    Suppress the exception during cleanup.
    """
    print("\n" + "=" * 60)
    print("SCENARIO 5: Try-except wrapper for cleanup")
    print("=" * 60)
    
    app = win32com.client.Dispatch("Access.Application")
    app.OpenCurrentDatabase(db_path)
    
    dbe = app.DBEngine
    db = dbe.OpenDatabase(db_path, False, False)
    
    print(f"  Database opened: {db.Name}")
    
    # Cleanup with exception handling
    print("  Cleaning up with exception handling...")
    
    try:
        db.Close()
    except Exception as e:
        print(f"    db.Close() exception: {e}")
    
    try:
        app.Quit()
    except Exception as e:
        print(f"    app.Quit() exception: {e}")
    
    # Clear references
    db = None
    dbe = None
    app = None
    
    gc.collect()
    time.sleep(2)
    
    print("  Scenario 5 complete")


def scenario_6_coinitialize(db_path):
    """
    Scenario 6: Explicitly initialize/uninitialize COM.
    """
    print("\n" + "=" * 60)
    print("SCENARIO 6: Explicit CoInitialize/CoUninitialize")
    print("=" * 60)
    
    # Initialize COM for this thread
    pythoncom.CoInitialize()
    print("  CoInitialize called")
    
    try:
        app = win32com.client.Dispatch("Access.Application")
        app.OpenCurrentDatabase(db_path)
        
        dbe = app.DBEngine
        db = dbe.OpenDatabase(db_path, False, False)
        
        print(f"  Database opened: {db.Name}")
        
        db.Close()
        app.Quit()
        
        del db
        del dbe
        del app
        
        gc.collect()
        
    finally:
        print("  CoUninitialize called")
        pythoncom.CoUninitialize()
    
    time.sleep(2)
    print("  Scenario 6 complete")


def cleanup_database(db_path):
    """Remove the test database."""
    time.sleep(1)
    gc.collect()
    time.sleep(1)
    
    # Remove lock file
    lock_file = db_path.replace('.accdb', '.laccdb')
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
    except Exception as e:
        print(f"Could not remove lock file: {e}")
    
    # Remove database
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"\nCleaned up: {db_path}")
    except Exception as e:
        print(f"\nCould not remove database: {e}")


def main():
    print("=" * 60)
    print("COM Cleanup Debug Script")
    print("=" * 60)
    print("\nThis script tests different approaches to COM cleanup")
    print("to understand when the fatal exception occurs.")
    print("\nChoose a scenario to run:")
    print("  1 - Basic cleanup (typically causes fatal exception)")
    print("  2 - Explicit COM release before quit")
    print("  3 - No quit (leave Access running)")
    print("  4 - DispatchEx (separate process)")
    print("  5 - Try-except wrapper")
    print("  6 - Explicit CoInitialize/CoUninitialize")
    print("  A - Run all scenarios")
    print("  Q - Quit")
    
    choice = input("\nEnter choice: ").strip().upper()
    
    if choice == 'Q':
        return
    
    db_path = create_test_database()
    
    try:
        if choice == '1':
            scenario_1_basic_cleanup(db_path)
        elif choice == '2':
            scenario_2_explicit_release(db_path)
        elif choice == '3':
            scenario_3_no_quit(db_path)
        elif choice == '4':
            scenario_4_dispatch_dynamic(db_path)
        elif choice == '5':
            scenario_5_try_except_wrapper(db_path)
        elif choice == '6':
            scenario_6_coinitialize(db_path)
        elif choice == 'A':
            # Run all scenarios - create fresh db for each
            for scenario_num, scenario_func in [
                (1, scenario_1_basic_cleanup),
                (2, scenario_2_explicit_release),
                # Skip 3 as it leaves Access running
                (4, scenario_4_dispatch_dynamic),
                (5, scenario_5_try_except_wrapper),
                (6, scenario_6_coinitialize),
            ]:
                try:
                    scenario_func(db_path)
                except Exception as e:
                    print(f"  Scenario {scenario_num} error: {e}")
                time.sleep(2)
        else:
            print("Invalid choice")
    
    finally:
        cleanup_database(db_path)
    
    print("\n" + "=" * 60)
    print("Done - check for any fatal exception messages above")
    print("=" * 60)


if __name__ == "__main__":
    main()
