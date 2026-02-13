"""
Benchmark: DAO SQL execution vs ODBC connect-per-request.

Tests whether using CurrentDb.OpenRecordset() and CurrentDb.Execute()
can replace the ODBC backend for SQL operations, eliminating the
~350ms ODBC connection overhead per call.

Usage:
    python benchmarks/bench_dao_vs_odbc.py <path_to_accdb_file> [--password PWD] [--iterations N] [--table TABLE]
"""

import argparse
import gc
import statistics
import sys
import time
from dataclasses import dataclass


@dataclass
class BenchResult:
    """Stores benchmark timing results with rich statistics."""
    name: str
    times: list[float]
    warmup_times: list[float] | None = None
    extra_info: str | None = None

    @property
    def times_ms(self) -> list[float]:
        return [t * 1000 for t in self.times]

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.times_ms)

    @property
    def median_ms(self) -> float:
        return statistics.median(self.times_ms)

    @property
    def stdev_ms(self) -> float:
        return statistics.stdev(self.times_ms) if len(self.times_ms) > 1 else 0.0

    @property
    def min_ms(self) -> float:
        return min(self.times_ms)

    @property
    def max_ms(self) -> float:
        return max(self.times_ms)

    def percentile(self, p: float) -> float:
        sorted_times = sorted(self.times_ms)
        idx = int(len(sorted_times) * p / 100)
        idx = min(idx, len(sorted_times) - 1)
        return sorted_times[idx]

    def print_report(self, indent: str = "   ") -> None:
        print(f"\n{self.name}:")
        if self.warmup_times:
            warmup_ms = [t * 1000 for t in self.warmup_times]
            print(f"{indent}Warmup: {len(warmup_ms)} rounds, "
                  f"range: {min(warmup_ms):.1f}ms - {max(warmup_ms):.1f}ms")
        print(f"{indent}Iterations:  {len(self.times)}")
        print(f"{indent}Mean:        {self.mean_ms:.1f}ms")
        print(f"{indent}Median:      {self.median_ms:.1f}ms")
        print(f"{indent}Stdev:       {self.stdev_ms:.1f}ms")
        print(f"{indent}Min:         {self.min_ms:.1f}ms")
        print(f"{indent}Max:         {self.max_ms:.1f}ms")
        print(f"{indent}P90:         {self.percentile(90):.1f}ms")
        print(f"{indent}P95:         {self.percentile(95):.1f}ms")
        if self.extra_info:
            print(f"{indent}Info:        {self.extra_info}")


def run_bench(name: str, func, iterations: int, warmup: int = 3) -> BenchResult:
    """Run a benchmark with warmup rounds."""
    warmup_times = []
    for _ in range(warmup):
        start = time.perf_counter()
        func()
        warmup_times.append(time.perf_counter() - start)

    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        func()
        times.append(time.perf_counter() - start)

    return BenchResult(name=name, times=times, warmup_times=warmup_times)


