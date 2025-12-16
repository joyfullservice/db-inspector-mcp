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
            connection_string: ODBC connection string or path to .accdb/.accda/.mdb file
            query_timeout_seconds: Query timeout in seconds
        """
        super().__init__(connection_string, query_timeout_seconds)
        if not COM_AVAILABLE:
            raise ImportError(
                "pywin32 is required for COM backend. "
                "Install it with: pip install pywin32"
            )
        self._app = None
        self._db = None  # Cached database object
        self._db_path = self._extract_db_path(connection_string)
        # Track whether we connected to an existing Access instance or created our own
        self._owns_app = False  # True only if we created Access via Dispatch
        self._owns_db = False   # True only if we opened db via DBEngine (not CurrentDb)
        self._db_opened_via_getobject = False
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
        """
        Get or create Access COM application.
        
        Uses a multi-step approach:
        1. Try GetObject(path) to connect to our database if already open
        2. If Access has a DIFFERENT database open, create NEW instance (don't interfere)
        3. If no Access running, create new instance
        
        IMPORTANT: Access can only have ONE database open at a time.
        - If user has OUR database open → connect to their instance
        - If user has DIFFERENT database open → create our OWN instance
        - If no Access running → create our OWN instance
        
        We keep our Access instance alive for subsequent MCP calls.
        
        Note: We deliberately don't use OpenCurrentDatabase() when creating a new
        Access instance because CurrentDb() often fails in that scenario. Instead,
        we use DBEngine.OpenDatabase() directly in _get_current_db().
        """
        if self._app is None:
            try:
                # First, try to get the specific database file directly
                # This will work if the database is already open in any Access instance
                # GetObject with a file path returns the Application (with database open)
                self._app = win32com.client.GetObject(self._db_path)
                self._db_opened_via_getobject = True
                self._owns_app = False  # User's Access instance - do NOT close it
            except Exception:
                # Our database is not open. Check if Access is running with another db.
                try:
                    existing_app = win32com.client.GetObject(None, "Access.Application")
                    # Access is running - check if it has a database open
                    try:
                        current_db = existing_app.CurrentDb()
                        if current_db is not None:
                            # Access has a DIFFERENT database open - do NOT use it!
                            # Create our own Access instance to avoid interfering
                            self._app = win32com.client.Dispatch("Access.Application")
                            self._db_opened_via_getobject = False
                            self._owns_app = True  # We created this
                        else:
                            # Access is running but no database open - we can use it
                            # But actually, better to create our own to avoid confusion
                            self._app = win32com.client.Dispatch("Access.Application")
                            self._db_opened_via_getobject = False
                            self._owns_app = True
                    except Exception:
                        # Can't check CurrentDb - Access might be in weird state
                        # Create our own instance to be safe
                        self._app = win32com.client.Dispatch("Access.Application")
                        self._db_opened_via_getobject = False
                        self._owns_app = True
                except Exception:
                    # No running Access instance, create new one
                    self._app = win32com.client.Dispatch("Access.Application")
                    self._db_opened_via_getobject = False
                    self._owns_app = True  # We created this - we're responsible for cleanup
        return self._app
    
    def _get_current_db(self):
        """
        Get database object for DAO operations.
        
        Strategy:
        1. If database was opened via GetObject (Access already had it open), use CurrentDb()
        2. Otherwise, use DBEngine.OpenDatabase() which is more reliable
        
        IMPORTANT: This method tracks whether we OWN the database connection:
        - If we use CurrentDb() (user's database), we do NOT own it
        - If we open via DBEngine, we OWN it and are responsible for closing
        
        CurrentDb() can fail in certain COM automation scenarios, particularly
        when opening a database that was created by a different Access instance.
        DBEngine.OpenDatabase() works reliably in all cases.
        
        Returns:
            DAO Database object
        """
        if self._db is None:
            app = self._get_access_app()
            
            # If Access already had the database open, CurrentDb() should work
            if self._db_opened_via_getobject:
                try:
                    db = app.CurrentDb()
                    if db is not None:
                        self._db = db
                        self._owns_db = False  # User's database - do NOT close it
                        return self._db
                except Exception:
                    pass
            
            # Use DBEngine.OpenDatabase() - more reliable
            try:
                dbe = app.DBEngine
                # Open database in shared mode (Exclusive=False, ReadOnly=False)
                # We use False for ReadOnly to allow full access
                self._db = dbe.OpenDatabase(self._db_path, False, False)
                self._owns_db = True  # We opened this - we're responsible for closing
            except Exception:
                # Try read-only mode
                try:
                    dbe = app.DBEngine
                    self._db = dbe.OpenDatabase(self._db_path, False, True)
                    self._owns_db = True
                except Exception:
                    # Last resort: try direct DAO without Access
                    try:
                        dbe = win32com.client.Dispatch("DAO.DBEngine.120")
                        self._db = dbe.OpenDatabase(self._db_path, False, True)
                        self._owns_db = True
                    except Exception as e:
                        raise RuntimeError(
                            f"Failed to open database '{self._db_path}' via COM. "
                            f"Ensure the database file exists and is not corrupted. Error: {e}"
                        )
        
        return self._db
    
    def close(self):
        """
        Close the database connection and cleanup COM resources.
        
        IMPORTANT: This method respects ownership:
        - If we connected to an existing Access instance (via GetObject), we do NOT
          close Access or the database - the user is still using them!
        - If we created our own Access instance, we clean it up properly.
        
        Call this when done using the backend to properly release resources.
        """
        # Only close the database if we opened it ourselves
        if self._db is not None and self._owns_db:
            try:
                self._db.Close()
            except Exception:
                pass
        self._db = None
        
        # Only quit Access if we created it ourselves
        if self._app is not None and self._owns_app:
            try:
                self._app.CloseCurrentDatabase()
            except Exception:
                pass
            try:
                self._app.Quit()
            except Exception:
                pass
        self._app = None
        
        # Reset ownership flags
        self._owns_app = False
        self._owns_db = False
        self._db_opened_via_getobject = False
    
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
