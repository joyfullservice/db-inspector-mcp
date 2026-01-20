"""Abstract base class for database backends."""

from abc import ABC, abstractmethod
from typing import Any


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
    
    @abstractmethod
    def list_tables(self) -> list[dict[str, Any]]:
        """
        List all tables in the database.
        
        Returns:
            List of dictionaries with table metadata:
            - name: Table name
            - schema: Schema name
            - row_count: Approximate row count (if available)
        """
        pass
    
    @abstractmethod
    def list_views(self) -> list[dict[str, Any]]:
        """
        List all views in the database with their definitions.
        
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

