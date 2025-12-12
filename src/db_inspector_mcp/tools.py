"""MCP tool definitions for db-inspector-mcp."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from .backends.registry import get_registry
from .config import check_data_access, get_config
from .security import validate_readonly_sql


# Create FastMCP server instance
mcp = FastMCP("db-inspector-mcp")


@mcp.tool()
def db_row_count(sql: str, database: str | None = None) -> dict[str, Any]:
    """
    Return the number of rows an arbitrary SQL query would produce.
    
    Args:
        sql: SQL SELECT query to count rows for
        database: Name of the database to use. Use db_list_databases() to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with "count" key containing the row count
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
    Return column names, data types, nullability, and precision/scale.
    
    Args:
        sql: SQL SELECT query to get columns for
        database: Name of the database to use. Use db_list_databases() to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with "columns" key containing list of column metadata
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
    Compute the SUM() of a single column for validation scenarios.
    
    Args:
        sql: SQL SELECT query to sum a column from
        column: Column name to sum
        database: Name of the database to use. Use db_list_databases() to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with "sum" key containing the sum value (or None)
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
    Return execution time, number of rows retrieved, and whether row cap was hit.
    
    Args:
        sql: SQL SELECT query to measure
        max_rows: Maximum number of rows to retrieve (default: 1000)
        database: Name of the database to use. Use db_list_databases() to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with execution_time_ms, row_count, and hit_limit
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
    Sample N rows from a query result.
    
    Requires data access permission (DB_MCP_ALLOW_DATA_ACCESS or DB_MCP_ALLOW_PREVIEW).
    
    Args:
        sql: SQL SELECT query to preview
        max_rows: Maximum number of rows to return (default: 100)
        database: Name of the database to use. Use db_list_databases() to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with "rows" key containing list of row dictionaries
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
    Return database-native execution plan.
    
    Args:
        sql: SQL SELECT query to explain
        database: Name of the database to use. Use db_list_databases() to discover
                  available database names. If not specified, uses the default database.
        
    Returns:
        Dictionary with "plan" key containing execution plan as string
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
    
    This is useful for comparing queries across databases (e.g., during migrations,
    testing, or validation). Use db_list_databases() to discover available database names.
    
    Args:
        sql1: First SQL SELECT query to compare
        sql2: Second SQL SELECT query to compare
        compare_samples: If True, compare sample data (requires data access permission)
        database1: Name of the database for sql1. Use db_list_databases() to discover
                   available names. If not specified, uses the default database.
        database2: Name of the database for sql2. Use db_list_databases() to discover
                    available names. If not specified, uses database1 (same database comparison).
        
    Returns:
        Dictionary with row_count_diff, column_differences, and optionally sample_differences
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
    List all tables in the database with metadata.
    
    Args:
        database: Name of the database to use. Use db_list_databases() to discover
                  available database names. If not specified, uses the default database.
    
    Returns:
        Dictionary with "tables" key containing list of table metadata
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
    
    Args:
        database: Name of the database to use. Use db_list_databases() to discover
                  available database names. If not specified, uses the default database.
    
    Returns:
        Dictionary with "views" key containing list of view metadata
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
    Verify that the database connection is read-only.
    
    Can be called by agents to confirm safety before performing operations.
    
    Args:
        database: Name of the database to use. Use db_list_databases() to discover
                  available database names. If not specified, uses the default database.
    
    Returns:
        Dictionary with "readonly" boolean and "details" string
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
def db_list_databases() -> dict[str, Any]:
    """
    List all available database backends that have been configured.
    
    IMPORTANT: Call this tool first to discover available database names before using
    other tools. Database names are user-defined and configured via environment variables.
    
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

