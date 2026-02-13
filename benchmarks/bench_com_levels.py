"""
Benchmark: COM Application-level vs Engine-level database access.

Compares three approaches to DAO database access:
1. Application.CurrentDb() — full Application context (forms, VBA, linked tables)
2. Application.DBEngine.OpenDatabase() — engine-level via Application
3. Standalone DAO.DBEngine.120 — pure engine, no Application process

Purpose: Determine the performance cost of Application-level access
(required for form references, VBA functions, expression service) vs
lighter-weight engine-only access. Informs the COM backend architecture:
should the MCP tool always go through the Application, or can metadata
operations safely use a cheaper path?

Usage:
    python benchmarks/bench_com_levels.py <path_to_accdb_file> [--password PWD] [--iterations N]
"""

import argparse
import statistics
import sys
import time
from dataclasses import dataclass


@dataclass
class BenchResult:
    """Stores benchmark timing results."""
    name: str
    times: list[float]
    warmup_times: list[float] | None = None
    extra_info: str | None = None
    error: str | None = None

    @property
    def times_ms(self) -> list[float]:
        return [t * 1000 for t in self.times]

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.times_ms) if self.times else 0.0

    @property
    def median_ms(self) -> float:
        return statistics.median(self.times_ms) if self.times else 0.0

    @property
    def stdev_ms(self) -> float:
        return statistics.stdev(self.times_ms) if len(self.times) > 1 else 0.0

    @property
    def min_ms(self) -> float:
        return min(self.times_ms) if self.times else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.times_ms) if self.times else 0.0

    def percentile(self, p: float) -> float:
        sorted_times = sorted(self.times_ms)
        idx = int(len(sorted_times) * p / 100)
        idx = min(idx, len(sorted_times) - 1)
        return sorted_times[idx]

    def print_report(self, indent: str = "   ") -> None:
        print(f"\n{self.name}:")
        if self.error:
            print(f"{indent}ERROR: {self.error}")
            return
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
        print(f"{indent}P95:         {self.percentile(95):.1f}ms")
        if self.extra_info:
            print(f"{indent}Info:        {self.extra_info}")


def run_bench(name: str, func, iterations: int, warmup: int = 3) -> BenchResult:
    """Run a benchmark with warmup rounds."""
    try:
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
    except Exception as e:
        return BenchResult(name=name, times=[], error=str(e))


def run_once(name: str, func) -> BenchResult:
    """Run a benchmark exactly once (for cold-start measurements)."""
    try:
        start = time.perf_counter()
        result = func()
        elapsed = time.perf_counter() - start
        return BenchResult(name=name, times=[elapsed], extra_info=str(result) if result else None)
    except Exception as e:
        return BenchResult(name=name, times=[], error=str(e))


