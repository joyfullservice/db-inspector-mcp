"""Microsoft Access backend implementation using COM automation."""

import re
from typing import Any

try:
    import win32com.client
    COM_AVAILABLE = True
except ImportError:
    COM_AVAILABLE = False

from .access_odbc import AccessODBCBackend
from .base import DatabaseBackend


class AccessCOMBackend(DatabaseBackend):
    """Microsoft Access database backend using COM automation for introspection."""
    
    def __init__(self, connection_string: str, query_timeout_seconds: int = 30):
        """
        Initialize Access COM backend.
        
        Args:
            connection_string: ODBC connection string or path to .accdb/.mdb file
            query_timeout_seconds: Query timeout in seconds
        """
        super().__init__(connection_string, query_timeout_seconds)
        if not COM_AVAILABLE:
            raise ImportError(
                "pywin32 is required for COM backend. "
                "Install it with: pip install pywin32"
            )
        self._app = None
        self._db_path = self._extract_db_path(connection_string)
        # Use ODBC backend internally for query execution
        self._odbc_backend = AccessODBCBackend(connection_string, query_timeout_seconds)
    
    def _extract_db_path(self, connection_string: str) -> str:
        """Extract database path from connection string."""
        # Look for DBQ= in connection string
        match = re.search(r'DBQ=([^;]+)', connection_string, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # If no DBQ found, assume connection_string is the path
        return connection_string
    
    def _get_access_app(self):
        """Get or create Access COM application."""
        if self._app is None:
            self._app = win32com.client.Dispatch("Access.Application")
            self._app.OpenCurrentDatabase(self._db_path)
        return self._app
    
    def _get_current_db(self):
        """Get CurrentDb object."""
        return self._get_access_app().CurrentDb()
    
    def _get_query_type(self, query_def) -> str:
        """Get query type from QueryDef."""
        try:
            # QueryDef.Type: 0=Select, 1=Union, 2=PassThrough, etc.
            type_map = {
                0: "Select",
                1: "Union",
                2: "PassThrough",
                3: "DataDefinition",
                4: "Append",
                5: "Delete",
                6: "Update",
                7: "MakeTable",
            }
            return type_map.get(query_def.Type, "Unknown")
        except Exception:
            return "Unknown"
    
    def get_query_by_name(self, query_name: str) -> dict[str, Any]:
        """
        Get native SQL from Access query by name.
        
        Args:
            query_name: Name of the Access query
            
        Returns:
            Dictionary with query name, SQL, and type
        """
        db = self._get_current_db()
        query_def = db.QueryDefs(query_name)
        return {
            "name": query_name,
            "sql": query_def.SQL,
            "type": self._get_query_type(query_def),
        }
    
    # Delegate all standard DatabaseBackend methods to ODBC backend
    def get_row_count(self, sql: str) -> int:
        """Get row count by wrapping query in SELECT COUNT(*)."""
        return self._odbc_backend.get_row_count(sql)
    
    def get_columns(self, sql: str) -> list[dict[str, Any]]:
        """Get column metadata using TOP 0 to get metadata without fetching data."""
        return self._odbc_backend.get_columns(sql)
    
    def sum_column(self, sql: str, column: str) -> float | None:
        """Compute SUM of a column."""
        return self._odbc_backend.sum_column(sql, column)
    
    def measure_query(self, sql: str, max_rows: int) -> dict[str, Any]:
        """Measure query execution time and retrieve limited rows."""
        return self._odbc_backend.measure_query(sql, max_rows)
    
    def preview(self, sql: str, max_rows: int) -> list[dict[str, Any]]:
        """Sample N rows from a query result."""
        return self._odbc_backend.preview(sql, max_rows)
    
    def explain_query(self, sql: str) -> str:
        """Get execution plan."""
        return self._odbc_backend.explain_query(sql)
    
    def list_tables(self) -> list[dict[str, Any]]:
        """List all tables using COM TableDefs."""
        db = self._get_current_db()
        tables = []
        for table_def in db.TableDefs:
            # Skip system tables
            if not table_def.Name.startswith("MSys"):
                tables.append({
                    "name": table_def.Name,
                    "schema": "dbo",  # Access doesn't have schemas
                    "row_count": None,
                })
        return tables
    
    def list_views(self) -> list[dict[str, Any]]:
        """
        List all queries without SQL (SQL extraction is costly).
        
        Use get_query_by_name() to get SQL for specific queries when needed.
        """
        db = self._get_current_db()
        views = []
        for query_def in db.QueryDefs:
            views.append({
                "name": query_def.Name,
                "schema": "dbo",
                "definition": None,  # SQL not extracted - use get_query_by_name() when needed
            })
        return views
    
    def verify_readonly(self) -> dict[str, Any]:
        """Verify user has no write permissions."""
        return self._odbc_backend.verify_readonly()
