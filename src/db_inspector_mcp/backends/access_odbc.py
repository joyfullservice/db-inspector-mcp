"""Microsoft Access backend implementation using pyodbc."""

import logging
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any

import pyodbc

from .base import DatabaseBackend
from .sql_utils import inject_top_clause, split_cte_prefix

logger = logging.getLogger(__name__)

# Default TTL for cached connections (seconds).  After the last operation
# finishes, the connection is held open for this long before being closed,
# releasing the .laccdb lock.  A new call within the window reuses the
# cached connection (~0.2 ms) instead of reconnecting (~220 ms).
_DEFAULT_CONN_TTL_SECONDS = 5.0


class AccessODBCBackend(DatabaseBackend):
    """Microsoft Access database backend using pyodbc.
    
    Uses a TTL-cached connection: the first operation opens an ODBC
    connection and caches it.  Subsequent calls within the TTL window
    reuse the same connection (~0.2 ms instead of ~220 ms).  After the
    TTL expires with no activity, the connection is closed automatically,
    releasing the .laccdb lock so the user and other processes can work
    with the database freely.
    """
    
    @property
    def sql_dialect(self) -> str:
        """Return 'access' as the SQL dialect."""
        return "access"
    
    def __init__(
        self,
        connection_string: str,
        query_timeout_seconds: int = 30,
        connection_ttl_seconds: float | None = None,
    ):
        """
        Initialize Access ODBC backend.
        
        Args:
            connection_string: ODBC connection string or path to .accdb/.accda/.mdb file
            query_timeout_seconds: Query timeout in seconds
            connection_ttl_seconds: How long (seconds) to keep an idle connection
                open before closing it.  Defaults to 5.0 s.  Set to 0 to
                disable caching (connect-per-request).
        """
        # Ensure connection string includes DBQ parameter for ODBC connections
        connection_string = self._ensure_dbq_parameter(connection_string)
        super().__init__(connection_string, query_timeout_seconds)
        
        # Short label for log messages (just the filename)
        match = re.search(r'DBQ=([^;]+)', connection_string, re.IGNORECASE)
        self._db_label: str = (
            os.path.basename(match.group(1).strip()) if match else "access"
        )

        # TTL connection cache state
        self._conn: pyodbc.Connection | None = None
        self._conn_lock = threading.Lock()
        self._close_timer: threading.Timer | None = None
        self._conn_ttl: float = (
            connection_ttl_seconds
            if connection_ttl_seconds is not None
            else _DEFAULT_CONN_TTL_SECONDS
        )
    
    def _ensure_dbq_parameter(self, connection_string: str) -> str:
        """
        Ensure the connection string includes the DBQ parameter.
        
        For Access ODBC connections, the DBQ parameter is required to specify
        the database file path. This method validates and adds it if missing.
        
        Args:
            connection_string: Original connection string
            
        Returns:
            Connection string with DBQ parameter ensured
        """
        # Check if connection string already contains DBQ (case-insensitive)
        if re.search(r'DBQ\s*=', connection_string, re.IGNORECASE):
            return connection_string
        
        # If connection string looks like an ODBC connection string (contains Driver=)
        # but is missing DBQ, raise an error
        if re.search(r'Driver\s*=', connection_string, re.IGNORECASE):
            raise ValueError(
                "Access ODBC connection string must include the DBQ parameter. "
                "Example: Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\path\\to\\database.accdb;"
            )
        
        # If connection string is just a file path (no Driver=), construct full connection string
        # This handles the case where user provides just the file path
        # Assume it's a file path and construct the connection string
        driver = "{Microsoft Access Driver (*.mdb, *.accdb)}"
        return f"Driver={driver};DBQ={connection_string};"
    
    # ------------------------------------------------------------------
    # TTL-cached connection management
    # ------------------------------------------------------------------

    def _open_connection(self) -> pyodbc.Connection:
        """Create a new ODBC connection (internal, no locking)."""
        return pyodbc.connect(
            self.connection_string,
            timeout=self.query_timeout_seconds,
        )

    def _discard_connection(self) -> None:
        """Close and discard the cached connection (must hold _conn_lock)."""
        if self._close_timer is not None:
            self._close_timer.cancel()
            self._close_timer = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _close_connection_on_timer(self) -> None:
        """Called by the Timer thread after the TTL expires."""
        with self._conn_lock:
            # Only close if the timer hasn't been cancelled/rescheduled
            if self._conn is not None:
                print(
                    f"[{self._db_label}] ODBC connection cache expired "
                    f"(idle {self._conn_ttl}s) — closing connection",
                    file=sys.stderr,
                )
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            self._close_timer = None

    def _schedule_close(self) -> None:
        """Schedule the cached connection to close after the TTL."""
        with self._conn_lock:
            if self._close_timer is not None:
                self._close_timer.cancel()
                self._close_timer = None
            if self._conn is None:
                return  # Already discarded (e.g. after a stale-connection error)
            if self._conn_ttl > 0:
                self._close_timer = threading.Timer(
                    self._conn_ttl, self._close_connection_on_timer
                )
                self._close_timer.daemon = True
                self._close_timer.start()
            else:
                # TTL == 0 → close immediately (connect-per-request mode)
                self._discard_connection()

    def close(self) -> None:
        """Cancel timers and close any cached ODBC connection."""
        with self._conn_lock:
            self._discard_connection()

    @contextmanager
    def _connection(self):
        """
        Context manager that yields a (possibly cached) ODBC connection.

        On the first call, a new connection is opened and cached.  Subsequent
        calls within the TTL window reuse the cached connection.  After the
        caller is done (the ``with`` block exits), a timer is scheduled to
        close the connection after ``_conn_ttl`` seconds of inactivity.  If
        another call arrives before the timer fires, the timer is cancelled
        and the connection is reused.

        If a cached connection turns out to be stale (raises ``pyodbc.Error``),
        it is discarded and the error is re-raised so the caller can retry or
        report it.

        Yields:
            pyodbc.Connection
        """
        with self._conn_lock:
            # Cancel any pending close — we're about to use the connection
            if self._close_timer is not None:
                self._close_timer.cancel()
                self._close_timer = None

            # Create connection if we don't have one
            if self._conn is None:
                self._conn = self._open_connection()

            conn = self._conn

        try:
            yield conn
        except pyodbc.Error:
            # The connection may be stale — discard it so the next call
            # gets a fresh one, then re-raise for the caller.
            with self._conn_lock:
                self._discard_connection()
            raise
        finally:
            self._schedule_close()
    
    def count_query_results(self, query: str) -> int:
        """Count row count by wrapping query in SELECT COUNT(*)."""
        cte, core = split_cte_prefix(query)
        wrapped_query = f"{cte}SELECT COUNT(*) AS cnt FROM ({core}) AS subquery"
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(wrapped_query)
                result = cursor.fetchone()
                return result[0] if result else 0
            finally:
                cursor.close()
    
    def get_query_columns(self, query: str) -> list[dict[str, Any]]:
        """Get column metadata using TOP 1 (Access does not support TOP 0)."""
        cte, core = split_cte_prefix(query)
        wrapped_query = f"{cte}SELECT TOP 1 * FROM ({core}) AS subquery"
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(wrapped_query)
                columns = []
                for col in cursor.description:
                    if col:
                        columns.append({
                            "name": col[0],
                            "type": str(col[1]),
                            "nullable": col[6] if len(col) > 6 else None,
                            "precision": col[4] if len(col) > 4 and col[4] else None,
                            "scale": col[5] if len(col) > 5 and col[5] else None,
                        })
                return columns
            finally:
                cursor.close()
    
    def sum_query_column(self, query: str, column: str) -> float | None:
        """Compute SUM of a column from query results."""
        cte, core = split_cte_prefix(query)
        safe_column = column.replace("]", "]]")
        wrapped_query = f"{cte}SELECT SUM([{safe_column}]) AS sum_val FROM ({core}) AS subquery"
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(wrapped_query)
                result = cursor.fetchone()
                return result[0] if result and result[0] is not None else None
            finally:
                cursor.close()
    
    def measure_query(self, sql: str, max_rows: int) -> dict[str, Any]:
        """Measure query execution time and retrieve limited rows."""
        sql = inject_top_clause(sql, max_rows)
        
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                start_time = time.time()
                cursor.execute(sql)
                rows = cursor.fetchall()
                execution_time_ms = (time.time() - start_time) * 1000
                
                column_names = [col[0] for col in cursor.description] if cursor.description else []
                result_rows = self._sanitize_rows(column_names, rows)
                
                row_count = len(result_rows)
                hit_limit = row_count >= max_rows
                
                return {
                    "execution_time_ms": round(execution_time_ms, 2),
                    "row_count": row_count,
                    "hit_limit": hit_limit,
                }
            finally:
                cursor.close()
    
    def preview(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        """Sample N rows from a query result."""
        query = inject_top_clause(query, max_rows)
        
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(query)
                rows = cursor.fetchall()
                
                column_names = [col[0] for col in cursor.description] if cursor.description else []
                result = self._sanitize_rows(column_names, rows)
                return result
            finally:
                cursor.close()
    
    def explain_query(self, query: str) -> str:
        """
        Get execution plan.
        
        Note: Access doesn't have native EXPLAIN support like SQL Server or PostgreSQL.
        This returns a message indicating that execution plans are not available.
        """
        return "Execution plans are not available for Microsoft Access databases. Access uses a query optimizer, but detailed execution plans are not exposed via ODBC."
    
    def get_object_counts(self) -> dict[str, int | None]:
        """Return object counts. Tries MSysObjects, falls back to ODBC catalog."""
        try:
            sql = "SELECT Type, COUNT(*) AS cnt FROM MSysObjects GROUP BY Type"
            with self._connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute(sql)
                    type_map = {
                        1: "tables", 4: "linked_tables", 5: "queries",
                        6: "linked_tables", -32768: "forms", -32764: "reports",
                        -32766: "macros", -32761: "modules",
                    }
                    counts: dict[str, int | None] = {
                        "tables": 0, "linked_tables": 0, "queries": 0,
                        "forms": 0, "reports": 0, "macros": 0, "modules": 0,
                    }
                    for row in cursor.fetchall():
                        key = type_map.get(row[0])
                        if key is not None:
                            counts[key] = (counts[key] or 0) + row[1]
                    return counts
                finally:
                    cursor.close()
        except Exception:
            # MSysObjects not accessible — fall back to catalog
            pass
        try:
            tables = 0
            views = 0
            with self._connection() as conn:
                cursor = conn.cursor()
                try:
                    for _ in cursor.tables(tableType="TABLE"):
                        tables += 1
                    for _ in cursor.tables(tableType="VIEW"):
                        views += 1
                finally:
                    cursor.close()
            # Only return keys we can actually determine via ODBC catalog
            return {"tables": tables, "queries": views}
        except Exception:
            return {}

    def list_tables(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """
        List all tables using MSysObjects system table.
        
        Falls back to ODBC catalog functions if MSysObjects is not accessible.
        """
        where = (
            "MSysObjects.Type = 1 AND MSysObjects.Flags = 0 "
            "AND MSysObjects.Name NOT LIKE 'MSys%'"
        )
        if name_filter:
            safe = name_filter.replace("'", "''")
            where += f" AND MSysObjects.Name LIKE '%{safe}%'"

        sql = f"""
            SELECT MSysObjects.Name AS table_name
            FROM MSysObjects
            WHERE {where}
            ORDER BY MSysObjects.Name
        """
        try:
            with self._connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute(sql)
                    return [
                        {"name": row[0], "schema": "dbo", "row_count": None}
                        for row in cursor.fetchall()
                    ]
                finally:
                    cursor.close()
        except pyodbc.ProgrammingError as e:
            error_msg = str(e).lower()
            if "msysobjects" in error_msg or "no read permission" in error_msg or "-1907" in str(e):
                try:
                    return self._list_tables_via_catalog(name_filter)
                except Exception as catalog_error:
                    raise RuntimeError(
                        f"Cannot list tables: MSysObjects not accessible. "
                        f"Consider using the 'access_com' backend. "
                        f"Original: {e}. Catalog also failed: {catalog_error}"
                    ) from e
            raise
        except Exception as e:
            try:
                return self._list_tables_via_catalog(name_filter)
            except Exception:
                raise RuntimeError(
                    f"Failed to list tables: {e}. "
                    f"Consider using the 'access_com' backend for better compatibility."
                ) from e

    def _list_tables_via_catalog(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """List tables using ODBC catalog functions as fallback."""
        filt = name_filter.lower() if name_filter else None
        with self._connection() as conn:
            cursor = conn.cursor()
            tables: list[dict[str, Any]] = []
            try:
                for row in cursor.tables(tableType='TABLE'):
                    table_name = row.table_name
                    if table_name.startswith('MSys'):
                        continue
                    if filt and filt not in table_name.lower():
                        continue
                    tables.append({
                        "name": table_name,
                        "schema": row.table_schem or "dbo",
                        "row_count": None,
                    })
            finally:
                cursor.close()
            return tables

    def list_views(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """
        List all views (queries) with their definitions.
        
        Falls back to ODBC catalog functions if MSysObjects is not accessible.
        """
        where = "MSysObjects.Type = 5 AND Left(MSysObjects.Name, 1) <> '~'"
        if name_filter:
            safe = name_filter.replace("'", "''")
            where += f" AND MSysObjects.Name LIKE '%{safe}%'"

        sql = f"""
            SELECT MSysObjects.Name AS view_name
            FROM MSysObjects
            WHERE {where}
            ORDER BY MSysObjects.Name
        """
        try:
            with self._connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute(sql)
                    return [
                        {"name": row[0], "schema": "dbo", "definition": None}
                        for row in cursor.fetchall()
                    ]
                finally:
                    cursor.close()
        except pyodbc.ProgrammingError as e:
            error_msg = str(e).lower()
            if "msysobjects" in error_msg or "msysqueries" in error_msg or "no read permission" in error_msg or "-1907" in str(e):
                try:
                    return self._list_views_via_catalog(name_filter)
                except Exception as catalog_error:
                    raise RuntimeError(
                        f"Cannot list views/queries: MSysObjects not accessible. "
                        f"Consider using the 'access_com' backend. "
                        f"Original: {e}. Catalog also failed: {catalog_error}"
                    ) from e
            raise
        except Exception as e:
            try:
                return self._list_views_via_catalog(name_filter)
            except Exception:
                raise RuntimeError(
                    f"Failed to list views/queries: {e}. "
                    f"Consider using the 'access_com' backend for better compatibility."
                ) from e

    def _list_views_via_catalog(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """List views/queries using ODBC catalog functions as fallback."""
        filt = name_filter.lower() if name_filter else None
        with self._connection() as conn:
            cursor = conn.cursor()
            views: list[dict[str, Any]] = []
            try:
                for row in cursor.tables(tableType='VIEW'):
                    view_name = row.table_name
                    if view_name.startswith('MSys') or view_name.startswith('~'):
                        continue
                    if filt and filt not in view_name.lower():
                        continue
                    views.append({
                        "name": view_name,
                        "schema": row.table_schem or "dbo",
                        "definition": None,
                    })
            finally:
                cursor.close()
            return views
    
    def verify_readonly(self) -> dict[str, Any]:
        """
        Verify user has no write permissions.
        
        Note: Access databases opened via ODBC may have different permission models.
        This checks if we can create temp tables (which should work even in read-only mode).
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            details = []
            readonly = True
            
            try:
                # Try to create a temp table
                try:
                    cursor.execute("CREATE TABLE #test_readonly (id INT)")
                    cursor.execute("DROP TABLE #test_readonly")
                    details.append("✓ Can create temp tables (expected for read-only)")
                except Exception as e:
                    details.append(f"✗ Cannot create temp tables: {str(e)}")
                    readonly = False
                
                # Access doesn't have the same role-based security as SQL Server/PostgreSQL
                # We can't easily check write permissions without trying to write
                details.append("⚠ Access permission model differs from SQL Server/PostgreSQL")
                details.append("⚠ Write permission checks are limited for Access databases")
                
            except Exception as e:
                details.append(f"Error during verification: {str(e)}")
                readonly = False
            finally:
                cursor.close()
            
            return {
                "readonly": readonly,
                "details": "\n".join(details),
            }
