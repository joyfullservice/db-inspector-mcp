"""Microsoft Access backend implementation using COM automation."""

import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from typing import Any

try:
    import pythoncom
    import pywintypes
    import win32com.client
    from win32com.client import gencache
    COM_AVAILABLE = True
except ImportError:
    COM_AVAILABLE = False
    pythoncom = None  # type: ignore
    gencache = None   # type: ignore
    pywintypes = None  # type: ignore

from .access_odbc import AccessODBCBackend
from .base import DatabaseBackend

logger = logging.getLogger(__name__)

# MSysObjects Type codes for Access object classification
_MSYS_TYPE_LOCAL_TABLE = 1
_MSYS_TYPE_ODBC_LINKED = 4
_MSYS_TYPE_QUERY = 5
_MSYS_TYPE_LINKED_TABLE = 6
_MSYS_TYPE_SUBDATASHEET = 8
_MSYS_TYPE_FORM = -32768
_MSYS_TYPE_MACRO = -32766
_MSYS_TYPE_REPORT = -32764
_MSYS_TYPE_MODULE = -32761

# Default TTL for the COM Application reference (seconds).  After the last
# COM operation, the reference is kept alive for this long to benefit burst
# workloads.  When the timer expires, the COM reference is released so that
# Access can exit normally when the user closes it.  We never close the
# database or quit Access — that is the user's responsibility.
_DEFAULT_APP_TTL_SECONDS = 5.0

# ODBC error patterns that indicate the query likely references a VBA UDF or
# Access domain function (DLookup, DCount, etc.) that requires the Application
# context.  When any of these match, the COM backend retries the query via DAO
# CurrentDb().OpenRecordset() which has access to compiled VBA modules.
_UDF_ERROR_PATTERNS = [
    re.compile(r"undefined function", re.IGNORECASE),
    re.compile(r"too few parameters", re.IGNORECASE),
]