def main():
    parser = argparse.ArgumentParser(description="DAO vs ODBC SQL execution benchmark")
    parser.add_argument("db_path", help="Path to Access database file")
    parser.add_argument("--password", "-p", help="Database password", default=None)
    parser.add_argument("--iterations", "-n", type=int, default=30,
                        help="Number of iterations per benchmark (default: 30)")
    parser.add_argument("--table", "-t", help="Table name for DAO benchmarks (default: auto-detect)", default=None)
    args = parser.parse_args()

    db_path = args.db_path
    iterations = args.iterations

    print(f"DAO vs ODBC SQL Execution Benchmark")
    print(f"Database:   {db_path}")
    print(f"Python:     {sys.version}")
    print(f"Iterations: {iterations} per test (+ 3 warmup)")

    # =========================================================================
    # Setup: find a test table and establish connections
    # =========================================================================
    import pyodbc

    driver = "{Microsoft Access Driver (*.mdb, *.accdb)}"
    conn_str = f"Driver={driver};DBQ={db_path};"
    if args.password:
        conn_str += f"PWD={args.password};"

    # Find a user table via ODBC catalog
    conn = pyodbc.connect(conn_str, timeout=30)
    table_name = None
    for row in conn.cursor().tables(tableType="TABLE"):
        if not row.table_name.startswith("MSys"):
            table_name = row.table_name
            break
    conn.close()

    if not table_name:
        print("ERROR: No user tables found in database")
        sys.exit(1)

    print(f"Test table: {table_name}")

    # Test queries
    select_top1 = f"SELECT TOP 1 * FROM [{table_name}]"
    select_top10 = f"SELECT TOP 10 * FROM [{table_name}]"
    count_query = f"SELECT COUNT(*) AS cnt FROM [{table_name}]"
    # Wrapped count (MCP pattern)
    wrapped_count = f"SELECT COUNT(*) AS cnt FROM ({select_top10}) AS subquery"

    print(f"\nQueries:")
    print(f"  SELECT TOP 1:  {select_top1}")
    print(f"  SELECT TOP 10: {select_top10}")
    print(f"  COUNT(*):      {count_query}")
    print(f"  Wrapped COUNT: {wrapped_count}")

    # =========================================================================
    # Setup COM
    # =========================================================================
    try:
        import win32com.client
        from win32com.client import gencache
    except ImportError:
        print("\nERROR: pywin32 not installed. COM benchmarks require pywin32.")
        sys.exit(1)

    import ctypes

    password = args.password or ""

    # Create the Access Application via EnsureDispatch (early-bound COM).
    # We intentionally do NOT use GetObject(db_path) here because for a
    # password-protected database that isn't already open, GetObject triggers
    # Windows to launch Access and open the file WITHOUT a password, which
    # pops up a password dialog and hangs the script.
    #
    # Instead: create Access, then OpenCurrentDatabase with the password.
    try:
        app = gencache.EnsureDispatch("Access.Application")
        try:
            ctypes.windll.user32.ShowWindow(app.hWndAccessApp(), 5)  # SW_SHOW
        except Exception:
            pass
        print("\nAccess: Created instance via EnsureDispatch")
    except Exception as e:
        print(f"\nERROR: Could not start Access: {e}")
        sys.exit(1)

    # Open the database with the password.  OpenCurrentDatabase(filepath,
    # Exclusive, bstrPassword) authenticates once; after that CurrentDb()
    # returns the open database with no further prompts.
    print(f"   Opening database via OpenCurrentDatabase...")
    app.OpenCurrentDatabase(db_path, False, password)
    time.sleep(0.5)
    cdb = app.CurrentDb()
    print(f"   Database opened — TableDefs: {cdb.TableDefs.Count}")

    dbe = app.DBEngine
    dbe_connect = f";PWD={password}" if password else ""

    results = []

    # =========================================================================
    # SECTION 1: ODBC connect-per-request (current MCP pattern)
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 1: ODBC CONNECT-PER-REQUEST (current MCP pattern)")
    print("=" * 70)

    def odbc_count():
        c = pyodbc.connect(conn_str, timeout=30)
        cur = c.cursor()
        cur.execute(count_query)
        result = cur.fetchone()[0]
        cur.close()
        c.close()
        return result

    r = run_bench("ODBC: COUNT(*) [connect-per-request]", odbc_count, iterations)
    r.print_report()
    results.append(r)

    def odbc_select_top1():
        c = pyodbc.connect(conn_str, timeout=30)
        cur = c.cursor()
        cur.execute(select_top1)
        rows = cur.fetchall()
        cols = [col[0] for col in cur.description]
        cur.close()
        c.close()
        return rows, cols

    r = run_bench("ODBC: SELECT TOP 1 [connect-per-request]", odbc_select_top1, iterations)
    r.print_report()
    results.append(r)

    def odbc_select_top10():
        c = pyodbc.connect(conn_str, timeout=30)
        cur = c.cursor()
        cur.execute(select_top10)
        rows = cur.fetchall()
        cols = [col[0] for col in cur.description]
        cur.close()
        c.close()
        return rows, cols

    r = run_bench("ODBC: SELECT TOP 10 [connect-per-request]", odbc_select_top10, iterations)
    r.print_report()
    results.append(r)

    def odbc_wrapped_count():
        c = pyodbc.connect(conn_str, timeout=30)
        cur = c.cursor()
        cur.execute(wrapped_count)
        result = cur.fetchone()[0]
        cur.close()
        c.close()
        return result

    r = run_bench("ODBC: Wrapped COUNT (MCP pattern) [connect-per-request]",
                  odbc_wrapped_count, iterations)
    r.print_report()
    results.append(r)

    # =========================================================================
    # SECTION 2: ODBC persistent connection (baseline)
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 2: ODBC PERSISTENT CONNECTION (baseline)")
    print("=" * 70)

    persistent_conn = pyodbc.connect(conn_str, timeout=30)

    def odbc_persistent_count():
        cur = persistent_conn.cursor()
        cur.execute(count_query)
        result = cur.fetchone()[0]
        cur.close()
        return result

    r = run_bench("ODBC: COUNT(*) [persistent conn]", odbc_persistent_count, iterations)
    r.print_report()
    results.append(r)

    def odbc_persistent_top10():
        cur = persistent_conn.cursor()
        cur.execute(select_top10)
        rows = cur.fetchall()
        cols = [col[0] for col in cur.description]
        cur.close()
        return rows, cols

    r = run_bench("ODBC: SELECT TOP 10 [persistent conn]", odbc_persistent_top10, iterations)
    r.print_report()
    results.append(r)

    persistent_conn.close()

    # =========================================================================
    # SECTION 3: DAO OpenRecordset (COM-based SQL execution)
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 3: DAO OpenRecordset (COM-based SQL)")
    print("=" * 70)

    # DAO constants
    dbOpenSnapshot = 4

    # Use the same auto-detected table, or a user-specified override
    dao_table = args.table or table_name
    dao_select_top1_sql = f"SELECT TOP 1 * FROM [{dao_table}]"
    dao_select_top10_sql = f"SELECT TOP 10 * FROM [{dao_table}]"
    dao_count_sql = f"SELECT COUNT(*) AS cnt FROM [{dao_table}]"
    dao_wrapped_count_sql = f"SELECT COUNT(*) AS cnt FROM (SELECT TOP 10 * FROM [{dao_table}]) AS subquery"
    print(f"   DAO test table: {dao_table}")

    # Smoke test
    print("   Smoke test: CurrentDb().OpenRecordset...")
    smoke_db = app.CurrentDb()
    smoke_rs = smoke_db.OpenRecordset(dao_select_top1_sql, dbOpenSnapshot)
    print(f"   OK - got {smoke_rs.Fields.Count} fields")
    smoke_rs.Close()

    # --- DAO: COUNT(*) via CurrentDb ---
    def dao_count():
        db = app.CurrentDb()
        rs = db.OpenRecordset(dao_count_sql, dbOpenSnapshot)
        result = rs.Fields("cnt").Value
        rs.Close()
        return result

    r = run_bench("DAO: COUNT(*) [CurrentDb + OpenRecordset]",
                  dao_count, iterations)
    r.print_report()
    results.append(r)

    # --- DAO: COUNT(*) with cached CurrentDb reference ---
    cached_db = app.CurrentDb()

    def dao_count_cached():
        rs = cached_db.OpenRecordset(dao_count_sql, dbOpenSnapshot)
        result = rs.Fields("cnt").Value
        rs.Close()
        return result

    r = run_bench("DAO: COUNT(*) [cached CurrentDb ref]",
                  dao_count_cached, iterations)
    r.print_report()
    results.append(r)

    # --- DAO: SELECT TOP 1 ---
    def dao_select_top1():
        db = app.CurrentDb()
        rs = db.OpenRecordset(dao_select_top1_sql, dbOpenSnapshot)
        row = {}
        for i in range(rs.Fields.Count):
            fld = rs.Fields(i)
            row[fld.Name] = fld.Value
        rs.Close()
        return row

    r = run_bench("DAO: SELECT TOP 1 [CurrentDb + OpenRecordset]",
                  dao_select_top1, iterations)
    r.print_report()
    results.append(r)

    # --- DAO: SELECT TOP 10 ---
    def dao_select_top10():
        db = app.CurrentDb()
        rs = db.OpenRecordset(dao_select_top10_sql, dbOpenSnapshot)
        rows = []
        field_count = rs.Fields.Count
        field_names = [rs.Fields(i).Name for i in range(field_count)]
        while not rs.EOF:
            row = {}
            for i in range(field_count):
                row[field_names[i]] = rs.Fields(i).Value
            rows.append(row)
            rs.MoveNext()
        rs.Close()
        return rows

    r = run_bench("DAO: SELECT TOP 10 [CurrentDb + OpenRecordset]",
                  dao_select_top10, iterations)
    r.extra_info = f"Returns {len(dao_select_top10())} rows"
    r.print_report()
    results.append(r)

    # --- DAO: Wrapped COUNT (MCP pattern) ---
    def dao_wrapped_count():
        db = app.CurrentDb()
        rs = db.OpenRecordset(dao_wrapped_count_sql, dbOpenSnapshot)
        result = rs.Fields("cnt").Value
        rs.Close()
        return result

    r = run_bench("DAO: Wrapped COUNT (MCP pattern) [CurrentDb]",
                  dao_wrapped_count, iterations)
    r.print_report()
    results.append(r)

    # =========================================================================
    # SECTION 4: DAO via DBEngine.OpenDatabase (per-request, like current COM)
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 4: DAO DBEngine.OpenDatabase (per-request open/close)")
    print("=" * 70)

    dbe = app.DBEngine
    # DBEngine.OpenDatabase Connect parameter for password-protected databases
    dbe_connect = f";PWD={password}" if password else ""

    def dao_dbe_count():
        db = dbe.OpenDatabase(db_path, False, True, dbe_connect)  # Shared, ReadOnly
        rs = db.OpenRecordset(dao_count_sql, dbOpenSnapshot)
        result = rs.Fields("cnt").Value
        rs.Close()
        db.Close()
        return result

    r = run_bench("DAO: COUNT(*) via DBEngine.OpenDatabase [per-request]",
                  dao_dbe_count, iterations)
    r.print_report()
    results.append(r)

    def dao_dbe_top10():
        db = dbe.OpenDatabase(db_path, False, True, dbe_connect)
        rs = db.OpenRecordset(dao_select_top10_sql, dbOpenSnapshot)
        rows = []
        field_count = rs.Fields.Count
        field_names = [rs.Fields(i).Name for i in range(field_count)]
        while not rs.EOF:
            row = {}
            for i in range(field_count):
                row[field_names[i]] = rs.Fields(i).Value
            rows.append(row)
            rs.MoveNext()
        rs.Close()
        db.Close()
        return rows

    r = run_bench("DAO: SELECT TOP 10 via DBEngine.OpenDatabase [per-request]",
                  dao_dbe_top10, iterations)
    r.print_report()
    results.append(r)

    # =========================================================================
    # SECTION 5: Database.Execute with dbFailOnError
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 5: Database.Execute with dbFailOnError")
    print("=" * 70)

    dbFailOnError = 128  # DAO constant

    # Execute requires a writable database reference
    exec_db = dbe.OpenDatabase(db_path, False, False, dbe_connect)  # ReadOnly=False

    def dao_execute_create_drop():
        try:
            exec_db.Execute("DROP TABLE [__bench_temp]", dbFailOnError)
        except Exception:
            pass
        exec_db.Execute(
            "CREATE TABLE [__bench_temp] (id LONG, nm TEXT(50))",
            dbFailOnError
        )
        exec_db.Execute("DROP TABLE [__bench_temp]", dbFailOnError)

    r = run_bench("DAO: Execute CREATE+DROP temp table [dbFailOnError]",
                  dao_execute_create_drop, iterations)
    r.print_report()
    results.append(r)

    # Test Execute with INSERT (action query)
    try:
        exec_db.Execute("DROP TABLE [__bench_temp]", dbFailOnError)
    except Exception:
        pass
    exec_db.Execute(
        "CREATE TABLE [__bench_temp] (id LONG, nm TEXT(50))",
        dbFailOnError
    )

    def dao_execute_insert():
        exec_db.Execute(
            "INSERT INTO [__bench_temp] (id, nm) VALUES (1, 'test')",
            dbFailOnError
        )

    r = run_bench("DAO: Execute INSERT [dbFailOnError]",
                  dao_execute_insert, iterations)
    r.print_report()
    results.append(r)

    def dao_execute_delete():
        exec_db.Execute("DELETE FROM [__bench_temp]", dbFailOnError)

    r = run_bench("DAO: Execute DELETE [dbFailOnError]",
                  dao_execute_delete, iterations)
    r.print_report()
    results.append(r)

    # Cleanup
    try:
        exec_db.Execute("DROP TABLE [__bench_temp]", dbFailOnError)
    except Exception:
        pass
    exec_db.Close()

    # =========================================================================
    # SECTION 6: Column metadata comparison
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 6: Column metadata (get_query_columns pattern)")
    print("=" * 70)

    # Access doesn't support TOP 0 — use TOP 1 and just read metadata
    top1_query = f"SELECT TOP 1 * FROM [{dao_table}]"

    def odbc_get_columns():
        c = pyodbc.connect(conn_str, timeout=30)
        cur = c.cursor()
        cur.execute(top1_query)
        cols = []
        for col in cur.description:
            cols.append({
                "name": col[0],
                "type": str(col[1]),
                "nullable": col[6] if len(col) > 6 else None,
            })
        cur.close()
        c.close()
        return cols

    r = run_bench("ODBC: get_query_columns [connect-per-request]",
                  odbc_get_columns, iterations)
    r.print_report()
    results.append(r)

    def dao_get_columns():
        db = app.CurrentDb()
        rs = db.OpenRecordset(top1_query, dbOpenSnapshot)
        cols = []
        for i in range(rs.Fields.Count):
            fld = rs.Fields(i)
            cols.append({
                "name": fld.Name,
                "type": fld.Type,
                "size": fld.Size,
                "required": fld.Required,
            })
        rs.Close()
        return cols

    r = run_bench("DAO: get_query_columns [CurrentDb + OpenRecordset]",
                  dao_get_columns, iterations)
    r.print_report()
    results.append(r)

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("\n" + "=" * 70)
    print("COMPARATIVE SUMMARY")
    print("=" * 70)
    print(f"\n{'Benchmark':<60} {'Median':>8} {'P95':>8} {'Max':>8}")
    print("-" * 84)

    for r in results:
        name = r.name[:59]
        print(f"{name:<60} {r.median_ms:>7.1f}ms {r.percentile(95):>7.1f}ms "
              f"{r.max_ms:>7.1f}ms")

    # Direct comparisons
    print("\n" + "=" * 70)
    print("HEAD-TO-HEAD COMPARISONS")
    print("=" * 70)

    # Build a lookup by name prefix for flexible comparisons
    def find_result(prefix):
        for r in results:
            if prefix in r.name:
                return r
        return None

    comparisons = [
        ("COUNT(*): ODBC connect-per-request vs DAO CurrentDb",
         "ODBC: COUNT(*) [connect-per-request]",
         "DAO: COUNT(*) [CurrentDb"),
        ("COUNT(*): ODBC persistent vs DAO CurrentDb",
         "ODBC: COUNT(*) [persistent",
         "DAO: COUNT(*) [CurrentDb"),
        ("SELECT TOP 10: ODBC connect-per-request vs DAO CurrentDb",
         "ODBC: SELECT TOP 10 [connect-per-request]",
         "DAO: SELECT TOP 10 [CurrentDb"),
        ("Wrapped COUNT (MCP pattern)",
         "ODBC: Wrapped COUNT",
         "DAO: Wrapped COUNT"),
        ("get_query_columns",
         "ODBC: get_query_columns",
         "DAO: get_query_columns"),
    ]

    for label, odbc_prefix, dao_prefix in comparisons:
        odbc_r = find_result(odbc_prefix)
        dao_r = find_result(dao_prefix)
        if odbc_r and dao_r and dao_r.median_ms > 0:
            speedup = odbc_r.median_ms / dao_r.median_ms
            savings = odbc_r.median_ms - dao_r.median_ms
            print(f"\n   {label}:")
            print(f"     ODBC: {odbc_r.median_ms:.1f}ms  |  DAO: {dao_r.median_ms:.1f}ms")
            print(f"     DAO is {speedup:.1f}x faster (saves {savings:.0f}ms per call)")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
