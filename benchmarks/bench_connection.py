"""
Benchmark script to measure Access database connection overhead.

Measures the cost of opening/closing connections vs. keeping them persistent,
to validate the connect-per-request strategy for both ODBC and COM backends.

Usage:
    python benchmarks/bench_connection.py <path_to_accdb_file>

Requires:
    - Microsoft Access ODBC driver installed
    - pyodbc
    - pywin32 (for COM benchmarks, optional)
"""

import gc
import statistics
import sys
import time


def fmt_ms(seconds: float) -> str:
    """Format seconds as milliseconds string."""
    return f"{seconds * 1000:.1f}ms"


def benchmark_odbc(db_path: str, iterations: int = 20) -> None:
    """Benchmark ODBC connection open/close overhead."""
    try:
        import pyodbc
    except ImportError:
        print("=" * 60)
        print("ODBC BENCHMARKS - SKIPPED (pyodbc not installed)")
        print("=" * 60)
        return

    driver = "{Microsoft Access Driver (*.mdb, *.accdb)}"
    conn_str = f"Driver={driver};DBQ={db_path};"

    print("=" * 60)
    print("ODBC BENCHMARKS")
    print("=" * 60)

    # Discover a user table via ODBC catalog to avoid MSysObjects permission issues
    conn = pyodbc.connect(conn_str, timeout=30)
    table_name = None
    for row in conn.cursor().tables(tableType="TABLE"):
        if not row.table_name.startswith("MSys"):
            table_name = row.table_name
            break
    conn.close()

    if table_name:
        test_query = f"SELECT TOP 1 * FROM [{table_name}]"
    else:
        # Database has no user tables – fall back to a lightweight expression
        test_query = "SELECT Now() AS ts"

    print(f"   Test query: {test_query}")

    # --- Benchmark 1: Connection open + close (no query) ---
    print(f"\n1. Connection open + close (no query), {iterations} iterations:")
    times = []
    for _ in range(iterations):
        gc.collect()
        start = time.perf_counter()
        conn = pyodbc.connect(conn_str, timeout=30)
        conn.close()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    print(f"   Mean:   {fmt_ms(statistics.mean(times))}")
    print(f"   Median: {fmt_ms(statistics.median(times))}")
    print(f"   Min:    {fmt_ms(min(times))}")
    print(f"   Max:    {fmt_ms(max(times))}")
    print(f"   Stdev:  {fmt_ms(statistics.stdev(times))}")

    # --- Benchmark 2: Connection open + query + close ---
    print(f"\n2. Connection open + query + close, {iterations} iterations:")
    times = []
    for _ in range(iterations):
        gc.collect()
        start = time.perf_counter()
        conn = pyodbc.connect(conn_str, timeout=30)
        cursor = conn.cursor()
        cursor.execute(test_query)
        cursor.fetchall()
        cursor.close()
        conn.close()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    print(f"   Mean:   {fmt_ms(statistics.mean(times))}")
    print(f"   Median: {fmt_ms(statistics.median(times))}")
    print(f"   Min:    {fmt_ms(min(times))}")
    print(f"   Max:    {fmt_ms(max(times))}")
    print(f"   Stdev:  {fmt_ms(statistics.stdev(times))}")

    # --- Benchmark 3: Persistent connection + query (baseline) ---
    print(f"\n3. Persistent connection + query (baseline), {iterations} iterations:")
    conn = pyodbc.connect(conn_str, timeout=30)
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        cursor = conn.cursor()
        cursor.execute(test_query)
        cursor.fetchall()
        cursor.close()
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    conn.close()

    print(f"   Mean:   {fmt_ms(statistics.mean(times))}")
    print(f"   Median: {fmt_ms(statistics.median(times))}")
    print(f"   Min:    {fmt_ms(min(times))}")
    print(f"   Max:    {fmt_ms(max(times))}")
    print(f"   Stdev:  {fmt_ms(statistics.stdev(times))}")

    # --- Summary ---
    print("\n   --> Per-request overhead = (Benchmark 2 mean) - (Benchmark 3 mean)")