# DAO Field.Type integer codes → human-readable type names.
# Reference: https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/datatypeenum-enumeration-dao
_DAO_FIELD_TYPES: dict[int, str] = {
    1: "Boolean",
    2: "Byte",
    3: "Integer",
    4: "Long",
    5: "Currency",
    6: "Single",
    7: "Double",
    8: "Date",
    9: "Binary",
    10: "Text",
    11: "LongBinary",
    12: "Memo",
    15: "GUID",
    16: "BigInt",
    17: "VarBinary",
    18: "Char",
    19: "Numeric",
    20: "Float",
    21: "Time",
    22: "TimeStamp",
}


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
    - The Access Application reference is acquired on demand (via GetObject
      for an existing instance, or EnsureDispatch for a new one) and released
      after a TTL of inactivity (default 5 seconds), mirroring the ODBC TTL
      strategy.  Releasing the COM reference allows Access to exit normally
      when the user closes it — we never close the database or quit the
      Access application ourselves.  That is the user's responsibility.
    - On the next tool call after the TTL expires, the reference is
      re-acquired via GetObject (fast, ~10 ms when Access is still running).
    - The DAO Database is opened per-request and closed when done.  If the
      user's Access has our database open, CurrentDb() is used (no extra
      lock).  Otherwise, DBEngine.OpenDatabase() opens a temporary handle
      that is closed after each operation.
    - SQL query execution is delegated to an internal ODBC backend, which
      uses its own TTL-cached connection.
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

        # COM Application lifecycle management
        self._com_lock = threading.Lock()
        self._close_timer: threading.Timer | None = None
        self._app_ttl: float = (
            connection_ttl_seconds
            if connection_ttl_seconds is not None
            else _DEFAULT_APP_TTL_SECONDS
        )
    
    def _extract_db_path(self, connection_string: str) -> str:
        """Extract database path from connection string."""
        # Look for DBQ= in connection string
        match = re.search(r'DBQ=([^;]+)', connection_string, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # If no DBQ found, assume connection_string is the path
        return connection_string

    def _extract_password(self, connection_string: str) -> str:
        """Extract password from connection string, or return empty string."""
        match = re.search(r'PWD=([^;]+)', connection_string, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""

    @staticmethod
    def _is_com_disconnected(exc: Exception) -> bool:
        """Check whether *exc* indicates the COM server has disconnected."""
        if pywintypes is not None and isinstance(exc, pywintypes.com_error):
            # RPC_E_DISCONNECTED, CO_E_OBJNOTCONNECTED, RPC_S_SERVER_UNAVAILABLE
            hr = getattr(exc, "hresult", None)
            if hr in (-2147023174, -2147220995, -2147023175):
                return True
        # Fallback: look for common keywords in the error message
        msg = str(exc).lower()
        return any(kw in msg for kw in (
            "rpc server", "disconnected", "server unavailable",
            "object is not connected",
        ))

    @staticmethod
    def _is_udf_error(exc: Exception) -> bool:
        """Check whether *exc* looks like a missing VBA UDF or domain function.

        The ODBC driver cannot resolve VBA user-defined functions or
        Application-level domain functions (DLookup, DCount, …).  It
        reports them as "undefined function" or treats unrecognised
        identifiers as parameters ("too few parameters").
        """
        msg = str(exc)
        return any(p.search(msg) for p in _UDF_ERROR_PATTERNS)

    # ------------------------------------------------------------------
    # COM Application lifecycle (TTL timer)
    # ------------------------------------------------------------------

    def _cancel_close_timer(self) -> None:
        """Cancel any pending Application close timer.

        The caller is expected to hold ``_com_lock`` (or to call this before
        any lock-sensitive operation where a race is impossible, e.g. during
        ``__init__``).
        """
        if self._close_timer is not None:
            self._close_timer.cancel()
            self._close_timer = None

    def _schedule_app_close(self) -> None:
        """Schedule the Application to be released after the TTL."""
        with self._com_lock:
            self._cancel_close_timer()
            if self._app is None:
                return
            if self._app_ttl > 0:
                self._close_timer = threading.Timer(
                    self._app_ttl, self._close_on_timer,
                )
                self._close_timer.daemon = True
                self._close_timer.start()
            else:
                # TTL == 0 → release immediately
                self._release_app()

    def _release_app(self) -> None:
        """Release the COM reference to the Access Application.

        This allows Access to exit normally when the user closes it.
        We never close the database or quit Access — that is the user's
        responsibility.  Must hold ``_com_lock``.
        """
        if self._app is not None:
            logger.debug("COM Application TTL expired — releasing reference")
            self._app = None

    def _close_on_timer(self) -> None:
        """Called by the Timer thread when the Application TTL expires."""
        with self._com_lock:
            self._release_app()
            self._close_timer = None

    # ------------------------------------------------------------------
    # Path / Application helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _paths_match(a: str, b: str) -> bool:
        """Return True if two file paths refer to the same file."""
        try:
            return os.path.normcase(os.path.abspath(a)) == os.path.normcase(
                os.path.abspath(b)
            )
        except Exception:
            return a.lower() == b.lower()

    def _ensure_current_db(self, app) -> None:
        """Ensure our database is open as the CurrentDb in *app*.

        Needed for operations that require the full Application context
        (e.g. VBA function calls via ``Application.Run``).  If the database
        is already the CurrentDb this is a no-op.
        """
        try:
            db = app.CurrentDb()
            if db is not None and self._paths_match(db.Name, self._db_path):
                return  # Already open
        except Exception:
            pass
        password = self._extract_password(self.connection_string)
        app.OpenCurrentDatabase(self._db_path, False, password)

    # ------------------------------------------------------------------
    # Application acquisition
    # ------------------------------------------------------------------

    def _find_existing_instance(self):
        """Search the Running Object Table for an Access instance with our database.

        Uses a two-tier strategy to find an already-running Access instance
        that has our database open, without triggering any file-open or
        password dialog.

        Tier 1 — Direct file moniker lookup (~1 ms):
            Uses ``CreateFileMoniker`` + ``IRunningObjectTable.GetObject``
            to check whether Access registered a file moniker for our
            database path.  ``GetObject`` only inspects the ROT — it does
            NOT fall through to moniker binding, so there is no risk of
            opening the file or showing a password dialog.

        Tier 2 — Enumerate all ROT entries (~10-50 ms):
            Iterates ``EnumRunning()``, obtains IDispatch for each entry,
            and calls ``CurrentDb()`` to compare the Name against our path.
            Catches instances where Access has our database open but did
            not register a file moniker (e.g. opened via
            ``OpenCurrentDatabase`` from another COM client).  Non-Access
            entries fail on ``CurrentDb()`` and are silently skipped.

        Returns:
            The Access Application COM object if found, else ``None``.
        """
        try:
            ctx = pythoncom.CreateBindCtx(0)  # noqa: F841 — needed for moniker display names
            rot = pythoncom.GetRunningObjectTable(0)
        except Exception:
            return None

        # Tier 1: direct file moniker lookup in the ROT
        try:
            moniker = pythoncom.CreateFileMoniker(self._db_path)
            obj = rot.GetObject(moniker)
            return win32com.client.Dispatch(
                obj.QueryInterface(pythoncom.IID_IDispatch)
            )
        except Exception:
            pass  # Not in ROT as a file moniker

        # Tier 2: enumerate all ROT entries, check CurrentDb on each
        try:
            enum = rot.EnumRunning()
            while True:
                monikers = enum.Next(1)
                if not monikers:
                    break
                try:
                    obj = rot.GetObject(monikers[0])
                    dispatch = win32com.client.Dispatch(
                        obj.QueryInterface(pythoncom.IID_IDispatch)
                    )
                    cdb = dispatch.CurrentDb()
                    if cdb is not None and self._paths_match(
                        cdb.Name, self._db_path
                    ):
                        return dispatch
                except Exception:
                    continue
        except Exception:
            pass

        return None

    def _get_access_app(self):
        """
        Get or create Access COM application.

        Uses a multi-step approach that varies based on whether the database
        is password-protected:

        **Non-password databases:**
        1. Try GetObject(path) to connect to our database if already open
        2. If not open, create NEW instance and open the database

        **Password-protected databases:**
        GetObject(path) is NOT used because OLE moniker resolution opens
        the file WITHOUT a password, causing Access to show a password
        dialog that blocks the process.  Instead:
        1. Search the Running Object Table (ROT) for an existing Access
           instance that already has our database open — first via direct
           file moniker lookup, then by enumerating all ROT entries and
           checking each instance's CurrentDb.  This finds the right
           instance even among multiple Access windows.
        2. If not found, create NEW instance and open with the password
           via OpenCurrentDatabase

        **For all newly created instances:**
        - Made visible so users can see and interact with Access
        - UserControl set to True so Access persists after COM references
          are released (the user manages closing, not us)
        - Database opened as CurrentDb for a consistent user experience

        IMPORTANT: Access can only have ONE database open at a time.
        - If user has OUR database open -> connect to their instance
        - If user has DIFFERENT database open -> create our OWN instance
        - If no Access running -> create new instance

        The Application reference is cached and managed by a TTL timer.
        After a period of inactivity the reference is released so that
        Access can exit normally when the user closes it.  On the next
        tool call the reference is re-acquired via GetObject (fast, ~10 ms
        when Access is still running) because UserControl=True keeps the
        process alive.

        Early Binding Strategy:
        When creating a new Access instance, we use gencache.EnsureDispatch() instead
        of plain Dispatch(). This generates and caches type library bindings, which:
        - Makes COM automation more reliable
        - Enables Application.Run to work correctly (late binding often fails)
        - Benefits subsequent GetObject calls (bindings are cached system-wide)
        """
        # Validate cached reference before returning it
        if self._app is not None:
            try:
                # Lightweight COM call to confirm the reference is alive
                self._app.Name  # noqa: B018 — intentional attribute access
            except Exception as e:
                if self._is_com_disconnected(e):
                    logger.info("Cached Access Application reference is stale — re-acquiring")
                    self._app = None
                else:
                    # Some other COM error — clear and re-acquire to be safe
                    logger.warning("Unexpected COM error validating Access reference: %s", e)
                    self._app = None

        if self._app is not None:
            # Cancel any pending release — we're about to use the Application
            with self._com_lock:
                self._cancel_close_timer()
            return self._app

        # --- Acquire a new Application reference ---
        password = self._extract_password(self.connection_string)

        if password:
            # Password-protected: skip GetObject(db_path) to avoid the
            # password dialog triggered by OLE moniker resolution.
            self._app = self._acquire_password_protected(password)
        else:
            # No password: GetObject(db_path) is the fastest and most
            # reliable way to find the specific Access instance with our
            # database open, even in multi-instance scenarios.
            self._app = self._acquire_for_open_db()

        return self._app

    def _acquire_password_protected(self, password: str):
        """Acquire COM Application for a password-protected database.

        Avoids ``GetObject(db_path)`` which triggers a password dialog via
        OLE moniker resolution.  Instead, searches the Running Object Table
        for an existing Access instance that already has our database open
        (the user already entered the password).

        The ROT search (``_find_existing_instance``) handles multi-instance
        scenarios — it checks ALL registered Access instances, not just the
        first one the ROT provides.  If no instance has our database, a new
        one is created and the database is opened with the password.
        """
        # Search ALL Access instances via the Running Object Table
        existing = self._find_existing_instance()
        if existing is not None:
            return existing

        # Not found — create new instance and open with password
        app = gencache.EnsureDispatch("Access.Application")
        _set_access_visible(app)
        app.UserControl = True
        app.OpenCurrentDatabase(self._db_path, False, password)
        return app

    def _acquire_for_open_db(self):
        """Acquire COM Application for a non-password database.

        Uses ``GetObject(db_path)`` which is the most reliable way to find
        the specific Access instance that has our database open, even when
        the user has multiple Access windows with different databases.  If
        no instance has our database open, creates a new one.
        """
        try:
            # GetObject with a file path returns the Application that has
            # our database open.  Works reliably in multi-instance scenarios.
            return win32com.client.GetObject(self._db_path)
        except Exception:
            pass

        # Our database is not open anywhere.  Create a new instance.
        # (If Access is running with a different database, EnsureDispatch
        # creates a separate instance — we don't interfere.)
        app = gencache.EnsureDispatch("Access.Application")
        _set_access_visible(app)
        app.UserControl = True
        app.OpenCurrentDatabase(self._db_path, False)
        return app
    
    def _open_dao_database(self, app):
        """Open a DAO Database via the given Application, returning (db, needs_close).

        Tries CurrentDb() first (no lock overhead), then DBEngine.OpenDatabase()
        in shared/read-only modes, and finally standalone DAO as a last resort.
        """
        password = self._extract_password(self.connection_string)
        dbe_connect = f";PWD={password}" if password else ""

        # If Access already has our database open, try CurrentDb() first
        try:
            current_db = app.CurrentDb()
            if current_db is not None and self._paths_match(
                current_db.Name, self._db_path
            ):
                return current_db, False
        except Exception:
            pass

        # If CurrentDb() didn't work, open via DBEngine
        try:
            dbe = app.DBEngine
            db = dbe.OpenDatabase(self._db_path, False, False, dbe_connect)
            return db, True
        except Exception:
            pass

        try:
            dbe = app.DBEngine
            db = dbe.OpenDatabase(self._db_path, False, True, dbe_connect)
            return db, True
        except Exception:
            pass

        # Last resort: try direct DAO without Access
        try:
            dbe = win32com.client.Dispatch("DAO.DBEngine.120")
            db = dbe.OpenDatabase(self._db_path, False, True, dbe_connect)
            return db, True
        except Exception as e:
            raise RuntimeError(
                f"Failed to open database '{self._db_path}' via COM. "
                f"Ensure the database file exists and is not corrupted. Error: {e}"
            ) from e

    @contextmanager
    def _dao_database(self):
        """
        Context manager that opens a DAO Database and closes it when done.
        
        If Access already has our database open (user's instance), uses
        CurrentDb() which does NOT create an additional lock. Otherwise,
        opens via DBEngine.OpenDatabase() and closes it afterwards.
        
        Cancels the Application TTL timer on entry and reschedules it on
        exit, so burst workloads reuse the Application without repeated
        start/stop overhead.
        
        If the first attempt fails with a COM disconnect error, the cached
        Application reference is cleared and acquisition is retried once.
        
        Yields:
            DAO Database object
        """
        with self._com_lock:
            self._cancel_close_timer()

        app = self._get_access_app()
        try:
            db, needs_close = self._open_dao_database(app)
        except Exception as first_err:
            if self._is_com_disconnected(first_err):
                logger.info("COM disconnected during _dao_database — retrying")
                self._app = None
                app = self._get_access_app()
                db, needs_close = self._open_dao_database(app)
            else:
                raise

        try:
            yield db
        finally:
            if needs_close and db is not None:
                try:
                    db.Close()
                except Exception:
                    pass
            self._schedule_app_close()
    
    @contextmanager
    def _dao_currentdb(self):
        """Context manager yielding CurrentDb — required for VBA UDF queries.

        Unlike ``_dao_database()`` which falls back to
        ``DBEngine.OpenDatabase``, this guarantees ``CurrentDb()`` is
        available.  VBA modules are only accessible through the
        Application's current database.

        The yielded DAO Database is **owned by the Application** — callers
        must NOT call ``.Close()`` on it.  Only Recordsets opened from it
        should be explicitly closed.

        Yields:
            DAO Database object (CurrentDb)
        """
        with self._com_lock:
            self._cancel_close_timer()

        app = self._get_access_app()
        try:
            self._ensure_current_db(app)
            db = app.CurrentDb()
        except Exception as first_err:
            if self._is_com_disconnected(first_err):
                logger.info("COM disconnected during _dao_currentdb — retrying")
                self._app = None
                app = self._get_access_app()
                self._ensure_current_db(app)
                db = app.CurrentDb()
            else:
                raise

        try:
            yield db
        finally:
            self._schedule_app_close()

    def _dao_execute(
        self, sql: str, max_rows: int | None = None,
    ) -> tuple[list[str], list[list[Any]]]:
        """Execute *sql* via DAO CurrentDb and return ``(column_names, rows)``.

        Each value is passed through ``_sanitize_value`` so the result is
        JSON-safe.  The Recordset is always closed in a ``finally`` block.
        """
        dbOpenSnapshot = 4
        with self._dao_currentdb() as db:
            rs = db.OpenRecordset(sql, dbOpenSnapshot)
            try:
                field_count = rs.Fields.Count
                col_names = [rs.Fields(i).Name for i in range(field_count)]
                rows: list[list[Any]] = []
                while not rs.EOF:
                    if max_rows is not None and len(rows) >= max_rows:
                        break
                    rows.append([
                        self._sanitize_value(rs.Fields(i).Value)
                        for i in range(field_count)
                    ])
                    rs.MoveNext()
                return col_names, rows
            finally:
                rs.Close()

    def call_vba_function(self, function_name: str, *args) -> Any:
        """
        Call a VBA function in the Access database via Application.Run.
        
        This method handles the quirk where early-bound Application.Run returns
        a tuple instead of just the result. The actual return value is always
        the first element of the tuple.
        
        The database is opened as CurrentDb (if not already) because VBA
        functions may reference database objects.
        
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
        with self._com_lock:
            self._cancel_close_timer()

        app = self._get_access_app()
        self._ensure_current_db(app)
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
        finally:
            self._schedule_app_close()
    
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
    
    # ------------------------------------------------------------------
    # DAO query execution (VBA UDF fallback)
    # ------------------------------------------------------------------

    def _dao_count_query_results(self, query: str) -> int:
        wrapped = f"SELECT COUNT(*) AS cnt FROM ({query}) AS subquery"
        _, rows = self._dao_execute(wrapped, max_rows=1)
        return rows[0][0] if rows else 0

    def _dao_get_query_columns(self, query: str) -> list[dict[str, Any]]:
        wrapped = f"SELECT TOP 0 * FROM ({query}) AS subquery"
        dbOpenSnapshot = 4
        with self._dao_currentdb() as db:
            rs = db.OpenRecordset(wrapped, dbOpenSnapshot)
            try:
                columns: list[dict[str, Any]] = []
                for i in range(rs.Fields.Count):
                    field = rs.Fields(i)
                    columns.append({
                        "name": field.Name,
                        "type": _DAO_FIELD_TYPES.get(field.Type, str(field.Type)),
                        "nullable": not getattr(field, "Required", False),
                        "precision": getattr(field, "Size", None) or None,
                        "scale": None,
                    })
                return columns
            finally:
                rs.Close()

    def _dao_sum_query_column(self, query: str, column: str) -> float | None:
        wrapped = f"SELECT SUM([{column}]) AS sum_val FROM ({query}) AS subquery"
        _, rows = self._dao_execute(wrapped, max_rows=1)
        if rows and rows[0][0] is not None:
            return rows[0][0]
        return None

    def _dao_measure_query(self, query: str, max_rows: int) -> dict[str, Any]:
        if "TOP " not in query.upper():
            query_upper = query.upper().strip()
            if query_upper.startswith("SELECT"):
                query = f"SELECT TOP {max_rows} " + query[6:].lstrip()
            else:
                query = f"SELECT TOP {max_rows} * FROM ({query}) AS subquery"

        start_time = time.time()
        _, rows = self._dao_execute(query, max_rows=max_rows)
        execution_time_ms = (time.time() - start_time) * 1000

        row_count = len(rows)
        return {
            "execution_time_ms": round(execution_time_ms, 2),
            "row_count": row_count,
            "hit_limit": row_count >= max_rows,
        }

    def _dao_preview(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        if "TOP " not in query.upper():
            query_upper = query.upper().strip()
            if query_upper.startswith("SELECT"):
                query = f"SELECT TOP {max_rows} " + query[6:].lstrip()
            else:
                query = f"SELECT TOP {max_rows} * FROM ({query}) AS subquery"

        col_names, rows = self._dao_execute(query, max_rows=max_rows)
        return [{col: val for col, val in zip(col_names, row)} for row in rows]

    # ------------------------------------------------------------------
    # Public query methods — ODBC first, DAO fallback for VBA UDFs
    # ------------------------------------------------------------------

    def count_query_results(self, query: str) -> int:
        """Count row count by wrapping query in SELECT COUNT(*)."""
        try:
            return self._odbc_backend.count_query_results(query)
        except Exception as e:
            if self._is_udf_error(e):
                logger.info("ODBC query failed (likely VBA UDF); retrying via DAO")
                return self._dao_count_query_results(query)
            raise

    def get_query_columns(self, query: str) -> list[dict[str, Any]]:
        """Get column metadata using TOP 0 to get metadata without fetching data."""
        try:
            return self._odbc_backend.get_query_columns(query)
        except Exception as e:
            if self._is_udf_error(e):
                logger.info("ODBC query failed (likely VBA UDF); retrying via DAO")
                return self._dao_get_query_columns(query)
            raise

    def sum_query_column(self, query: str, column: str) -> float | None:
        """Compute SUM of a column from query results."""
        try:
            return self._odbc_backend.sum_query_column(query, column)
        except Exception as e:
            if self._is_udf_error(e):
                logger.info("ODBC query failed (likely VBA UDF); retrying via DAO")
                return self._dao_sum_query_column(query, column)
            raise

    def measure_query(self, query: str, max_rows: int) -> dict[str, Any]:
        """Measure query execution time and retrieve limited rows."""
        try:
            return self._odbc_backend.measure_query(query, max_rows)
        except Exception as e:
            if self._is_udf_error(e):
                logger.info("ODBC query failed (likely VBA UDF); retrying via DAO")
                return self._dao_measure_query(query, max_rows)
            raise

    def preview(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        """Sample N rows from a query result."""
        try:
            return self._odbc_backend.preview(query, max_rows)
        except Exception as e:
            if self._is_udf_error(e):
                logger.info("ODBC query failed (likely VBA UDF); retrying via DAO")
                return self._dao_preview(query, max_rows)
            raise

    def explain_query(self, query: str) -> str:
        """Get execution plan."""
        return self._odbc_backend.explain_query(query)
    
    # ------------------------------------------------------------------
    # Object counts (cheap, used by db_list_databases)
    # ------------------------------------------------------------------

    def _counts_via_msysobjects(self) -> dict[str, int | None]:
        """Full inventory via MSysObjects through the Application (~100ms)."""
        dbOpenSnapshot = 4  # DAO constant
        counts: dict[str, int | None] = {
            "tables": 0,
            "linked_tables": 0,
            "queries": 0,
            "forms": 0,
            "reports": 0,
            "macros": 0,
            "modules": 0,
        }
        type_map = {
            _MSYS_TYPE_LOCAL_TABLE: "tables",
            _MSYS_TYPE_ODBC_LINKED: "linked_tables",
            _MSYS_TYPE_QUERY: "queries",
            _MSYS_TYPE_LINKED_TABLE: "linked_tables",
            _MSYS_TYPE_FORM: "forms",
            _MSYS_TYPE_REPORT: "reports",
            _MSYS_TYPE_MACRO: "macros",
            _MSYS_TYPE_MODULE: "modules",
        }
        try:
            with self._dao_database() as db:
                sql = "SELECT Type, COUNT(*) AS cnt FROM MSysObjects GROUP BY Type"
                rs = db.OpenRecordset(sql, dbOpenSnapshot)
                while not rs.EOF:
                    type_val = rs.Fields("Type").Value
                    cnt = rs.Fields("cnt").Value
                    key = type_map.get(type_val)
                    if key is not None:
                        counts[key] = (counts[key] or 0) + cnt
                    rs.MoveNext()
                rs.Close()
        except Exception:
            logger.debug("MSysObjects count failed", exc_info=True)
            return {}
        return counts

    def get_object_counts(self) -> dict[str, int | None]:
        """Return full object counts via MSysObjects through the Application.

        Always acquires the Application (via GetObject probe or
        EnsureDispatch) so agents get a complete inventory including
        forms, reports, macros, and modules.  The Application reference
        is cached, so subsequent tool calls (list_tables, list_views,
        query execution) benefit from the warm connection.

        db_list_databases is a one-time orientation call, and the agent
        almost always follows up with list_tables/list_views which need
        the Application anyway, so front-loading the startup cost here
        is a net win for overall session performance.
        """
        return self._counts_via_msysobjects()

    # ------------------------------------------------------------------
    # list_tables / list_views — MSysObjects via Application
    # ------------------------------------------------------------------

    def list_tables(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """List tables using MSysObjects through the Application.

        Returns richer metadata than TableDefs iteration, including
        whether a table is local or linked.
        """
        dbOpenSnapshot = 4
        table_types = (_MSYS_TYPE_LOCAL_TABLE, _MSYS_TYPE_ODBC_LINKED, _MSYS_TYPE_LINKED_TABLE)
        type_labels = {
            _MSYS_TYPE_LOCAL_TABLE: "local",
            _MSYS_TYPE_ODBC_LINKED: "linked_odbc",
            _MSYS_TYPE_LINKED_TABLE: "linked",
        }

        where = (
            f"Type IN ({','.join(str(t) for t in table_types)}) "
            "AND Name NOT LIKE 'MSys*' AND Name NOT LIKE '~*'"
        )
        if name_filter:
            safe = name_filter.replace("'", "''")
            where += f" AND Name LIKE '*{safe}*'"

        sql = f"SELECT Name, Type FROM MSysObjects WHERE {where} ORDER BY Name"

        with self._dao_database() as db:
            tables: list[dict[str, Any]] = []
            try:
                rs = db.OpenRecordset(sql, dbOpenSnapshot)
                while not rs.EOF:
                    tables.append({
                        "name": rs.Fields("Name").Value,
                        "schema": "dbo",
                        "table_type": type_labels.get(rs.Fields("Type").Value, "unknown"),
                        "row_count": None,
                    })
                    rs.MoveNext()
                rs.Close()
            except Exception:
                # MSysObjects failed (shouldn't happen via Application, but be safe)
                logger.debug("MSysObjects list_tables failed — falling back to TableDefs", exc_info=True)
                tables = self._list_tables_via_tabledefs(db, name_filter)
            return tables

    @staticmethod
    def _list_tables_via_tabledefs(db, name_filter: str | None) -> list[dict[str, Any]]:
        """Fallback: iterate TableDefs (no type info, slower via out-of-process COM)."""
        tables: list[dict[str, Any]] = []
        filt = name_filter.lower() if name_filter else None
        for td in db.TableDefs:
            nm = td.Name
            if nm.startswith("MSys") or nm.startswith("~"):
                continue
            if filt and filt not in nm.lower():
                continue
            tables.append({
                "name": nm,
                "schema": "dbo",
                "table_type": "unknown",
                "row_count": None,
            })
        return tables

    def list_views(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """List queries using MSysObjects through the Application.

        Returns query names without SQL definitions (use
        get_query_by_name() for SQL).
        """
        dbOpenSnapshot = 4

        where = "Type = 5 AND Name NOT LIKE '~*'"
        if name_filter:
            safe = name_filter.replace("'", "''")
            where += f" AND Name LIKE '*{safe}*'"

        sql = f"SELECT Name FROM MSysObjects WHERE {where} ORDER BY Name"

        with self._dao_database() as db:
            views: list[dict[str, Any]] = []
            try:
                rs = db.OpenRecordset(sql, dbOpenSnapshot)
                while not rs.EOF:
                    views.append({
                        "name": rs.Fields("Name").Value,
                        "schema": "dbo",
                        "definition": None,
                    })
                    rs.MoveNext()
                rs.Close()
            except Exception:
                logger.debug("MSysObjects list_views failed — falling back to QueryDefs", exc_info=True)
                views = self._list_views_via_querydefs(db, name_filter)
            return views

    @staticmethod
    def _list_views_via_querydefs(db, name_filter: str | None) -> list[dict[str, Any]]:
        """Fallback: iterate QueryDefs."""
        views: list[dict[str, Any]] = []
        filt = name_filter.lower() if name_filter else None
        for qd in db.QueryDefs:
            nm = qd.Name
            if nm.startswith("~"):
                continue
            if filt and filt not in nm.lower():
                continue
            views.append({
                "name": nm,
                "schema": "dbo",
                "definition": None,
            })
        return views
    
    def verify_readonly(self) -> dict[str, Any]:
        """Verify user has no write permissions."""
        return self._odbc_backend.verify_readonly()
