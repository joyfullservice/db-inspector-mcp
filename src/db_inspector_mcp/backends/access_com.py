"""Microsoft Access backend implementation using COM automation."""

import re
from contextlib import contextmanager
from typing import Any

try:
    import win32com.client
    from win32com.client import gencache
    COM_AVAILABLE = True
except ImportError:
    COM_AVAILABLE = False
    gencache = None  # type: ignore

from .access_odbc import AccessODBCBackend
from .base import DatabaseBackend


def _set_access_visible(app) -> None:
    """Make the Access Application window visible.

    The obvious ``app.Visible = True`` fails because both EnsureDispatch
    (early-bound) and Dispatch (late-bound, with gen_py cache) resolve the
    ``Visible`` property to a DAO DISPID instead of the Application one.
    This is a type-library collision in the Access COM object itself.

    Workaround: get the Access window handle via ``hWndAccessApp`` and call
    the Win32 ``ShowWindow`` API directly — no COM property dispatch needed.
    """
    import ctypes
    SW_SHOW = 5
    try:
        hwnd = app.hWndAccessApp()
        ctypes.windll.user32.ShowWindow(hwnd, SW_SHOW)
    except Exception:
        pass  # Best effort — Access may already be visible


class AccessCOMBackend(DatabaseBackend):
    """Microsoft Access database backend using COM automation for introspection.
    
    Connection lifecycle strategy:
    - The Access Application is cached after first use and left running. The user
      is responsible for closing Access when they are done -- we never quit it.
      This is intentional: users of this tool are typically already working in
      Access, so GetObject is fast. Even if we start Access, leaving it running
      means subsequent calls are quick.
    - The DAO Database is opened per-request and closed when done, avoiding a
      persistent lock (.laccdb file) on the database between tool calls.
    - SQL query execution is delegated to an internal ODBC backend, which also
      uses connect-per-request.
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
        Initialize Access COM backend.
        
        Args:
            connection_string: ODBC connection string or path to .accdb/.accda/.mdb file
            query_timeout_seconds: Query timeout in seconds
            connection_ttl_seconds: TTL for the internal ODBC connection cache (passed
                through to the AccessODBCBackend used for SQL execution).
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
        self._odbc_backend = AccessODBCBackend(
            connection_string, query_timeout_seconds, connection_ttl_seconds
        )
    
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
        - If user has OUR database open -> connect to their instance
        - If user has DIFFERENT database open -> create our OWN instance
        - If no Access running -> create new instance
        
        The Application is cached and left running. The user is responsible for
        closing Access when they are done with their session.
        
        Early Binding Strategy:
        When creating a new Access instance, we use gencache.EnsureDispatch() instead
        of plain Dispatch(). This generates and caches type library bindings, which:
        - Makes COM automation more reliable
        - Enables Application.Run to work correctly (late binding often fails)
        - Benefits subsequent GetObject calls (bindings are cached system-wide)
        """
        if self._app is None:
            try:
                # First, try to get the specific database file directly
                # This will work if the database is already open in any Access instance
                # GetObject with a file path returns the Application (with database open)
                self._app = win32com.client.GetObject(self._db_path)
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
                            self._app = gencache.EnsureDispatch("Access.Application")
                        else:
                            # Access is running but no database open
                            # Create our own to avoid confusion
                            self._app = gencache.EnsureDispatch("Access.Application")
                    except Exception:
                        # Can't check CurrentDb - Access might be in weird state
                        # Create our own instance to be safe
                        self._app = gencache.EnsureDispatch("Access.Application")
                except Exception:
                    # No running Access instance, create new one
                    self._app = gencache.EnsureDispatch("Access.Application")
            # Always ensure Access is visible so users can see (and close) it
            _set_access_visible(self._app)
        return self._app
    
    @contextmanager
    def _dao_database(self):
        """
        Context manager that opens a DAO Database and closes it when done.
        
        If Access already has our database open (via GetObject), uses CurrentDb()
        which does NOT create an additional lock. Otherwise, opens via
        DBEngine.OpenDatabase() and closes it afterwards.
        
        This ensures the database lock is released after each operation when
        we opened the database ourselves.
        
        Yields:
            DAO Database object
        """
        app = self._get_access_app()
        db = None
        needs_close = False
        
        # If Access already has the database open, try CurrentDb() first
        try:
            current_db = app.CurrentDb()
            if current_db is not None:
                db = current_db
                needs_close = False  # User's database - don't close it
        except Exception:
            pass
        
        # If CurrentDb() didn't work, open via DBEngine
        if db is None:
            try:
                dbe = app.DBEngine
                # Open database in shared mode (Exclusive=False, ReadOnly=False)
                db = dbe.OpenDatabase(self._db_path, False, False)
                needs_close = True
            except Exception:
                # Try read-only mode
                try:
                    dbe = app.DBEngine
                    db = dbe.OpenDatabase(self._db_path, False, True)
                    needs_close = True
                except Exception:
                    # Last resort: try direct DAO without Access
                    try:
                        dbe = win32com.client.Dispatch("DAO.DBEngine.120")
                        db = dbe.OpenDatabase(self._db_path, False, True)
                        needs_close = True
                    except Exception as e:
                        raise RuntimeError(
                            f"Failed to open database '{self._db_path}' via COM. "
                            f"Ensure the database file exists and is not corrupted. Error: {e}"
                        )
        
        try:
            yield db
        finally:
            if needs_close and db is not None:
                try:
                    db.Close()
                except Exception:
                    pass
    
    def call_vba_function(self, function_name: str, *args) -> Any:
        """
        Call a VBA function in the Access database via Application.Run.
        
        This method handles the quirk where early-bound Application.Run returns
        a tuple instead of just the result. The actual return value is always
        the first element of the tuple.
        
        Args:
            function_name: Name of the VBA function to call (can include module prefix)
            *args: Arguments to pass to the VBA function
            
        Returns:
            The return value from the VBA function
            
        Raises:
            RuntimeError: If the function call fails
            
        Example:
            # Call a simple function
            result = backend.call_vba_function("MyModule.GetVersion")
            
            # Call with arguments
            result = backend.call_vba_function("MyModule.Calculate", 10, 20)
            
            # Call an add-in function (use the API path pattern)
            result = backend.call_vba_function("MyAddin.API", "FunctionName", arg1)
        """
        app = self._get_access_app()
        try:
            if args:
                result = app.Run(function_name, *args)
            else:
                result = app.Run(function_name)
            
            # With early binding (gencache.EnsureDispatch), Application.Run returns
            # a tuple where the first element is the actual result
            if isinstance(result, tuple) and len(result) > 0:
                return result[0]
            return result
        except Exception as e:
            # COM errors often have cryptic codes - provide more context
            raise RuntimeError(
                f"Failed to call VBA function '{function_name}': {e}"
            ) from e
    
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
            
        Raises:
            ValueError: If query doesn't exist
            RuntimeError: If there's an error accessing the query
        """
        with self._dao_database() as db:
            try:
                query_def = db.QueryDefs(query_name)
            except Exception as e:
                # Query might not exist - provide helpful error
                error_msg = str(e).lower()
                if "item not found" in error_msg or "not found" in error_msg or "3265" in str(e):
                    raise ValueError(
                        f"Query '{query_name}' not found in database. "
                        f"Use db_list_views() to see available queries."
                    ) from e
                raise RuntimeError(
                    f"Failed to access query '{query_name}': {e}"
                ) from e
            
            try:
                sql = query_def.SQL
                query_type = self._get_query_type(query_def)
            except Exception as e:
                raise RuntimeError(
                    f"Failed to retrieve SQL definition for query '{query_name}': {e}"
                ) from e
            
            return {
                "name": query_name,
                "sql": sql,
                "type": query_type,
            }
    
    # Delegate all standard DatabaseBackend methods to ODBC backend
    def count_query_results(self, query: str) -> int:
        """Count row count by wrapping query in SELECT COUNT(*)."""
        return self._odbc_backend.count_query_results(query)
    
    def get_query_columns(self, query: str) -> list[dict[str, Any]]:
        """Get column metadata using TOP 0 to get metadata without fetching data."""
        return self._odbc_backend.get_query_columns(query)
    
    def sum_query_column(self, query: str, column: str) -> float | None:
        """Compute SUM of a column from query results."""
        return self._odbc_backend.sum_query_column(query, column)
    
    def measure_query(self, query: str, max_rows: int) -> dict[str, Any]:
        """Measure query execution time and retrieve limited rows."""
        return self._odbc_backend.measure_query(query, max_rows)
    
    def preview(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        """Sample N rows from a query result."""
        return self._odbc_backend.preview(query, max_rows)
    
    def explain_query(self, query: str) -> str:
        """Get execution plan."""
        return self._odbc_backend.explain_query(query)
    
    def list_tables(self) -> list[dict[str, Any]]:
        """List all tables using COM TableDefs."""
        with self._dao_database() as db:
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
        with self._dao_database() as db:
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
