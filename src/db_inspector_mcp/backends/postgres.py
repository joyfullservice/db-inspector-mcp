"""PostgreSQL backend implementation using psycopg2."""

import json
import time
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from .base import DatabaseBackend


class PostgresBackend(DatabaseBackend):
    """PostgreSQL database backend using psycopg2."""
    
    def __init__(self, connection_string: str, query_timeout_seconds: int = 30):
        """
        Initialize PostgreSQL backend.
        
        Args:
            connection_string: PostgreSQL connection string (dbname=... user=... etc.)
            query_timeout_seconds: Query timeout in seconds
        """
        super().__init__(connection_string, query_timeout_seconds)
        self._connection: psycopg2.extensions.connection | None = None
    
    def _get_connection(self) -> psycopg2.extensions.connection:
        """Get or create a database connection."""
        if self._connection is None:
            self._connection = psycopg2.connect(
                self.connection_string,
                connect_timeout=self.query_timeout_seconds
            )
            # Set statement timeout
            with self._connection.cursor() as cursor:
                cursor.execute(f"SET statement_timeout = {self.query_timeout_seconds * 1000}")
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
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(sql)
        if fetch:
            return cursor
        return None
    
    def get_row_count(self, sql: str) -> int:
        """Get row count by wrapping query in SELECT COUNT(*)."""
        wrapped_sql = f"SELECT COUNT(*) as cnt FROM ({sql}) AS subquery"
        cursor = self._execute_query(wrapped_sql)
        result = cursor.fetchone()
        count = result["cnt"] if result else 0
        cursor.close()
        return count
    
    def get_columns(self, sql: str) -> list[dict[str, Any]]:
        """Get column metadata using LIMIT 0."""
        # Use LIMIT 0 to get metadata without fetching data
        wrapped_sql = f"SELECT * FROM ({sql}) AS subquery LIMIT 0"
        cursor = self._execute_query(wrapped_sql)
        
        columns = []
        for col in cursor.description:
            if col:
                # PostgreSQL cursor description format:
                # (name, type_code, display_size, internal_size, precision, scale, null_ok)
                columns.append({
                    "name": col.name,
                    "type": str(col.type_code),
                    "nullable": col.null_ok if hasattr(col, 'null_ok') else None,
                    "precision": col.precision if hasattr(col, 'precision') and col.precision else None,
                    "scale": col.scale if hasattr(col, 'scale') and col.scale else None,
                })
        
        cursor.close()
        return columns
    
    def sum_column(self, sql: str, column: str) -> float | None:
        """Compute SUM of a column."""
        # Use double quotes for column name to handle case sensitivity
        wrapped_sql = f'SELECT SUM("{column}") as sum_val FROM ({sql}) AS subquery'
        cursor = self._execute_query(wrapped_sql)
        result = cursor.fetchone()
        sum_val = result["sum_val"] if result else None
        cursor.close()
        return sum_val
    
    def measure_query(self, sql: str, max_rows: int) -> dict[str, Any]:
        """Measure query execution time and retrieve limited rows."""
        # Add LIMIT clause
        if "LIMIT" not in sql.upper():
            sql = f"{sql} LIMIT {max_rows}"
        
        start_time = time.time()
        cursor = self._execute_query(sql)
        rows = cursor.fetchall()
        execution_time_ms = (time.time() - start_time) * 1000
        
        # Convert rows to dictionaries (RealDictCursor already does this)
        result_rows = [dict(row) for row in rows]
        
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
        # Add LIMIT clause
        if "LIMIT" not in sql.upper():
            sql = f"{sql} LIMIT {max_rows}"
        
        cursor = self._execute_query(sql)
        rows = cursor.fetchall()
        
        # Convert rows to dictionaries (RealDictCursor already does this)
        result = [dict(row) for row in rows]
        
        cursor.close()
        return result
    
    def explain_query(self, sql: str) -> str:
        """Get execution plan using EXPLAIN (FORMAT JSON)."""
        explain_sql = f"EXPLAIN (FORMAT JSON) {sql}"
        cursor = self._execute_query(explain_sql)
        result = cursor.fetchone()
        cursor.close()
        
        if result:
            # EXPLAIN (FORMAT JSON) returns a list with one element containing the plan
            # The result is a dict with "QUERY PLAN" key containing a list
            if isinstance(result, dict) and "QUERY PLAN" in result:
                plan_json = result["QUERY PLAN"]
                # Format JSON for readability
                try:
                    return json.dumps(plan_json, indent=2)
                except (TypeError, ValueError):
                    return str(plan_json)
            else:
                # Fallback: try to get the plan directly
                return json.dumps(result, indent=2) if result else "No execution plan available"
        else:
            return "No execution plan available"
    
    def list_tables(self) -> list[dict[str, Any]]:
        """List all tables using information_schema."""
        sql = """
            SELECT 
                table_schema,
                table_name,
                (SELECT reltuples::bigint 
                 FROM pg_class 
                 WHERE relname = table_name 
                 AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = table_schema)
                ) AS approximate_row_count
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
            AND table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
        """
        cursor = self._execute_query(sql)
        rows = cursor.fetchall()
        
        tables = []
        for row in rows:
            tables.append({
                "name": row["table_name"],
                "schema": row["table_schema"],
                "row_count": int(row["approximate_row_count"]) if row["approximate_row_count"] else None,
            })
        
        cursor.close()
        return tables
    
    def list_views(self) -> list[dict[str, Any]]:
        """List all views with their definitions."""
        sql = """
            SELECT 
                table_schema,
                table_name,
                view_definition
            FROM information_schema.views
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
        """
        cursor = self._execute_query(sql)
        rows = cursor.fetchall()
        
        views = []
        for row in rows:
            views.append({
                "name": row["table_name"],
                "schema": row["table_schema"],
                "definition": row["view_definition"],
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
                cursor.execute("CREATE TEMP TABLE test_readonly (id INT)")
                cursor.execute("DROP TABLE test_readonly")
                details.append("✓ Can create temp tables (expected for read-only)")
            except Exception as e:
                details.append(f"✗ Cannot create temp tables: {str(e)}")
                readonly = False
            
            # Check privileges on tables
            try:
                check_sql = """
                    SELECT 
                        table_schema,
                        table_name,
                        privilege_type
                    FROM information_schema.role_table_grants
                    WHERE grantee = CURRENT_USER
                    AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'CREATE')
                    LIMIT 10
                """
                cursor.execute(check_sql)
                privileges = cursor.fetchall()
                
                if privileges:
                    priv_list = [f"{p[0]}.{p[1]}: {p[2]}" for p in privileges]
                    details.append(f"⚠ User has write privileges on: {', '.join(priv_list[:5])}")
                    if len(privileges) > 5:
                        details.append(f"  ... and {len(privileges) - 5} more")
                    readonly = False
                else:
                    details.append("✓ User has no write privileges on tables")
            except Exception as e:
                details.append(f"⚠ Could not check privileges: {str(e)}")
            
            # Check if user is superuser
            try:
                cursor.execute("SELECT current_setting('is_superuser')")
                is_superuser = cursor.fetchone()[0]
                if is_superuser == 'on':
                    details.append("⚠ User is a superuser (has all privileges)")
                    readonly = False
                else:
                    details.append("✓ User is not a superuser")
            except Exception as e:
                details.append(f"⚠ Could not check superuser status: {str(e)}")
            
            cursor.close()
            
        except Exception as e:
            details.append(f"Error during verification: {str(e)}")
            readonly = False
        
        return {
            "readonly": readonly,
            "details": "\n".join(details),
        }

