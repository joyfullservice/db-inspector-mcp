"""
Quick test: Which connection methods can read MSysObjects?

MSysObjects is an Access system table that provides object metadata
including object type (tables, queries, forms, reports, modules, etc.)
and distinguishes between query types (e.g., pass-through vs native).

Usage:
    python benchmarks/bench_msysobjects.py <path_to_accdb_file> [--password PWD]
"""

import argparse
import sys
import time


def test_level(label: str, open_db, close_db=None):
    """Test reading MSysObjects at a given level."""
    print(f"\n  {label}:")
    db = None
    try:
        db = open_db()
    except Exception as e:
        print(f"    Could not open database: {str(e)[:120]}")
        return

    # Test 1: OpenRecordset on MSysObjects
    try:
        sql = "SELECT TOP 5 Name, Type, Flags FROM MSysObjects ORDER BY Name"
        start = time.perf_counter()
        rs = db.OpenRecordset(sql, 4)  # dbOpenSnapshot
        rows = []
        while not rs.EOF:
            rows.append({
                "Name": rs.Fields("Name").Value,
                "Type": rs.Fields("Type").Value,
                "Flags": rs.Fields("Flags").Value,
            })
            rs.MoveNext()
        rs.Close()
        elapsed = (time.perf_counter() - start) * 1000
        print(f"    DAO OpenRecordset (SELECT TOP 5): OK ({elapsed:.1f}ms, {len(rows)} rows)")
        for row in rows:
            print(f"      Name={row['Name']!s:30s} Type={row['Type']!s:6s} Flags={row['Flags']}")
    except Exception as e:
        print(f"    DAO OpenRecordset: FAIL — {str(e)[:150]}")

    # Test 2: Full count
    try:
        sql_count = "SELECT COUNT(*) AS cnt FROM MSysObjects"
        start = time.perf_counter()
        rs = db.OpenRecordset(sql_count, 4)
        count = rs.Fields("cnt").Value
        rs.Close()
        elapsed = (time.perf_counter() - start) * 1000
        print(f"    DAO COUNT(*): OK ({elapsed:.1f}ms, {count} objects)")
    except Exception as e:
        print(f"    DAO COUNT(*): FAIL — {str(e)[:150]}")

    # Test 3: Group by Type to see what object types are available
    try:
        sql_types = "SELECT Type, COUNT(*) AS cnt FROM MSysObjects GROUP BY Type ORDER BY Type"
        start = time.perf_counter()
        rs = db.OpenRecordset(sql_types, 4)
        types = []
        while not rs.EOF:
            types.append((rs.Fields("Type").Value, rs.Fields("cnt").Value))
            rs.MoveNext()
        rs.Close()
        elapsed = (time.perf_counter() - start) * 1000
        print(f"    DAO GROUP BY Type: OK ({elapsed:.1f}ms)")

        # Type reference: 1=Table, 5=Query, -32768=Form, -32764=Report,
        # -32761=Module, -32766=Macro, etc.
        type_names = {
            1: "Local Table",
            2: "Access Table",
            3: "Access Table",
            4: "ODBC Linked",
            5: "Query",
            6: "Linked Table",
            8: "SubDatasheet",
            -32768: "Form",
            -32766: "Macro",
            -32764: "Report",
            -32761: "Module",
            -32756: "Data Access Page",
            -32758: "Database Diagram",
        }
        for type_val, cnt in types:
            name = type_names.get(type_val, f"Unknown({type_val})")
            print(f"      Type {type_val:>7} ({name:20s}): {cnt} objects")
    except Exception as e:
        print(f"    DAO GROUP BY Type: FAIL — {str(e)[:150]}")

    if close_db and db:
        try:
            close_db(db)
        except Exception:
            pass


