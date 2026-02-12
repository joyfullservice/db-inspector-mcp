"""
Detailed benchmark to investigate ODBC connection overhead and outlier spikes.

Addresses specific concerns:
1. Extreme outliers inflating mean (up to 5335ms seen in prior runs)
2. Cold vs warm connection behavior
3. Whether GC or external factors cause spikes
4. Percentile-based analysis for clearer bottleneck identification
5. Comparison of ODBC connect-per-request vs persistent connection

Usage:
    python benchmarks/bench_detailed.py <path_to_accdb_file> [--password PWD] [--iterations N]
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
    times: list[float]  # in seconds
    warmup_times: list[float] | None = None

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
        """Get p-th percentile in ms."""
        sorted_times = sorted(self.times_ms)
        idx = int(len(sorted_times) * p / 100)
        idx = min(idx, len(sorted_times) - 1)
        return sorted_times[idx]

    @property
    def outlier_count(self) -> int:
        """Count values > 2x median (simple outlier detection)."""
        threshold = self.median_ms * 2
        return sum(1 for t in self.times_ms if t > threshold)

    def print_report(self, indent: str = "   ") -> None:
        """Print detailed statistics."""
        print(f"\n{self.name}:")
        if self.warmup_times:
            warmup_ms = [t * 1000 for t in self.warmup_times]
            print(f"{indent}Warmup rounds: {len(warmup_ms)}, "
                  f"range: {min(warmup_ms):.1f}ms - {max(warmup_ms):.1f}ms")
        print(f"{indent}Iterations:  {len(self.times)}")
        print(f"{indent}Mean:        {self.mean_ms:.1f}ms")
        print(f"{indent}Median:      {self.median_ms:.1f}ms")
        print(f"{indent}Stdev:       {self.stdev_ms:.1f}ms")
        print(f"{indent}Min:         {self.min_ms:.1f}ms")
        print(f"{indent}Max:         {self.max_ms:.1f}ms")
        print(f"{indent}P90:         {self.percentile(90):.1f}ms")
        print(f"{indent}P95:         {self.percentile(95):.1f}ms")
        print(f"{indent}P99:         {self.percentile(99):.1f}ms")
        print(f"{indent}Outliers:    {self.outlier_count} (>{self.median_ms * 2:.0f}ms)")
        if self.outlier_count > 0:
            threshold = self.median_ms * 2
            outlier_vals = sorted([t for t in self.times_ms if t > threshold], reverse=True)
            print(f"{indent}  Outlier values: {', '.join(f'{v:.1f}ms' for v in outlier_vals[:5])}")


def run_bench(name: str, func, iterations: int, warmup: int = 3, 
              disable_gc: bool = False) -> BenchResult:
    """Run a benchmark with warmup rounds and optional GC control."""
    warmup_times = []
    for _ in range(warmup):
        start = time.perf_counter()
        func()
        warmup_times.append(time.perf_counter() - start)

    times = []
    for _ in range(iterations):
        if disable_gc:
            gc.disable()
        try:
            start = time.perf_counter()
            func()
            elapsed = time.perf_counter() - start
            times.append(elapsed)
        finally:
            if disable_gc:
                gc.enable()

    return BenchResult(name=name, times=times, warmup_times=warmup_times)


def benchmark_odbc_detailed(db_path: str, password: str | None, iterations: int) -> list[BenchResult]:
    """Detailed ODBC benchmarks with warmup and percentile analysis."""
    try:
        import pyodbc
    except ImportError:
        print("ODBC BENCHMARKS - SKIPPED (pyodbc not installed)")
        return []

    driver = "{Microsoft Access Driver (*.mdb, *.accdb)}"
    conn_str = f"Driver={driver};DBQ={db_path};"
    if password:
        conn_str += f"PWD={password};"

    print("=" * 70)
    print("ODBC DETAILED BENCHMARKS")
    print("=" * 70)

    # Discover a user table
    conn = pyodbc.connect(conn_str, timeout=30)
    table_name = None
    for row in conn.cursor().tables(tableType="TABLE"):
        if not row.table_name.startswith("MSys"):
            table_name = row.table_name
            break
    conn.close()

    test_query = f"SELECT TOP 1 * FROM [{table_name}]" if table_name else "SELECT Now() AS ts"
    print(f"   Test table: {table_name or '(none - using SELECT Now())'}")
    print(f"   Test query: {test_query}")
    print(f"   Iterations: {iterations} (+ 3 warmup)")

    results = []

    # --- 1. Connection open + close (no query) ---
    def odbc_connect_only():
        c = pyodbc.connect(conn_str, timeout=30)
        c.close()

    results.append(run_bench(
        "1. ODBC Connect + Close (no query)", odbc_connect_only, iterations
    ))
    results[-1].print_report()

    # --- 2. Connection open + close (no GC) ---
    results.append(run_bench(
        "2. ODBC Connect + Close (GC disabled during measurement)",
        odbc_connect_only, iterations, disable_gc=True
    ))
    results[-1].print_report()

    # --- 3. Connection open + simple query + close ---
    def odbc_connect_query_close():
        c = pyodbc.connect(conn_str, timeout=30)
        cur = c.cursor()
        cur.execute(test_query)
        cur.fetchall()
        cur.close()
        c.close()

    results.append(run_bench(
        "3. ODBC Connect + Query + Close", odbc_connect_query_close, iterations
    ))
    results[-1].print_report()

    # --- 4. Persistent connection + query (baseline) ---
    persistent_conn = pyodbc.connect(conn_str, timeout=30)

    def odbc_persistent_query():
        cur = persistent_conn.cursor()
        cur.execute(test_query)
        cur.fetchall()
        cur.close()

    results.append(run_bench(
        "4. Persistent Connection + Query (baseline)", odbc_persistent_query, iterations
    ))
    results[-1].print_report()
    persistent_conn.close()

    # --- 5. list_tables via MSysObjects (MCP-style) ---
    list_tables_sql = """
        SELECT MSysObjects.Name AS table_name, 'dbo' AS table_schema
        FROM MSysObjects
        WHERE MSysObjects.Type = 1 AND MSysObjects.Flags = 0
        AND MSysObjects.Name NOT LIKE 'MSys%'
        ORDER BY MSysObjects.Name
    """

    def odbc_list_tables():
        c = pyodbc.connect(conn_str, timeout=30)
        cur = c.cursor()
        try:
            cur.execute(list_tables_sql)
            cur.fetchall()
        except Exception:
            # Fall back to catalog if MSysObjects not accessible
            for row in cur.tables(tableType="TABLE"):
                pass
        cur.close()
        c.close()

    results.append(run_bench(
        "5. ODBC list_tables (connect-per-request, MCP pattern)",
        odbc_list_tables, iterations
    ))
    results[-1].print_report()

    # --- 6. list_tables via catalog (ODBC catalog approach) ---
    def odbc_list_tables_catalog():
        c = pyodbc.connect(conn_str, timeout=30)
        cur = c.cursor()
        tables = []
        for row in cur.tables(tableType="TABLE"):
            if not row.table_name.startswith("MSys"):
                tables.append(row.table_name)
        cur.close()
        c.close()

    results.append(run_bench(
        "6. ODBC list_tables via catalog (connect-per-request)",
        odbc_list_tables_catalog, iterations
    ))
    results[-1].print_report()

    # --- 7. count_query_results pattern ---
    count_query = f"SELECT COUNT(*) AS cnt FROM (SELECT TOP 10 * FROM [{table_name}]) AS subquery" if table_name else "SELECT 1 AS cnt"

    def odbc_count_query():
        c = pyodbc.connect(conn_str, timeout=30)
        cur = c.cursor()
        cur.execute(count_query)
        cur.fetchone()
        cur.close()
        c.close()

    results.append(run_bench(
        "7. ODBC count_query_results (connect-per-request, MCP pattern)",
        odbc_count_query, iterations
    ))
    results[-1].print_report()

    # --- 8. Back-to-back rapid connections (stress test) ---
    print("\n--- Rapid-fire connection test (10 connections, no delay) ---")
    rapid_times = []
    for i in range(10):
        start = time.perf_counter()
        c = pyodbc.connect(conn_str, timeout=30)
        c.close()
        elapsed = time.perf_counter() - start
        rapid_times.append(elapsed)
        print(f"   #{i+1}: {elapsed*1000:.1f}ms")
    rapid_result = BenchResult(name="8. Rapid-fire connections (10x, no delay)", times=rapid_times)
    rapid_result.print_report()
    results.append(rapid_result)

    return results


def benchmark_com_detailed(db_path: str, iterations: int) -> list[BenchResult]:
    """Detailed COM benchmarks."""
    try:
        import win32com.client
        from win32com.client import gencache
    except ImportError:
        print("\nCOM BENCHMARKS - SKIPPED (pywin32 not installed)")
        return []

    print("\n" + "=" * 70)
    print("COM DETAILED BENCHMARKS")
    print("=" * 70)

    results = []

    # --- 1. Cold start ---
    print("\n1. Cold start - EnsureDispatch('Access.Application'):")
    print("   (Single measurement)")
    gc.collect()
    start = time.perf_counter()
    app = gencache.EnsureDispatch("Access.Application")
    import ctypes
    try:
        ctypes.windll.user32.ShowWindow(app.hWndAccessApp(), 5)
    except Exception:
        pass
    cold_start_time = time.perf_counter() - start
    print(f"   Time: {cold_start_time*1000:.1f}ms")

    dbe = app.DBEngine

    # --- 2. DBEngine.OpenDatabase + Close ---
    def com_open_close_db():
        db = dbe.OpenDatabase(db_path, False, True)
        db.Close()

    results.append(run_bench(
        "2. COM DBEngine.OpenDatabase + Close", com_open_close_db, iterations
    ))
    results[-1].print_report()

    # --- 3. Open DB + list TableDefs + Close ---
    def com_list_tables():
        db = dbe.OpenDatabase(db_path, False, True)
        names = [td.Name for td in db.TableDefs if not td.Name.startswith("MSys")]
        db.Close()

    results.append(run_bench(
        "3. COM Open DB + list TableDefs + Close (MCP pattern)",
        com_list_tables, iterations
    ))
    results[-1].print_report()

    # --- 4. Persistent DB + list TableDefs ---
    persistent_db = dbe.OpenDatabase(db_path, False, True)

    def com_persistent_list_tables():
        names = [td.Name for td in persistent_db.TableDefs if not td.Name.startswith("MSys")]

    results.append(run_bench(
        "4. COM Persistent DB + list TableDefs (baseline)",
        com_persistent_list_tables, iterations
    ))
    results[-1].print_report()
    persistent_db.Close()

    # --- 5. Open DB + list QueryDefs + Close ---
    def com_list_views():
        db = dbe.OpenDatabase(db_path, False, True)
        names = [qd.Name for qd in db.QueryDefs]
        db.Close()

    results.append(run_bench(
        "5. COM Open DB + list QueryDefs + Close",
        com_list_views, iterations
    ))
    results[-1].print_report()

    # --- 6. GetObject warm start ---
    try:
        app.CloseCurrentDatabase()
    except Exception:
        pass
    app.OpenCurrentDatabase(db_path)
    time.sleep(1)

    def com_getobject():
        warm = win32com.client.GetObject(db_path)

    results.append(run_bench(
        "6. COM GetObject warm start (DB already open in Access)",
        com_getobject, iterations
    ))
    results[-1].print_report()

    # --- 7. CurrentDb() path (cheapest COM path) ---
    def com_currentdb_tables():
        db = app.CurrentDb()
        if db:
            names = [td.Name for td in db.TableDefs if not td.Name.startswith("MSys")]

    results.append(run_bench(
        "7. COM CurrentDb() + list TableDefs (cheapest path)",
        com_currentdb_tables, iterations
    ))
    results[-1].print_report()

    # Cleanup
    try:
        app.CloseCurrentDatabase()
    except Exception:
        pass

    print(f"\n   NOTE: Access Application left running (close manually if desired)")

    return results


def print_summary(odbc_results: list, com_results: list) -> None:
    """Print a comparative summary table."""
    print("\n" + "=" * 70)
    print("COMPARATIVE SUMMARY")
    print("=" * 70)
    print(f"\n{'Benchmark':<55} {'Median':>8} {'P95':>8} {'Max':>8} {'Outliers':>8}")
    print("-" * 87)

    all_results = []
    if odbc_results:
        all_results.extend(odbc_results)
    if com_results:
        all_results.extend(com_results)

    for r in all_results:
        name = r.name[:54]
        print(f"{name:<55} {r.median_ms:>7.1f}ms {r.percentile(95):>7.1f}ms "
              f"{r.max_ms:>7.1f}ms {r.outlier_count:>5}")

    # Key insights
    print("\n" + "=" * 70)
    print("KEY INSIGHTS")
    print("=" * 70)

    if odbc_results and len(odbc_results) >= 4:
        # ODBC connection overhead
        connect_close = odbc_results[0]  # connect+close
        persistent = odbc_results[3]     # persistent baseline
        overhead_median = connect_close.median_ms - persistent.median_ms
        overhead_p95 = connect_close.percentile(95) - persistent.percentile(95)
        print(f"\n   ODBC Connection Overhead (median): {overhead_median:.1f}ms per request")
        print(f"   ODBC Connection Overhead (P95):    {overhead_p95:.1f}ms per request")
        print(f"   ODBC Max spike:                    {connect_close.max_ms:.1f}ms")
        if connect_close.outlier_count > 0:
            print(f"   ODBC Outlier frequency:            {connect_close.outlier_count}/{len(connect_close.times)} "
                  f"({connect_close.outlier_count/len(connect_close.times)*100:.0f}%)")

    if com_results and len(com_results) >= 4:
        # COM overhead
        com_open_close = com_results[1]  # open+list+close
        com_persistent = com_results[2]  # persistent baseline
        com_overhead_median = com_open_close.median_ms - com_persistent.median_ms
        print(f"\n   COM DB Open/Close Overhead (median): {com_overhead_median:.1f}ms per request")
        print(f"   COM via CurrentDb (cheapest):        {com_results[-1].median_ms:.1f}ms")

    if odbc_results and com_results and len(odbc_results) >= 5 and len(com_results) >= 2:
        # Compare equivalent operations
        odbc_lt = odbc_results[4]   # ODBC list_tables
        com_lt = com_results[1]     # COM list_tables
        print(f"\n   list_tables comparison:")
        print(f"     ODBC (connect-per-request): median={odbc_lt.median_ms:.1f}ms, P95={odbc_lt.percentile(95):.1f}ms")
        print(f"     COM (open-per-request):     median={com_lt.median_ms:.1f}ms, P95={com_lt.percentile(95):.1f}ms")
        speedup = odbc_lt.median_ms / com_lt.median_ms if com_lt.median_ms > 0 else float('inf')
        print(f"     COM is {speedup:.1f}x faster at median")


def main():
    parser = argparse.ArgumentParser(description="Detailed connection benchmark")
    parser.add_argument("db_path", help="Path to Access database file")
    parser.add_argument("--password", "-p", help="Database password", default=None)
    parser.add_argument("--iterations", "-n", type=int, default=30,
                        help="Number of iterations per benchmark (default: 30)")
    parser.add_argument("--odbc-only", action="store_true", help="Skip COM benchmarks")
    parser.add_argument("--com-only", action="store_true", help="Skip ODBC benchmarks")
    args = parser.parse_args()

    print(f"Detailed Connection Benchmark")
    print(f"Database: {args.db_path}")
    print(f"Python:   {sys.version}")
    print(f"Iterations: {args.iterations} per test (+ 3 warmup)")
    print()

    odbc_results = []
    com_results = []

    if not args.com_only:
        odbc_results = benchmark_odbc_detailed(args.db_path, args.password, args.iterations)

    if not args.odbc_only:
        com_results = benchmark_com_detailed(args.db_path, args.iterations)

    print_summary(odbc_results, com_results)

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
