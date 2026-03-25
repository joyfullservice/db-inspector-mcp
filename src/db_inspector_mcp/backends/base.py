"""Abstract base class for database backends."""

import json
from abc import ABC, abstractmethod
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID


class DatabaseBackend(ABC):
    """Abstract base class defining the interface for database backends."""
    
    def __init__(self, connection_string: str, query_timeout_seconds: int = 30):
        """
        Initialize the database backend.
        
        Args:
            connection_string: Database connection string
            query_timeout_seconds: Query timeout in seconds
        """
        self.connection_string = connection_string
        self.query_timeout_seconds = query_timeout_seconds

    @property
    def is_connected(self) -> bool:
        """Whether this backend currently holds an active connection.

        Used by ``db_list_databases`` to avoid opening fresh connections just
        to retrieve object counts.  Backends override this to reflect their
        actual connection state.  The default is ``False``.
        """
        return False

    def close(self) -> None:
        """Release backend resources (connections, timers, handles).

        Backends override this when they keep long-lived resources. The default
        implementation is a no-op so callers can always invoke ``close()``
        safely during cleanup paths.
        """
        return None
    
    # -------------------------------------------------------------------------
    # Row sanitization helpers
    # -------------------------------------------------------------------------
    @staticmethod
    def _sanitize_value(value: Any) -> Any:
        """
        Convert a single database value to a JSON-serializable type.
        
        Handles types that pyodbc/database drivers return but that are not
        natively JSON-serializable, including:
        - bytes/bytearray (e.g., SQL Server timestamp/rowversion, binary columns)
        - Decimal → float
        - datetime/date/time → ISO-format string
        - UUID → string
        - Strings with invalid surrogate characters → cleaned strings
        
        Args:
            value: A raw value from a database cursor row
            
        Returns:
            A JSON-safe Python primitive (str, int, float, bool, None, list, or dict)
        """
        if value is None:
            return None
        
        # bytes / bytearray → hex string (e.g., SQL Server timestamp/rowversion)
        if isinstance(value, (bytes, bytearray)):
            return f"0x{value.hex()}"
        
        # Decimal → float (preserves numeric meaning)
        if isinstance(value, Decimal):
            return float(value)
        
        # datetime / date / time → ISO-format string
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, time):
            return value.isoformat()
        
        # UUID → string
        if isinstance(value, UUID):
            return str(value)
        
        # Strings: ensure they are valid for JSON encoding (no lone surrogates)
        if isinstance(value, str):
            try:
                # Test that the string can be encoded as UTF-8
                value.encode("utf-8")
                return value
            except UnicodeEncodeError:
                # Replace lone surrogates or other un-encodable chars
                return value.encode("utf-8", errors="replace").decode("utf-8")
        
        # int, float, bool pass through directly
        if isinstance(value, (int, float, bool)):
            return value
        
        # Fallback: convert to string
        return str(value)
    
    @classmethod
    def _sanitize_rows(cls, column_names: list[str], rows: list) -> list[dict[str, Any]]:
        """
        Convert a list of cursor rows into JSON-safe dictionaries.
        
        This replaces the common pattern:
            [dict(zip(column_names, row)) for row in rows]
        with a version that sanitizes every value.
        
        Args:
            column_names: List of column names from cursor.description
            rows: List of rows from cursor.fetchall()
            
        Returns:
            List of dictionaries with sanitized values
        """
        return [
            {col: cls._sanitize_value(val) for col, val in zip(column_names, row)}
            for row in rows
        ]
    
    @property
    @abstractmethod
    def sql_dialect(self) -> str:
        """
        Return the SQL dialect identifier for this backend.
        
        Returns:
            Dialect string: 'access', 'mssql', 'postgres', etc.
        """
        pass
    
    @abstractmethod
    def count_query_results(self, query: str) -> int:
        """
        Count the number of rows a SELECT query returns.
        
        Args:
            query: SQL query to count rows for
            
        Returns:
            Number of rows
        """
        pass
    
    @abstractmethod
    def get_query_columns(self, query: str) -> list[dict[str, Any]]:
        """
        Get column metadata for a SQL query.
        
        Args:
            query: SQL query to get columns for
            
        Returns:
            List of dictionaries with column metadata:
            - name: Column name
            - type: Data type
            - nullable: Whether column allows NULL
            - precision: Numeric precision (if applicable)
            - scale: Numeric scale (if applicable)
        """
        pass
    
    @abstractmethod
    def sum_query_column(self, query: str, column: str) -> float | None:
        """
        Compute the SUM() of a single column from query results.
        
        Args:
            query: SQL query to sum a column from
            column: Column name to sum
            
        Returns:
            Sum value, or None if all values are NULL
        """
        pass
    
    @abstractmethod
    def measure_query(self, query: str, max_rows: int) -> dict[str, Any]:
        """
        Measure query execution time and retrieve limited rows.
        
        Args:
            query: SQL query to measure
            max_rows: Maximum number of rows to retrieve
            
        Returns:
            Dictionary with:
            - execution_time_ms: Query execution time in milliseconds
            - row_count: Number of rows retrieved
            - hit_limit: Whether the row limit was reached
        """
        pass
    
    @abstractmethod
    def preview(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        """
        Sample N rows from a query result.
        
        Args:
            query: SQL query to preview
            max_rows: Maximum number of rows to return
            
        Returns:
            List of dictionaries, each representing a row
        """
        pass
    
    @abstractmethod
    def explain_query(self, query: str) -> str:
        """
        Get database-native execution plan.
        
        Args:
            query: SQL query to explain
            
        Returns:
            Execution plan as a string (XML for SQL Server, JSON for Postgres)
        """
        pass
    
    def get_object_counts(self) -> dict[str, int | None]:
        """
        Get counts of database objects by type.
        
        Returns a dict whose keys are backend-specific object type names
        (e.g. "tables", "views", "queries", "forms", "stored_procedures")
        and whose values are integer counts.  Only includes keys the
        backend can actually determine — unknown object types are omitted
        rather than set to None, so the absence of a key means "not
        measured" while a value of 0 means "we checked and found none."
        
        The default implementation returns an empty dict.  Backends should
        override this to provide richer information using the cheapest
        available path.
        """
        return {}
    
    @abstractmethod
    def list_tables(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """
        List tables in the database, optionally filtered by name.
        
        Args:
            name_filter: Optional case-insensitive substring filter.
                When provided, only tables whose name contains this
                string are returned.
        
        Returns:
            List of dictionaries with table metadata:
            - name: Table name
            - schema: Schema name
            - row_count: Approximate row count (if available)
        """
        pass
    
    @abstractmethod
    def list_views(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """
        List views/queries in the database, optionally filtered by name.
        
        Args:
            name_filter: Optional case-insensitive substring filter.
                When provided, only views whose name contains this
                string are returned.
        
        Returns:
            List of dictionaries with view metadata:
            - name: View name
            - schema: Schema name
            - definition: SQL definition of the view
        """
        pass
    
    @abstractmethod
    def verify_readonly(self) -> dict[str, Any]:
        """
        Verify that the database connection is read-only.
        
        Returns:
            Dictionary with:
            - readonly: Boolean indicating if connection is read-only
            - details: String with detailed status information
        """
        pass