def benchmark_com(db_path: str, iterations: int = 20) -> None:
    """Benchmark COM connection overhead."""
    try:
        import win32com.client
        from win32com.client import gencache
    except ImportError:
        print("\n" + "=" * 60)
        print("COM BENCHMARKS - SKIPPED (pywin32 not installed)")
        print("=" * 60)
        return

    print("\n" + "=" * 60)
    print("COM BENCHMARKS")
    print("=" * 60)

    # --- Benchmark 4: Cold start - EnsureDispatch from scratch ---
    print("\n4. Cold start - EnsureDispatch('Access.Application'):")
    print("   (Single measurement - expensive operation)")
    gc.collect()
    start = time.perf_counter()
    app = gencache.EnsureDispatch("Access.Application")
    # app.Visible = True fails due to DAO type-library collision in Access COM.
    # Use Win32 ShowWindow API via the Access window handle instead.
    import ctypes
    try:
        ctypes.windll.user32.ShowWindow(app.hWndAccessApp(), 5)  # SW_SHOW
    except Exception:
        pass
    elapsed = time.perf_counter() - start
    print(f"   Time: {fmt_ms(elapsed)}")

    # --- Benchmark 5: DBEngine.OpenDatabase + Close ---
    print(f"\n5. DBEngine.OpenDatabase + Close, {iterations} iterations:")
    print("   (Application already running)")
    dbe = app.DBEngine
    times = []
    for _ in range(iterations):
        gc.collect()
        start = time.perf_counter()
        db = dbe.OpenDatabase(db_path, False, True)  # Shared, ReadOnly
        db.Close()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    print(f"   Mean:   {fmt_ms(statistics.mean(times))}")
    print(f"   Median: {fmt_ms(statistics.median(times))}")
    print(f"   Min:    {fmt_ms(min(times))}")
    print(f"   Max:    {fmt_ms(max(times))}")
    print(f"   Stdev:  {fmt_ms(statistics.stdev(times))}")

    # --- Benchmark 6: Open DB + DAO operation + Close DB ---
    print(f"\n6. Open DB + list TableDefs + Close DB, {iterations} iterations:")
    times = []
    for _ in range(iterations):
        gc.collect()
        start = time.perf_counter()
        db = dbe.OpenDatabase(db_path, False, True)
        table_names = [td.Name for td in db.TableDefs]
        db.Close()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    print(f"   Mean:   {fmt_ms(statistics.mean(times))}")
    print(f"   Median: {fmt_ms(statistics.median(times))}")
    print(f"   Min:    {fmt_ms(min(times))}")
    print(f"   Max:    {fmt_ms(max(times))}")
    print(f"   Stdev:  {fmt_ms(statistics.stdev(times))}")
    print(f"   Tables found: {len(table_names)}")

    # --- Benchmark 7: Persistent DB + DAO operation (baseline) ---
    print(f"\n7. Persistent DB + list TableDefs (baseline), {iterations} iterations:")
    db = dbe.OpenDatabase(db_path, False, True)
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        table_names = [td.Name for td in db.TableDefs]
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    db.Close()

    print(f"   Mean:   {fmt_ms(statistics.mean(times))}")
    print(f"   Median: {fmt_ms(statistics.median(times))}")
    print(f"   Min:    {fmt_ms(min(times))}")
    print(f"   Max:    {fmt_ms(max(times))}")
    print(f"   Stdev:  {fmt_ms(statistics.stdev(times))}")

    # --- Benchmark 8: GetObject warm start ---
    print("\n8. GetObject warm start (Access already running with DB open):")
    # Close any previously-open current database before opening
    try:
        app.CloseCurrentDatabase()
    except Exception:
        pass
    app.OpenCurrentDatabase(db_path)
    time.sleep(1)  # Let Access settle
    times = []
    for _ in range(iterations):
        gc.collect()
        start = time.perf_counter()
        warm_app = win32com.client.GetObject(db_path)
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    print(f"   Mean:   {fmt_ms(statistics.mean(times))}")
    print(f"   Median: {fmt_ms(statistics.median(times))}")
    print(f"   Min:    {fmt_ms(min(times))}")
    print(f"   Max:    {fmt_ms(max(times))}")
    print(f"   Stdev:  {fmt_ms(statistics.stdev(times))}")

    # Cleanup
    try:
        app.CloseCurrentDatabase()
    except Exception:
        pass

    # --- Summary ---
    print("\n   --> Per-request overhead = (Benchmark 6 mean) - (Benchmark 7 mean)")

    # Leave Access running (user's responsibility to close)
    print("\n   NOTE: Access Application left running (close manually if desired)")


def main():
    if len(sys.argv) < 2:
        print("Usage: python benchmarks/bench_connection.py <path_to_accdb_file>")
        print("\nExample:")
        print("  python benchmarks/bench_connection.py C:\\path\\to\\database.accdb")
        sys.exit(1)

    db_path = sys.argv[1]

    print(f"Benchmarking connection overhead for: {db_path}")
    print(f"Python: {sys.version}")
    print()

    benchmark_odbc(db_path)
    benchmark_com(db_path)

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)


if __name__ == "__main__":
    main()
