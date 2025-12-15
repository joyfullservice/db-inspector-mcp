"""
MCP tool definitions for db-inspector-mcp.

This module provides database introspection tools for AI assistants working with SQL databases.
All tools are read-only by default and designed for safe database exploration and validation.

**Getting Started Workflow:**
1. Always start with db_list_databases() to discover available databases
2. Use db_list_tables() or db_list_views() to explore the schema
3. Use db_columns() to understand query structure
4. Use db_row_count() to validate query results
5. Use db_compare_queries() for migration validation

**Key Features:**
- Multi-database support (SQL Server, PostgreSQL, Access)
- Cross-database comparison for migrations
- Read-only by default (write operations are blocked)
- Schema discovery and query analysis
- Performance measurement and execution plans
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from .backends.access_com import AccessCOMBackend
from .backends.registry import get_registry
from .config import check_data_access, get_config
from .security import validate_readonly_sql


# Create FastMCP server instance with proper metadata
mcp = FastMCP(
    name="db-inspector-mcp",
    instructions=(
        "A lightweight, extensible, cross-database MCP server for database introspection. "
        "Provides read-only tools for exploring database schemas, validating queries, "
        "and comparing databases. Supports SQL Server, PostgreSQL, and Microsoft Access."
    )
)


@mcp.tool()
def db_row_count(sql: str, database: str | None = None) -> dict[str, Any]:
    """
    Return the number of rows an arbitrary SQL query would produce.
    
    Use this tool to:
    - Validate query results match expectations
    - Compare row counts between queries or databases
    - Check data volume before running operations
    - Verify migrations by comparing row counts
    
    Args:
        sql: SQL SELECT query to count rows for (must be a SELECT statement, read-only)
        database: Name of the database to use. Call db_list_databases() first to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with "count" key containing the row count as an integer
    """
    validate_readonly_sql(sql)
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        count = backend.get_row_count(sql)
        return {"count": count}
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "count": None}


@mcp.tool()
def db_columns(sql: str, database: str | None = None) -> dict[str, Any]:
    """
    Return column names, data types, nullability, and precision/scale for a SQL query.
    
    Use this tool to:
    - Understand the schema/structure of query results
    - Validate column types match expectations
    - Compare schemas between queries or databases
    - Build type-safe code based on query results
    - Debug schema mismatches during migrations
    
    Args:
        sql: SQL SELECT query to get columns for (must be a SELECT statement, read-only)
        database: Name of the database to use. Call db_list_databases() first to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with "columns" key containing list of column metadata dictionaries.
        Each column dict includes: name, type, nullable, precision, scale, etc.
    """
    validate_readonly_sql(sql)
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        columns = backend.get_columns(sql)
        return {"columns": columns}
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "columns": []}


@mcp.tool()
def db_sum_column(sql: str, column: str, database: str | None = None) -> dict[str, Any]:
    """
    Compute the SUM() of a single column for validation and aggregation checks.
    
    Use this tool to:
    - Validate aggregate values match expectations
    - Compare totals across databases during migrations
    - Verify financial/transaction totals
    - Check data integrity through aggregate validation
    
    Args:
        sql: SQL SELECT query to sum a column from (must be SELECT, read-only)
        column: Column name to sum (must exist in the query result)
        database: Name of the database to use. Call db_list_databases() first to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with "sum" key containing the sum value (numeric) or None if column not found
    """
    validate_readonly_sql(sql)
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        sum_val = backend.sum_column(sql, column)
        return {"sum": sum_val}
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "sum": None}


@mcp.tool()
def db_measure_query(sql: str, max_rows: int = 1000, database: str | None = None) -> dict[str, Any]:
    """
    Measure query execution time and retrieve limited rows for performance testing.
    
    Use this tool to:
    - Measure query performance (execution time)
    - Test query speed before running full queries
    - Validate queries return expected row counts
    - Performance benchmarking
    
    Args:
        sql: SQL SELECT query to measure (must be SELECT, read-only)
        max_rows: Maximum number of rows to retrieve (default: 1000). Query stops after this limit.
        database: Name of the database to use. Call db_list_databases() first to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with:
        - execution_time_ms: Query execution time in milliseconds
        - row_count: Number of rows actually retrieved
        - hit_limit: Boolean indicating if max_rows limit was reached
    """
    validate_readonly_sql(sql)
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        result = backend.measure_query(sql, max_rows)
        return result
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "execution_time_ms": None, "row_count": 0, "hit_limit": False}


@mcp.tool()
def db_preview(sql: str, max_rows: int = 100, database: str | None = None) -> dict[str, Any]:
    """
    Sample N rows from a query result to preview actual data.
    
    **Requires data access permission** (set DB_MCP_ALLOW_DATA_ACCESS=true or DB_MCP_ALLOW_PREVIEW=true in .env).
    
    Use this tool to:
    - Preview query results
    - Spot-check data values
    - Validate data quality
    - Debug data issues
    - Compare sample data across databases
    
    Args:
        sql: SQL SELECT query to preview (must be SELECT, read-only)
        max_rows: Maximum number of rows to return (default: 100)
        database: Name of the database to use. Call db_list_databases() first to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with "rows" key containing list of row dictionaries.
        Each row is a dict mapping column names to values.
    """
    validate_readonly_sql(sql)
    check_data_access("db_preview")  # Check permission
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        rows = backend.preview(sql, max_rows)
        return {"rows": rows}
    except PermissionError:
        raise  # Re-raise permission errors
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "rows": []}


@mcp.tool()
def db_explain(sql: str, database: str | None = None) -> dict[str, Any]:
    """
    Return database-native execution plan (EXPLAIN/EXPLAIN PLAN output).
    
    Use this tool to:
    - Analyze query performance
    - Identify missing indexes
    - Understand query execution strategy
    - Debug slow queries
    - Optimize query performance
    
    Args:
        sql: SQL SELECT query to explain (must be SELECT, read-only)
        database: Name of the database to use. Call db_list_databases() first to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with "plan" key containing execution plan as string (format varies by database).
        For SQL Server: XML execution plan. For PostgreSQL: EXPLAIN output.
    """
    validate_readonly_sql(sql)
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        plan = backend.explain_query(sql)
        return {"plan": plan}
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "plan": None}


@mcp.tool()
def db_compare_queries(
    sql1: str,
    sql2: str,
    compare_samples: bool = False,
    database1: str | None = None,
    database2: str | None = None
) -> dict[str, Any]:
    """
    Compare two queries side-by-side, optionally from different databases.
    
    **Perfect for migration validation!** Use this tool to:
    - Validate migrated queries produce matching results
    - Compare query performance and structure
    - Verify refactored queries maintain correctness
    - Test queries across dev/staging/prod environments
    - Compare Access queries to SQL Server equivalents
    
    Always call db_list_databases() first to discover available database names.
    
    Args:
        sql1: First SQL SELECT query to compare (must be SELECT, read-only)
        sql2: Second SQL SELECT query to compare (must be SELECT, read-only)
        compare_samples: If True, compare sample data (requires DB_MCP_ALLOW_DATA_ACCESS=true)
        database1: Name of the database for sql1. Call db_list_databases() to discover
                   available names. If not specified, uses the default database.
        database2: Name of the database for sql2. Call db_list_databases() to discover
                    available names. If not specified, uses database1 (same database comparison).
        
    Returns:
        Dictionary with:
        - row_count_diff: Difference in row counts (positive means sql2 has more)
        - row_count_1, row_count_2: Individual row counts
        - columns_missing_in_2, columns_missing_in_1: Column name differences
        - type_mismatches: List of columns with different types
        - sample_differences: (if compare_samples=True) Sample data comparison
    """
    validate_readonly_sql(sql1)
    validate_readonly_sql(sql2)
    
    if compare_samples:
        check_data_access("db_preview")  # Sample comparison requires data access
    
    registry = get_registry()
    backend1 = registry.get(database1)
    # If database2 is not specified, use database1 (same database comparison)
    backend2 = registry.get(database2 if database2 is not None else database1)
    
    try:
        # Get row counts
        count1 = backend1.get_row_count(sql1)
        count2 = backend2.get_row_count(sql2)
        row_count_diff = count2 - count1
        
        # Get column schemas
        cols1 = backend1.get_columns(sql1)
        cols2 = backend2.get_columns(sql2)
        
        # Compare columns
        col_names1 = {col["name"] for col in cols1}
        col_names2 = {col["name"] for col in cols2}
        
        missing_in_2 = col_names1 - col_names2
        missing_in_1 = col_names2 - col_names1
        common_cols = col_names1 & col_names2
        
        # Check for type mismatches
        type_mismatches = []
        cols1_dict = {col["name"]: col for col in cols1}
        cols2_dict = {col["name"]: col for col in cols2}
        
        for col_name in common_cols:
            type1 = cols1_dict[col_name].get("type")
            type2 = cols2_dict[col_name].get("type")
            if type1 != type2:
                type_mismatches.append({
                    "column": col_name,
                    "type1": type1,
                    "type2": type2,
                })
        
        result = {
            "row_count_diff": row_count_diff,
            "row_count_1": count1,
            "row_count_2": count2,
            "columns_missing_in_2": list(missing_in_2),
            "columns_missing_in_1": list(missing_in_1),
            "type_mismatches": type_mismatches,
            "database1": database1 or registry.get_default_name(),
            "database2": database2 or database1 or registry.get_default_name(),
        }
        
        # Compare samples if requested
        if compare_samples:
            try:
                samples1 = backend1.preview(sql1, 10)
                samples2 = backend2.preview(sql2, 10)
                result["sample_differences"] = {
                    "samples_1_count": len(samples1),
                    "samples_2_count": len(samples2),
                    "note": "Sample comparison limited to first 10 rows",
                }
            except Exception as e:
                result["sample_differences"] = {"error": str(e)}
        
        return result
        
    except PermissionError:
        raise  # Re-raise permission errors
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def db_list_tables(database: str | None = None) -> dict[str, Any]:
    """
    List all tables in the database with metadata (name, schema, row counts, etc.).
    
    Use this tool to:
    - Explore the database schema
    - Discover available tables
    - Understand database structure
    - Find tables for queries
    
    Args:
        database: Name of the database to use. Call db_list_databases() first to discover
                  available database names. If not specified, uses the default database.
    
    Returns:
        Dictionary with "tables" key containing list of table metadata dictionaries.
        Each table dict includes: name, schema, row_count, and other metadata.
    """
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        tables = backend.list_tables()
        return {"tables": tables}
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "tables": []}


@mcp.tool()
def db_list_views(database: str | None = None) -> dict[str, Any]:
    """
    List all views in the database with their SQL definitions.
    
    Use this tool to:
    - Discover available views
    - Understand view definitions and logic
    - Compare views across databases
    - Debug view-related issues
    
    Args:
        database: Name of the database to use. Call db_list_databases() first to discover
                  available database names. If not specified, uses the default database.
    
    Returns:
        Dictionary with "views" key containing list of view metadata dictionaries.
        Each view dict includes: name, schema, definition (SQL), and other metadata.
    """
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        views = backend.list_views()
        return {"views": views}
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "views": []}


@mcp.tool()
def db_verify_readonly(database: str | None = None) -> dict[str, Any]:
    """
    Verify that the database connection is read-only for safety confirmation.
    
    Use this tool to:
    - Confirm database safety before operations
    - Validate read-only configuration
    - Check security settings
    
    Args:
        database: Name of the database to use. Call db_list_databases() first to discover
                  available database names. If not specified, uses the default database.
    
    Returns:
        Dictionary with:
        - "readonly": Boolean indicating if connection is read-only
        - "details": String with verification details and status
    """
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        result = backend.verify_readonly()
        return result
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"readonly": False, "details": f"Error during verification: {str(e)}"}


@mcp.tool()
def db_get_access_query(name: str, database: str | None = None) -> dict[str, Any]:
    """
    Get Access query SQL by name (requires access_com backend).
    
    Use this tool to:
    - Retrieve native SQL from Access queries by name
    - Get query definitions for migration workflows
    - Understand Access query structure
    
    **Note**: This tool requires the `access_com` backend (not `access_odbc`).
    Set DB_MCP_DATABASE=access_com to use this functionality.
    
    Args:
        name: Name of the Access query to retrieve
        database: Name of the database to use. Call db_list_databases() first to discover
                  available database names. If not specified, uses the default database.
    
    Returns:
        Dictionary with:
        - "name": Query name
        - "sql": Native SQL definition
        - "type": Query type (Select, Union, etc.)
    """
    registry = get_registry()
    backend = registry.get(database)
    
    if not isinstance(backend, AccessCOMBackend):
        raise ValueError(
            f"db_get_access_query requires access_com backend, but database '{database or 'default'}' "
            f"uses {type(backend).__name__}. Set DB_MCP_DATABASE=access_com to use this feature."
        )
    
    try:
        query = backend.get_query_by_name(name)
        return query
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "name": name, "sql": None, "type": None}


@mcp.tool()
def db_list_databases() -> dict[str, Any]:
    """
    List all available database backends that have been configured.
    
    **IMPORTANT: Always call this tool first** to discover available database names before using
    other tools. Database names are user-defined and configured via environment variables.
    
    Use this tool when:
    - Starting any database operation to see what databases are available
    - Working with multi-database configurations (migrations, testing, etc.)
    - You need to know which database is the default
    
    Returns:
        Dictionary with "databases" key containing list of database names and default indicator.
        Each database entry has "name" and "is_default" fields.
    """
    registry = get_registry()
    backend_names = registry.list_backends()
    default_name = registry.get_default_name()
    
    databases = [
        {
            "name": name,
            "is_default": name == default_name
        }
        for name in backend_names
    ]
    
    return {
        "databases": databases,
        "default": default_name
    }

