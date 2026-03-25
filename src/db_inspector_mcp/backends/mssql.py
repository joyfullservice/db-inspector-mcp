"""SQL Server backend implementation using pyodbc."""

import time
import xml.etree.ElementTree as ET
from typing import Any

import pyodbc

from .base import DatabaseBackend
from .sql_utils import inject_top_clause, split_cte_prefix


class MSSQLBackend(DatabaseBackend):
    """SQL Server database backend using pyodbc."""
    
    @property
    def sql_dialect(self) -> str:
        """Return 'mssql' as the SQL dialect."""
        return "mssql"

    @property
    def is_connected(self) -> bool:
        return self._connection is not None
    
    def __init__(self, connection_string: str, query_timeout_seconds: int = 30):
        """
        Initialize SQL Server backend.
        
        Args:
            connection_string: ODBC connection string or DSN
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

    def close(self) -> None:
        """Close the cached connection, if any."""
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None
    
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
        cte, core = split_cte_prefix(query)
        wrapped_query = f"{cte}SELECT COUNT(*) as cnt FROM ({core}) AS subquery"
        cursor = self._execute_query(wrapped_query)
        result = cursor.fetchone()
        cursor.close()
        return result[0] if result else 0
    
    def get_query_columns(self, query: str) -> list[dict[str, Any]]:
        """Get column metadata using cursor description."""
        cte, core = split_cte_prefix(query)
        wrapped_query = f"{cte}SELECT TOP 0 * FROM ({core}) AS subquery"
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
        cte, core = split_cte_prefix(query)
        safe_column = column.replace("]", "]]")
        wrapped_query = f"{cte}SELECT SUM([{safe_column}]) as sum_val FROM ({core}) AS subquery"
        cursor = self._execute_query(wrapped_query)
        result = cursor.fetchone()
        cursor.close()
        return result[0] if result and result[0] is not None else None
    
    def measure_query(self, query: str, max_rows: int) -> dict[str, Any]:
        """Measure query execution time and retrieve limited rows."""
        query = inject_top_clause(query, max_rows)
        
        start_time = time.time()
        cursor = self._execute_query(query)
        rows = cursor.fetchall()
        execution_time_ms = (time.time() - start_time) * 1000
        
        column_names = [col[0] for col in cursor.description] if cursor.description else []
        result_rows = self._sanitize_rows(column_names, rows)
        
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
        query = inject_top_clause(query, max_rows)
        
        cursor = self._execute_query(query)
        rows = cursor.fetchall()
        
        column_names = [col[0] for col in cursor.description] if cursor.description else []
        result = self._sanitize_rows(column_names, rows)
        
        cursor.close()
        return result
    
    def explain_query(self, query: str) -> str:
        """Get execution plan using SHOWPLAN_XML."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # Enable showplan
            cursor.execute("SET SHOWPLAN_XML ON")
            
            # Execute the query (won't actually run, just generate plan)
            cursor.execute(query)
            
            # Get the plan
            plan_xml = None
            for row in cursor:
                if row[0]:
                    plan_xml = row[0]
                    break
            
            # Disable showplan
            cursor.execute("SET SHOWPLAN_XML OFF")
            cursor.close()
            
            if plan_xml:
                # Format XML for readability
                try:
                    root = ET.fromstring(plan_xml)
                    return ET.tostring(root, encoding="unicode")
                except ET.ParseError:
                    return plan_xml
            else:
                return "No execution plan available"
                
        except Exception as e:
            # Make sure to turn off showplan even on error
            try:
                cursor.execute("SET SHOWPLAN_XML OFF")
            except:
                pass
            cursor.close()
            raise Exception(f"Failed to get execution plan: {str(e)}")
    
    def get_object_counts(self) -> dict[str, int | None]:
        """Return object counts via INFORMATION_SCHEMA and sys catalog.

        Only includes keys we can actually determine.  If the query fails
        entirely, returns an empty dict.
        """
        try:
            sql = """
                SELECT
                    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE') AS tbl,
                    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.VIEWS) AS vw,
                    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_TYPE='PROCEDURE') AS sp,
                    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_TYPE='FUNCTION') AS fn,
                    (SELECT COUNT(*) FROM sys.triggers) AS trg
            """
            cursor = self._execute_query(sql)
            row = cursor.fetchone()
            cursor.close()
            if row:
                return {
                    "tables": row[0],
                    "views": row[1],
                    "stored_procedures": row[2],
                    "functions": row[3],
                    "triggers": row[4],
                }
        except Exception:
            pass
        return {}

    def list_tables(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """List all tables using INFORMATION_SCHEMA."""
        where = "TABLE_TYPE = 'BASE TABLE'"
        if name_filter:
            safe = name_filter.replace("'", "''")
            where += f" AND TABLE_NAME LIKE '%{safe}%'"

        sql = f"""
            SELECT TABLE_SCHEMA, TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE {where}
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
        cursor = self._execute_query(sql)
        rows = cursor.fetchall()

        tables = []
        for row in rows:
            tables.append({
                "name": row[1],
                "schema": row[0],
                "row_count": None,
            })

        cursor.close()
        return tables

    def list_views(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """List all views with their definitions."""
        where = "1=1"
        if name_filter:
            safe = name_filter.replace("'", "''")
            where += f" AND TABLE_NAME LIKE '%{safe}%'"

        sql = f"""
            SELECT TABLE_SCHEMA, TABLE_NAME, VIEW_DEFINITION
            FROM INFORMATION_SCHEMA.VIEWS
            WHERE {where}
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
        cursor = self._execute_query(sql)
        rows = cursor.fetchall()

        views = []
        for row in rows:
            views.append({
                "name": row[1],
                "schema": row[0],
                "definition": row[2] if len(row) > 2 else None,
            })

        cursor.close()
        return views
    
    def verify_readonly(self) -> dict[str, Any]:
        """Verify user has no write permissions."""
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
            
            # Check role membership
            try:
                check_sql = """
                    SELECT 
                        dp.name AS role_name
                    FROM sys.database_role_members drm
                    INNER JOIN sys.database_principals dp ON drm.role_principal_id = dp.principal_id
                    INNER JOIN sys.database_principals mp ON drm.member_principal_id = mp.principal_id
                    WHERE mp.name = USER_NAME()
                    AND dp.name IN ('db_owner', 'db_datawriter', 'db_ddladmin')
                """
                cursor.execute(check_sql)
                roles = cursor.fetchall()
                
                if roles:
                    role_names = [r[0] for r in roles]
                    details.append(f"⚠ User is member of write roles: {', '.join(role_names)}")
                    readonly = False
                else:
                    details.append("✓ User is not a member of write roles")
            except Exception as e:
                details.append(f"⚠ Could not check role membership: {str(e)}")
            
            cursor.close()
            
        except Exception as e:
            details.append(f"Error during verification: {str(e)}")
            readonly = False
        
        return {
            "readonly": readonly,
            "details": "\n".join(details),
        }