def main():
    parser = argparse.ArgumentParser(
        description="COM Application-level vs Engine-level benchmark"
    )
    parser.add_argument("db_path", help="Path to Access database file")
    parser.add_argument("--password", "-p", help="Database password", default=None)
    parser.add_argument("--iterations", "-n", type=int, default=30,
                        help="Number of iterations per benchmark (default: 30)")
    args = parser.parse_args()

    db_path = args.db_path
    password = args.password or ""
    iterations = args.iterations

    print("=" * 70)
    print("COM Application-level vs Engine-level Benchmark")
    print("=" * 70)
    print(f"Database:   {db_path}")
    print(f"Python:     {sys.version}")
    print(f"Iterations: {iterations} per test (+ 3 warmup)")

    try:
        import win32com.client
        from win32com.client import gencache
    except ImportError:
        print("\nERROR: pywin32 not installed.")
        sys.exit(1)

    import ctypes

    results = []
    dbe_connect = f";PWD={password}" if password else ""
    dbOpenSnapshot = 4  # DAO constant

    # =========================================================================
    # SECTION 1: Cold start — how long to create each entry point?
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 1: COLD START COSTS")
    print("=" * 70)
    print("  (Each measured once — these are one-time initialization costs)")

    # 1a. Standalone DAO.DBEngine.120
    def create_standalone_dbe():
        dbe = win32com.client.Dispatch("DAO.DBEngine.120")
        return f"DBEngine version: {dbe.Version}"

    r = run_once("Standalone: Create DAO.DBEngine.120", create_standalone_dbe)
    r.print_report()
    results.append(r)

    # 1b. Access Application via EnsureDispatch
    # We measure this but note it may reuse an existing Access if one is running
    def create_access_app():
        app = gencache.EnsureDispatch("Access.Application")
        try:
            ctypes.windll.user32.ShowWindow(app.hWndAccessApp(), 5)
        except Exception:
            pass
        return f"Access version: {app.Version}"

    r = run_once("Application: Create Access.Application", create_access_app)
    r.print_report()
    results.append(r)

    # Keep the Application for subsequent tests
    app = gencache.EnsureDispatch("Access.Application")
    try:
        ctypes.windll.user32.ShowWindow(app.hWndAccessApp(), 5)
    except Exception:
        pass

    # Open the database at the Application level so CurrentDb() works
    print(f"\n   Opening database via OpenCurrentDatabase...")
    app.OpenCurrentDatabase(db_path, False, password)
    time.sleep(0.5)
    cdb = app.CurrentDb()

    # Find a test table
    table_name = None
    for i in range(cdb.TableDefs.Count):
        td = cdb.TableDefs(i)
        if not td.Name.startswith("MSys") and not td.Name.startswith("~"):
            table_name = td.Name
            break

    if not table_name:
        print("ERROR: No user tables found")
        sys.exit(1)

    print(f"   Database opened — using test table: [{table_name}]")
    print(f"   TableDefs: {cdb.TableDefs.Count}, QueryDefs: {cdb.QueryDefs.Count}")

    # Test queries
    count_sql = f"SELECT COUNT(*) AS cnt FROM [{table_name}]"
    top10_sql = f"SELECT TOP 10 * FROM [{table_name}]"

    # Also keep a standalone DBEngine for repeated tests
    standalone_dbe = win32com.client.Dispatch("DAO.DBEngine.120")

    # =========================================================================
    # SECTION 2: Database open/close overhead (per-request pattern)
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 2: DATABASE OPEN/CLOSE OVERHEAD (per-request pattern)")
    print("=" * 70)
    print("  How much does it cost to open and close the database each call?")

    # 2a. Standalone DBEngine: OpenDatabase + Close
    def standalone_open_close():
        db = standalone_dbe.OpenDatabase(db_path, False, True, dbe_connect)
        db.Close()

    r = run_bench("Standalone DBEngine: OpenDatabase + Close",
                  standalone_open_close, iterations)
    r.print_report()
    results.append(r)

    # 2b. Application.DBEngine: OpenDatabase + Close
    app_dbe = app.DBEngine

    def app_dbe_open_close():
        db = app_dbe.OpenDatabase(db_path, False, True, dbe_connect)
        db.Close()

    r = run_bench("App DBEngine: OpenDatabase + Close",
                  app_dbe_open_close, iterations)
    r.print_report()
    results.append(r)

    # 2c. Application.CurrentDb() — no close needed
    def app_currentdb_call():
        db = app.CurrentDb()
        return db

    r = run_bench("App CurrentDb(): get reference (no close)",
                  app_currentdb_call, iterations)
    r.print_report()
    results.append(r)

    # =========================================================================
    # SECTION 3: METADATA — TableDefs iteration (list_tables pattern)
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 3: METADATA — TableDefs iteration (list_tables pattern)")
    print("=" * 70)

    # 3a. Standalone DBEngine
    def standalone_list_tables():
        db = standalone_dbe.OpenDatabase(db_path, False, True, dbe_connect)
        tables = []
        for i in range(db.TableDefs.Count):
            td = db.TableDefs(i)
            if not td.Name.startswith("MSys"):
                tables.append(td.Name)
        db.Close()
        return tables

    r = run_bench("Standalone: list_tables (open + iterate + close)",
                  standalone_list_tables, iterations)
    r.extra_info = f"Found {len(standalone_list_tables())} tables"
    r.print_report()
    results.append(r)

    # 3b. Application.DBEngine.OpenDatabase
    def app_dbe_list_tables():
        db = app_dbe.OpenDatabase(db_path, False, True, dbe_connect)
        tables = []
        for i in range(db.TableDefs.Count):
            td = db.TableDefs(i)
            if not td.Name.startswith("MSys"):
                tables.append(td.Name)
        db.Close()
        return tables

    r = run_bench("App DBEngine: list_tables (open + iterate + close)",
                  app_dbe_list_tables, iterations)
    r.print_report()
    results.append(r)

    # 3c. Application.CurrentDb()
    def app_currentdb_list_tables():
        db = app.CurrentDb()
        tables = []
        for i in range(db.TableDefs.Count):
            td = db.TableDefs(i)
            if not td.Name.startswith("MSys"):
                tables.append(td.Name)
        return tables

    r = run_bench("App CurrentDb: list_tables (no open/close)",
                  app_currentdb_list_tables, iterations)
    r.print_report()
    results.append(r)

    # =========================================================================
    # SECTION 4: METADATA — QueryDefs iteration (list_views pattern)
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 4: METADATA — QueryDefs iteration (list_views pattern)")
    print("=" * 70)

    # 4a. Standalone DBEngine
    def standalone_list_views():
        db = standalone_dbe.OpenDatabase(db_path, False, True, dbe_connect)
        views = []
        for i in range(db.QueryDefs.Count):
            qd = db.QueryDefs(i)
            views.append(qd.Name)
        db.Close()
        return views

    r = run_bench("Standalone: list_views (open + iterate + close)",
                  standalone_list_views, iterations)
    r.extra_info = f"Found {len(standalone_list_views())} queries"
    r.print_report()
    results.append(r)

    # 4b. Application.DBEngine.OpenDatabase
    def app_dbe_list_views():
        db = app_dbe.OpenDatabase(db_path, False, True, dbe_connect)
        views = []
        for i in range(db.QueryDefs.Count):
            qd = db.QueryDefs(i)
            views.append(qd.Name)
        db.Close()
        return views

    r = run_bench("App DBEngine: list_views (open + iterate + close)",
                  app_dbe_list_views, iterations)
    r.print_report()
    results.append(r)

    # 4c. Application.CurrentDb()
    def app_currentdb_list_views():
        db = app.CurrentDb()
        views = []
        for i in range(db.QueryDefs.Count):
            qd = db.QueryDefs(i)
            views.append(qd.Name)
        return views

    r = run_bench("App CurrentDb: list_views (no open/close)",
                  app_currentdb_list_views, iterations)
    r.print_report()
    results.append(r)

    # =========================================================================
    # SECTION 5: QUERY EXECUTION — COUNT(*)
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 5: QUERY EXECUTION — COUNT(*)")
    print("=" * 70)
    print(f"  Query: {count_sql}")

    # 5a. Standalone DBEngine
    def standalone_count():
        db = standalone_dbe.OpenDatabase(db_path, False, True, dbe_connect)
        rs = db.OpenRecordset(count_sql, dbOpenSnapshot)
        result = rs.Fields("cnt").Value
        rs.Close()
        db.Close()
        return result

    r = run_bench("Standalone: COUNT(*) (open + query + close)",
                  standalone_count, iterations)
    r.extra_info = f"Result: {standalone_count()}"
    r.print_report()
    results.append(r)

    # 5b. Application.DBEngine.OpenDatabase
    def app_dbe_count():
        db = app_dbe.OpenDatabase(db_path, False, True, dbe_connect)
        rs = db.OpenRecordset(count_sql, dbOpenSnapshot)
        result = rs.Fields("cnt").Value
        rs.Close()
        db.Close()
        return result

    r = run_bench("App DBEngine: COUNT(*) (open + query + close)",
                  app_dbe_count, iterations)
    r.print_report()
    results.append(r)

    # 5c. Application.CurrentDb()
    def app_currentdb_count():
        db = app.CurrentDb()
        rs = db.OpenRecordset(count_sql, dbOpenSnapshot)
        result = rs.Fields("cnt").Value
        rs.Close()
        return result

    r = run_bench("App CurrentDb: COUNT(*) (no open/close)",
                  app_currentdb_count, iterations)
    r.print_report()
    results.append(r)

    # =========================================================================
    # SECTION 6: QUERY EXECUTION — SELECT TOP 10
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 6: QUERY EXECUTION — SELECT TOP 10")
    print("=" * 70)
    print(f"  Query: {top10_sql}")

    def _fetch_rows(db, sql):
        """Helper: execute query and fetch all rows via COM field iteration."""
        rs = db.OpenRecordset(sql, dbOpenSnapshot)
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

    # 6a. Standalone DBEngine
    def standalone_top10():
        db = standalone_dbe.OpenDatabase(db_path, False, True, dbe_connect)
        rows = _fetch_rows(db, top10_sql)
        db.Close()
        return rows

    r = run_bench("Standalone: SELECT TOP 10 (open + fetch + close)",
                  standalone_top10, iterations)
    r.extra_info = f"Rows returned: {len(standalone_top10())}"
    r.print_report()
    results.append(r)

    # 6b. Application.DBEngine.OpenDatabase
    def app_dbe_top10():
        db = app_dbe.OpenDatabase(db_path, False, True, dbe_connect)
        rows = _fetch_rows(db, top10_sql)
        db.Close()
        return rows

    r = run_bench("App DBEngine: SELECT TOP 10 (open + fetch + close)",
                  app_dbe_top10, iterations)
    r.print_report()
    results.append(r)

    # 6c. Application.CurrentDb()
    def app_currentdb_top10():
        db = app.CurrentDb()
        rows = _fetch_rows(db, top10_sql)
        return rows

    r = run_bench("App CurrentDb: SELECT TOP 10 (no open/close)",
                  app_currentdb_top10, iterations)
    r.print_report()
    results.append(r)

    # =========================================================================
    # SECTION 7: FUNCTIONAL BOUNDARY — saved queries and expressions
    # =========================================================================
    print("\n" + "=" * 70)
    print("SECTION 7: FUNCTIONAL BOUNDARY")
    print("=" * 70)
    print("  Testing what works at each level beyond simple table queries.")

    # 7a. Try running each saved query at each level
    # This reveals whether queries with VBA/form references break at engine level
    saved_queries = []
    for i in range(cdb.QueryDefs.Count):
        qd = cdb.QueryDefs(i)
        if not qd.Name.startswith("~"):  # Skip temp queries
            saved_queries.append(qd.Name)

    if saved_queries:
        print(f"\n  Found {len(saved_queries)} saved queries. Testing first 5...")
        test_queries = saved_queries[:5]

        for qname in test_queries:
            print(f"\n  --- Query: {qname} ---")

            # Get the SQL for reference
            try:
                qd = cdb.QueryDefs(qname)
                sql_text = qd.SQL[:120].replace('\n', ' ').replace('\r', '')
                print(f"      SQL: {sql_text}...")
            except Exception:
                print(f"      SQL: (could not read)")

            for level_name, open_func, close_needed in [
                ("Standalone", lambda: standalone_dbe.OpenDatabase(db_path, False, True, dbe_connect), True),
                ("App DBEngine", lambda: app_dbe.OpenDatabase(db_path, False, True, dbe_connect), True),
                ("App CurrentDb", lambda: app.CurrentDb(), False),
            ]:
                try:
                    db = open_func()
                    start = time.perf_counter()
                    rs = db.OpenRecordset(qname, dbOpenSnapshot)
                    # Just get field count, don't iterate
                    field_count = rs.Fields.Count
                    elapsed = (time.perf_counter() - start) * 1000
                    rs.Close()
                    if close_needed:
                        db.Close()
                    print(f"      {level_name:15s}: OK  ({elapsed:.1f}ms, {field_count} fields)")
                except Exception as e:
                    err_msg = str(e)[:100]
                    if close_needed:
                        try:
                            db.Close()
                        except Exception:
                            pass
                    print(f"      {level_name:15s}: FAIL — {err_msg}")
    else:
        print("  No saved queries found — skipping saved query test.")

    # 7b. Test Access expression service
    # Nz(), IIf(), and other Access-specific functions rely on the expression service.
    # These may or may not work at engine level depending on ACE version.
    expr_queries = [
        ("IIf expression", f"SELECT IIf(1=1, 'yes', 'no') AS result FROM [{table_name}]"),
        ("Nz expression", f"SELECT Nz(Null, 'default') AS result FROM [{table_name}]"),
    ]

    print(f"\n  --- Access Expression Service ---")
    for expr_name, expr_sql in expr_queries:
        # Use TOP 1 to keep it fast
        test_sql = expr_sql.replace("SELECT ", "SELECT TOP 1 ", 1)
        print(f"\n  {expr_name}: {test_sql}")

        for level_name, open_func, close_needed in [
            ("Standalone", lambda: standalone_dbe.OpenDatabase(db_path, False, True, dbe_connect), True),
            ("App DBEngine", lambda: app_dbe.OpenDatabase(db_path, False, True, dbe_connect), True),
            ("App CurrentDb", lambda: app.CurrentDb(), False),
        ]:
            try:
                db = open_func()
                start = time.perf_counter()
                rs = db.OpenRecordset(test_sql, dbOpenSnapshot)
                val = rs.Fields("result").Value
                elapsed = (time.perf_counter() - start) * 1000
                rs.Close()
                if close_needed:
                    db.Close()
                print(f"      {level_name:15s}: OK  ({elapsed:.1f}ms, value={val})")
            except Exception as e:
                err_msg = str(e)[:100]
                if close_needed:
                    try:
                        db.Close()
                    except Exception:
                        pass
                print(f"      {level_name:15s}: FAIL — {err_msg}")

    # 7c. Test Application.Eval (only available via Application)
    print(f"\n  --- Application.Eval ---")
    try:
        start = time.perf_counter()
        result = app.Eval("1+1")
        elapsed = (time.perf_counter() - start) * 1000
        print(f"      App.Eval('1+1'): {result} ({elapsed:.1f}ms)")
        print(f"      (This is Application-only — no equivalent at engine level)")
    except Exception as e:
        print(f"      App.Eval: FAIL — {e}")

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("\n" + "=" * 70)
    print("COMPARATIVE SUMMARY (repeated benchmarks only)")
    print("=" * 70)

    # Filter to only the repeated benchmarks (not one-shots or Section 7)
    repeated = [r for r in results if len(r.times) > 1 and r.error is None]

    print(f"\n{'Benchmark':<55} {'Median':>8} {'P95':>8} {'Max':>8}")
    print("-" * 80)

    for r in repeated:
        name = r.name[:54]
        print(f"{name:<55} {r.median_ms:>7.1f}ms {r.percentile(95):>7.1f}ms "
              f"{r.max_ms:>7.1f}ms")

    # Group comparisons
    print("\n" + "=" * 70)
    print("HEAD-TO-HEAD: Application.CurrentDb vs Standalone DBEngine")
    print("=" * 70)

    groups = [
        ("Database open/close", "OpenDatabase + Close", "get reference"),
        ("list_tables", "list_tables", "list_tables"),
        ("list_views", "list_views", "list_views"),
        ("COUNT(*)", "COUNT(*)", "COUNT(*)"),
        ("SELECT TOP 10", "SELECT TOP 10", "SELECT TOP 10"),
    ]

    for label, standalone_key, currentdb_key in groups:
        standalone_r = None
        currentdb_r = None
        for r in repeated:
            if "Standalone" in r.name and standalone_key in r.name:
                standalone_r = r
            if "CurrentDb" in r.name and currentdb_key in r.name:
                currentdb_r = r

        if standalone_r and currentdb_r and currentdb_r.median_ms > 0:
            ratio = standalone_r.median_ms / currentdb_r.median_ms
            diff = standalone_r.median_ms - currentdb_r.median_ms
            winner = "CurrentDb" if diff > 0 else "Standalone"
            print(f"\n  {label}:")
            print(f"    Standalone:   {standalone_r.median_ms:.1f}ms")
            print(f"    App DBEngine: ", end="")
            # Find app_dbe result
            for r in repeated:
                if "App DBEngine" in r.name and (standalone_key in r.name or currentdb_key in r.name):
                    print(f"{r.median_ms:.1f}ms")
                    break
            else:
                print("N/A")
            print(f"    App CurrentDb: {currentdb_r.median_ms:.1f}ms")
            print(f"    → {winner} is faster by {abs(diff):.1f}ms ({ratio:.1f}x)")

    print("\n" + "=" * 70)
    print("IMPLICATIONS FOR MCP BACKEND ARCHITECTURE")
    print("=" * 70)
    print("""
  Key questions answered by this benchmark:

  1. Is CurrentDb() meaningfully faster than per-request OpenDatabase?
     → Compare Section 2-6 results above.

  2. Is standalone DBEngine a viable alternative for metadata operations?
     → Compare Standalone vs App DBEngine in Sections 3-4.
     → Check Section 7 for functional gaps.

  3. What is the per-call overhead of maintaining the Application path?
     → CurrentDb() call overhead is in Section 2.

  4. Do saved queries work at engine level?
     → Section 7 shows which queries succeed/fail at each level.
     → Queries referencing Forms, VBA functions, or open recordsets
       will fail at engine level but succeed via CurrentDb().
""")

    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
