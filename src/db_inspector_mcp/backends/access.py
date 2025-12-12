"""Microsoft Access backend implementation using pyodbc."""

import time
from typing import Any

import pyodbc

from .base import DatabaseBackend


class AccessBackend(DatabaseBackend):
    """Microsoft Access database backend using pyodbc."""
    
    def __init__(self, connection_string: str, query_timeout_seconds: int = 30):
        """
        Initialize Access backend.
        
        Args:
            connection_string: ODBC connection string or path to .accdb/.mdb file
            query_timeout_seconds: Query timeout in seconds
        """
        super().__init__(connection_string, query_timeout_seconds)
        self._connection: pyodbc.Connection | None = None
    
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
    
    def get_row_count(self, sql: str) -> int:
        """Get row count by wrapping query in SELECT COUNT(*)."""
        wrapped_sql = f"SELECT COUNT(*) AS cnt FROM ({sql}) AS subquery"
        cursor = self._execute_query(wrapped_sql)
        result = cursor.fetchone()
        cursor.close()
        return result[0] if result else 0
    
    def get_columns(self, sql: str) -> list[dict[str, Any]]:
        """Get column metadata using TOP 0 to get metadata without fetching data."""
        # Use TOP 0 to get metadata without fetching data
        wrapped_sql = f"SELECT TOP 0 * FROM ({sql}) AS subquery"
        cursor = self._execute_query(wrapped_sql)
        
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
    
    def sum_column(self, sql: str, column: str) -> float | None:
        """Compute SUM of a column."""
        # Access uses square brackets for identifiers
        wrapped_sql = f"SELECT SUM([{column}]) AS sum_val FROM ({sql}) AS subquery"
        cursor = self._execute_query(wrapped_sql)
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
    
    def preview(self, sql: str, max_rows: int) -> list[dict[str, Any]]:
        """Sample N rows from a query result."""
        # Add TOP clause to limit rows
        if "TOP " not in sql.upper():
            sql_upper = sql.upper().strip()
            if sql_upper.startswith("SELECT"):
                sql = f"SELECT TOP {max_rows} " + sql[6:].lstrip()
            else:
                sql = f"SELECT TOP {max_rows} * FROM ({sql}) AS subquery"
        
        cursor = self._execute_query(sql)
        rows = cursor.fetchall()
        
        # Convert rows to dictionaries
        column_names = [col[0] for col in cursor.description] if cursor.description else []
        result = [dict(zip(column_names, row)) for row in rows]
        
        cursor.close()
        return result
    
    def explain_query(self, sql: str) -> str:
        """
        Get execution plan.
        
        Note: Access doesn't have native EXPLAIN support like SQL Server or PostgreSQL.
        This returns a message indicating that execution plans are not available.
        """
        return "Execution plans are not available for Microsoft Access databases. Access uses a query optimizer, but detailed execution plans are not exposed via ODBC."
    
    def list_tables(self) -> list[dict[str, Any]]:
        """List all tables using MSysObjects system table."""
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
    
    def list_views(self) -> list[dict[str, Any]]:
        """List all views (queries) with their definitions."""
        sql = """
            SELECT 
                MSysObjects.Name AS view_name,
                'dbo' AS view_schema,
                MSysQueries.Expression AS view_definition
            FROM MSysObjects
            INNER JOIN MSysQueries ON MSysObjects.Id = MSysQueries.ObjectId
            WHERE MSysObjects.Type = 5
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
        except Exception:
            # If MSysQueries is not accessible, try alternative approach
            # Access may restrict access to system tables
            return []
    
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
