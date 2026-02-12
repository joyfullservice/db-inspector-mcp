"""Microsoft Access backend implementation using pyodbc."""

import logging
import re
import threading
import time
from contextlib import contextmanager
from typing import Any

import pyodbc

from .base import DatabaseBackend

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
                logger.debug("TTL expired — closing cached ODBC connection")
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
        wrapped_query = f"SELECT COUNT(*) AS cnt FROM ({query}) AS subquery"
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(wrapped_query)
                result = cursor.fetchone()
                return result[0] if result else 0
            finally:
                cursor.close()
    
    def get_query_columns(self, query: str) -> list[dict[str, Any]]:
        """Get column metadata using TOP 0 to get metadata without fetching data."""
        # Use TOP 0 to get metadata without fetching data
        wrapped_query = f"SELECT TOP 0 * FROM ({query}) AS subquery"
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
        # Access uses square brackets for identifiers
        wrapped_query = f"SELECT SUM([{column}]) AS sum_val FROM ({query}) AS subquery"
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
        # Add TOP clause to limit rows (Access uses TOP like SQL Server)
        if "TOP " not in sql.upper():
            sql_upper = sql.upper().strip()
            if sql_upper.startswith("SELECT"):
                sql = f"SELECT TOP {max_rows} " + sql[6:].lstrip()
            else:
                sql = f"SELECT TOP {max_rows} * FROM ({sql}) AS subquery"
        
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                start_time = time.time()
                cursor.execute(sql)
                rows = cursor.fetchall()
                execution_time_ms = (time.time() - start_time) * 1000
                
                # Convert rows to sanitized dictionaries (handles bytes, Decimal, etc.)
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
        # Add TOP clause to limit rows
        if "TOP " not in query.upper():
            query_upper = query.upper().strip()
            if query_upper.startswith("SELECT"):
                query = f"SELECT TOP {max_rows} " + query[6:].lstrip()
            else:
                query = f"SELECT TOP {max_rows} * FROM ({query}) AS subquery"
        
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(query)
                rows = cursor.fetchall()
                
                # Convert rows to sanitized dictionaries (handles bytes, Decimal, etc.)
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
    
    def list_tables(self) -> list[dict[str, Any]]:
        """
        List all tables using MSysObjects system table.
        
        Falls back to ODBC catalog functions if MSysObjects is not accessible.
        """
        # First, try MSysObjects (most reliable when accessible)
        sql = """
            SELECT 
                MSysObjects.Name AS table_name,
                'dbo' AS table_schema
            FROM MSysObjects
            WHERE MSysObjects.Type = 1
            AND MSysObjects.Flags = 0
            AND MSysObjects.Name NOT LIKE 'MSys%'
            ORDER BY MSysObjects.Name
        """
        try:
            with self._connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute(sql)
                    rows = cursor.fetchall()
                    
                    tables = []
                    for row in rows:
                        tables.append({
                            "name": row[0],
                            "schema": "dbo",  # Access doesn't have schemas, use default
                            "row_count": None,  # Could add count if needed
                        })
                    return tables
                finally:
                    cursor.close()
        except pyodbc.ProgrammingError as e:
            # Check if it's a permission error on MSysObjects
            error_msg = str(e).lower()
            if "msysobjects" in error_msg or "no read permission" in error_msg or "-1907" in str(e):
                # Try alternative method using ODBC catalog functions
                try:
                    return self._list_tables_via_catalog()
                except Exception as catalog_error:
                    # If catalog method also fails, raise a helpful error
                    raise RuntimeError(
                        f"Cannot list tables: MSysObjects system table is not accessible "
                        f"(permission denied). This is common with Access databases that have "
                        f"restricted system table access. Consider using the 'access_com' backend "
                        f"instead, which uses COM automation and can access table definitions directly. "
                        f"Original error: {e}. Catalog method also failed: {catalog_error}"
                    ) from e
            # Re-raise if it's a different error
            raise
        except Exception as e:
            # For other errors, try catalog method as fallback
            try:
                return self._list_tables_via_catalog()
            except Exception:
                # If catalog method fails, raise original error
                raise RuntimeError(
                    f"Failed to list tables: {e}. "
                    f"Consider using the 'access_com' backend for better compatibility."
                ) from e
    
    def _list_tables_via_catalog(self) -> list[dict[str, Any]]:
        """
        List tables using ODBC catalog functions as fallback.
        
        This method uses pyodbc's tables() method which queries the ODBC catalog.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            # Use ODBC catalog function to get table names
            # table_type='TABLE' filters for user tables (excludes system tables and views)
            tables = []
            try:
                for row in cursor.tables(tableType='TABLE'):
                    table_name = row.table_name
                    # Skip system tables (MSys*)
                    if not table_name.startswith('MSys'):
                        tables.append({
                            "name": table_name,
                            "schema": row.table_schem or "dbo",  # Use schema if available
                            "row_count": None,
                        })
            finally:
                cursor.close()
            
            return tables
    
    def list_views(self) -> list[dict[str, Any]]:
        """
        List all views (queries) with their definitions.
        
        Falls back to ODBC catalog functions if MSysObjects is not accessible.
        """
        # First, try MSysObjects (most reliable when accessible)
        sql = """
            SELECT 
                MSysObjects.Name AS view_name,
                'dbo' AS view_schema,
                MSysQueries.Expression AS view_definition
            FROM MSysObjects
            INNER JOIN MSysQueries ON MSysObjects.Id = MSysQueries.ObjectId
            WHERE MSysObjects.Type = 5
            AND Left(MSysObjects.Name, 1) <> '~'
            ORDER BY MSysObjects.Name
        """
        try:
            with self._connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute(sql)
                    rows = cursor.fetchall()
                    
                    views = []
                    for row in rows:
                        views.append({
                            "name": row[0],
                            "schema": "dbo",
                            "definition": row[2] if len(row) > 2 else None,
                        })
                    return views
                finally:
                    cursor.close()
        except pyodbc.ProgrammingError as e:
            # Check if it's a permission error on MSysObjects
            error_msg = str(e).lower()
            if "msysobjects" in error_msg or "msysqueries" in error_msg or "no read permission" in error_msg or "-1907" in str(e):
                # Try alternative method using ODBC catalog functions
                try:
                    return self._list_views_via_catalog()
                except Exception as catalog_error:
                    # If catalog method also fails, raise a helpful error
                    raise RuntimeError(
                        f"Cannot list views/queries: MSysObjects system table is not accessible "
                        f"(permission denied). This is common with Access databases that have "
                        f"restricted system table access. Consider using the 'access_com' backend "
                        f"instead, which uses COM automation and can access query definitions directly. "
                        f"Original error: {e}. Catalog method also failed: {catalog_error}"
                    ) from e
            # Re-raise if it's a different error
            raise
        except Exception as e:
            # For other errors, try catalog method as fallback
            try:
                return self._list_views_via_catalog()
            except Exception:
                # If catalog method fails, raise original error
                raise RuntimeError(
                    f"Failed to list views/queries: {e}. "
                    f"Consider using the 'access_com' backend for better compatibility."
                ) from e
    
    def _list_views_via_catalog(self) -> list[dict[str, Any]]:
        """
        List views/queries using ODBC catalog functions as fallback.
        
        This method uses pyodbc's tables() method which queries the ODBC catalog.
        Note: This method can list query names but cannot retrieve SQL definitions
        without access to MSysQueries system table.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            # Use ODBC catalog function to get view/query names
            # table_type='VIEW' filters for queries/views in Access
            views = []
            try:
                for row in cursor.tables(tableType='VIEW'):
                    view_name = row.table_name
                    # Skip system views and temporary queries (starting with ~)
                    if not view_name.startswith('MSys') and not view_name.startswith('~'):
                        views.append({
                            "name": view_name,
                            "schema": row.table_schem or "dbo",  # Use schema if available
                            "definition": None,  # Cannot get SQL definition without MSysQueries access
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
