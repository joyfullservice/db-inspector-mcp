"""Microsoft Access backend implementation using pyodbc."""

import re
import time
from typing import Any

import pyodbc

from .base import DatabaseBackend


class AccessODBCBackend(DatabaseBackend):
    """Microsoft Access database backend using pyodbc."""
    
    def __init__(self, connection_string: str, query_timeout_seconds: int = 30):
        """
        Initialize Access ODBC backend.
        
        Args:
            connection_string: ODBC connection string or path to .accdb/.accda/.mdb file
            query_timeout_seconds: Query timeout in seconds
        """
        # Ensure connection string includes DBQ parameter for ODBC connections
        connection_string = self._ensure_dbq_parameter(connection_string)
        super().__init__(connection_string, query_timeout_seconds)
        self._connection: pyodbc.Connection | None = None
    
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
    
    def _get_connection(self) -> pyodbc.Connection:
        """Get or create a database connection."""
        if self._connection is None:
            self._connection = pyodbc.connect(
                self.connection_string,
                timeout=self.query_timeout_seconds
            )
        return self._connection
    
    def _execute_query(self, sql: str, fetch: bool = True) -> Any:
        """
        Execute a SQL query and optionally fetch results.
        
        Args:
            sql: SQL query to execute
            fetch: Whether to fetch results
            
        Returns:
            Cursor with results if fetch=True, otherwise None
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        if fetch:
            return cursor
        return None
    
    def count_query_results(self, query: str) -> int:
        """Count row count by wrapping query in SELECT COUNT(*)."""
        wrapped_query = f"SELECT COUNT(*) AS cnt FROM ({query}) AS subquery"
        cursor = self._execute_query(wrapped_query)
        result = cursor.fetchone()
        cursor.close()
        return result[0] if result else 0
    
    def get_query_columns(self, query: str) -> list[dict[str, Any]]:
        """Get column metadata using TOP 0 to get metadata without fetching data."""
        # Use TOP 0 to get metadata without fetching data
        wrapped_query = f"SELECT TOP 0 * FROM ({query}) AS subquery"
        cursor = self._execute_query(wrapped_query)
        
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
        
        cursor.close()
        return columns
    
    def sum_query_column(self, query: str, column: str) -> float | None:
        """Compute SUM of a column from query results."""
        # Access uses square brackets for identifiers
        wrapped_query = f"SELECT SUM([{column}]) AS sum_val FROM ({query}) AS subquery"
        cursor = self._execute_query(wrapped_query)
        result = cursor.fetchone()
        cursor.close()
        return result[0] if result and result[0] is not None else None
    
    def measure_query(self, sql: str, max_rows: int) -> dict[str, Any]:
        """Measure query execution time and retrieve limited rows."""
        # Add TOP clause to limit rows (Access uses TOP like SQL Server)
        if "TOP " not in sql.upper():
            sql_upper = sql.upper().strip()
            if sql_upper.startswith("SELECT"):
                sql = f"SELECT TOP {max_rows} " + sql[6:].lstrip()
            else:
                sql = f"SELECT TOP {max_rows} * FROM ({sql}) AS subquery"
        
        start_time = time.time()
        cursor = self._execute_query(sql)
        rows = cursor.fetchall()
        execution_time_ms = (time.time() - start_time) * 1000
        
        # Convert rows to dictionaries
        column_names = [col[0] for col in cursor.description] if cursor.description else []
        result_rows = [dict(zip(column_names, row)) for row in rows]
        
        row_count = len(result_rows)
        hit_limit = row_count >= max_rows
        
        cursor.close()
        
        return {
            "execution_time_ms": round(execution_time_ms, 2),
            "row_count": row_count,
            "hit_limit": hit_limit,
        }
    
    def preview(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        """Sample N rows from a query result."""
        # Add TOP clause to limit rows
        if "TOP " not in query.upper():
            query_upper = query.upper().strip()
            if query_upper.startswith("SELECT"):
                query = f"SELECT TOP {max_rows} " + query[6:].lstrip()
            else:
                query = f"SELECT TOP {max_rows} * FROM ({query}) AS subquery"
        
        cursor = self._execute_query(query)
        rows = cursor.fetchall()
        
        # Convert rows to dictionaries
        column_names = [col[0] for col in cursor.description] if cursor.description else []
        result = [dict(zip(column_names, row)) for row in rows]
        
        cursor.close()
        return result
    
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
            cursor = self._execute_query(sql)
            rows = cursor.fetchall()
            
            tables = []
            for row in rows:
                tables.append({
                    "name": row[0],
                    "schema": "dbo",  # Access doesn't have schemas, use default
                    "row_count": None,  # Could add count if needed
                })
            
            cursor.close()
            return tables
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
        conn = self._get_connection()
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
            cursor = self._execute_query(sql)
            rows = cursor.fetchall()
            
            views = []
            for row in rows:
                views.append({
                    "name": row[0],
                    "schema": "dbo",
                    "definition": row[2] if len(row) > 2 else None,
                })
            
            cursor.close()
            return views
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
        conn = self._get_connection()
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
        conn = self._get_connection()
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
            
            cursor.close()
            
        except Exception as e:
            details.append(f"Error during verification: {str(e)}")
            readonly = False
        
        return {
            "readonly": readonly,
            "details": "\n".join(details),
        }