def test_odbc(label: str, conn_str: str):
    """Test reading MSysObjects via ODBC."""
    import pyodbc

    print(f"\n  {label}:")

    try:
        conn = pyodbc.connect(conn_str, timeout=30)
    except Exception as e:
        print(f"    Could not connect: {str(e)[:120]}")
        return

    # Test 1: SELECT TOP 5
    try:
        sql = "SELECT TOP 5 Name, Type, Flags FROM MSysObjects ORDER BY Name"
        start = time.perf_counter()
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        elapsed = (time.perf_counter() - start) * 1000
        print(f"    ODBC SELECT TOP 5: OK ({elapsed:.1f}ms, {len(rows)} rows)")
        for row in rows:
            print(f"      Name={str(row[0]):30s} Type={str(row[1]):6s} Flags={row[2]}")
        cur.close()
    except Exception as e:
        print(f"    ODBC SELECT TOP 5: FAIL — {str(e)[:150]}")

    # Test 2: COUNT(*)
    try:
        sql_count = "SELECT COUNT(*) AS cnt FROM MSysObjects"
        start = time.perf_counter()
        cur = conn.cursor()
        cur.execute(sql_count)
        count = cur.fetchone()[0]
        elapsed = (time.perf_counter() - start) * 1000
        print(f"    ODBC COUNT(*): OK ({elapsed:.1f}ms, {count} objects)")
        cur.close()
    except Exception as e:
        print(f"    ODBC COUNT(*): FAIL — {str(e)[:150]}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Test MSysObjects access at each level")
    parser.add_argument("db_path", help="Path to Access database file")
    parser.add_argument("--password", "-p", help="Database password", default=None)
    args = parser.parse_args()

    db_path = args.db_path
    password = args.password or ""
    dbe_connect = f";PWD={password}" if password else ""

    print("=" * 70)
    print("MSysObjects Access Test")
    print("=" * 70)
    print(f"Database: {db_path}")

    try:
        import win32com.client
        from win32com.client import gencache
    except ImportError:
        print("ERROR: pywin32 not installed")
        sys.exit(1)

    import ctypes

    # --- Setup ---

    # Standalone DBEngine
    standalone_dbe = win32com.client.Dispatch("DAO.DBEngine.120")

    # Access Application
    print("\nStarting Access Application...")
    app = gencache.EnsureDispatch("Access.Application")
    try:
        ctypes.windll.user32.ShowWindow(app.hWndAccessApp(), 5)
    except Exception:
        pass
    app.OpenCurrentDatabase(db_path, False, password)
    time.sleep(0.5)
    app_dbe = app.DBEngine

    # ODBC connection string
    driver = "{Microsoft Access Driver (*.mdb, *.accdb)}"
    conn_str = f"Driver={driver};DBQ={db_path};"
    if password:
        conn_str += f"PWD={password};"

    # --- Tests ---

    print("\n" + "=" * 70)
    print("DAO TESTS")
    print("=" * 70)

    # 1. Standalone DBEngine
    test_level(
        "Standalone DAO.DBEngine.120",
        lambda: standalone_dbe.OpenDatabase(db_path, False, True, dbe_connect),
        lambda db: db.Close(),
    )

    # 2. App DBEngine.OpenDatabase (read-only)
    test_level(
        "App DBEngine.OpenDatabase (ReadOnly=True)",
        lambda: app_dbe.OpenDatabase(db_path, False, True, dbe_connect),
        lambda db: db.Close(),
    )

    # 3. App DBEngine.OpenDatabase (read-write)
    test_level(
        "App DBEngine.OpenDatabase (ReadOnly=False)",
        lambda: app_dbe.OpenDatabase(db_path, False, False, dbe_connect),
        lambda db: db.Close(),
    )

    # 4. App CurrentDb()
    test_level(
        "App CurrentDb()",
        lambda: app.CurrentDb(),
        None,  # Don't close CurrentDb
    )

    print("\n" + "=" * 70)
    print("ODBC TESTS")
    print("=" * 70)

    # 5. ODBC connect-per-request
    test_odbc("ODBC (standard connection)", conn_str)

    # 6. ODBC with SystemDB if applicable
    # Some MSysObjects access requires admin permissions configured via workgroup
    print("\n  (Note: If ODBC failed, it may require 'MSysObjects Read' permissions")
    print("   set via Tools > Security in older Access versions, or the database")
    print("   may need to grant read access to the Admin user on MSysObjects.)")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
