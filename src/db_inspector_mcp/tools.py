"""
MCP tool definitions for db-inspector-mcp.

This module provides database introspection tools for AI assistants working with SQL databases.
All tools are read-only by default and designed for safe database exploration and validation.

**Getting Started Workflow:**
1. Always start with db_list_databases() to discover available databases
2. Use db_list_tables() or db_list_views() to explore the schema
3. Use db_get_query_columns() to understand query structure
4. Use db_count_query_results() to validate query results
5. Use db_compare_queries() for migration validation

**Key Features:**
- Multi-database support (discover available databases with db_list_databases())
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
        "A cross-database MCP server for database introspection and migration validation. "
        "Provides read-only tools for exploring schemas, analyzing queries, and comparing databases.\n\n"
        "**Recommended workflow:**\n"
        "1. Start with db_list_databases() to discover available databases\n"
        "2. Use db_list_tables() and db_list_views() to explore schemas\n"
        "3. Use db_count_query_results(), db_get_query_columns(), and db_sum_query_column() "
        "to analyze queries (these tools wrap YOUR query for efficiency - pass the base query)\n"
        "4. Use db_compare_queries() to validate migrations across databases\n\n"
        "**How query analysis tools work:**\n"
        "- db_count_query_results(query) wraps your query in SELECT COUNT(*) FROM (query)\n"
        "- db_sum_query_column(query, column) wraps your query to sum the specified column\n"
        "- db_get_query_columns(query) executes your query with 0 rows to get metadata\n"
        "Pass your base SELECT query; the tool handles aggregation."
    )
)


@mcp.tool()
def db_count_query_results(query: str, database: str | None = None) -> dict[str, Any]:
    """
    Count the number of rows a SELECT query returns.
    
    This tool wraps your query in SELECT COUNT(*) FROM (your_query) to efficiently
    count results without fetching all data. Pass your base query; the tool handles
    the COUNT aggregation.
    
    Use this tool to:
    - Count how many rows a query returns without fetching all data
    - Validate query results match expected counts
    - Compare row counts between queries or databases
    - Check data volume before running operations
    
    Examples:
        # Count active users
        db_count_query_results("SELECT * FROM users WHERE active = 1")
        # Returns: {"count": 1234}
        
        # Count with complex filtering
        db_count_query_results("SELECT * FROM orders WHERE status = 'completed' AND total > 100")
        # Returns: {"count": 567}
        
        # Works with complex queries (CTEs, subqueries, etc.)
        db_count_query_results('''
            WITH recent AS (SELECT * FROM events WHERE date > '2024-01-01')
            SELECT * FROM recent WHERE type = 'purchase'
        ''')
        # Returns: {"count": 89}
    
    Args:
        query: A SELECT query to count results from
        database: Database name (call db_list_databases() first, uses default if not specified)
        
    Returns:
        Dictionary with "count" key containing the row count as an integer
    """
    validate_readonly_sql(query)
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        count = backend.count_query_results(query)
        return {"count": count}
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "count": None}


@mcp.tool()
def db_get_query_columns(query: str, database: str | None = None) -> dict[str, Any]:
    """
    Analyze the column schema of a SELECT query's results.
    
    This tool executes your query with a limit to fetch 0 rows, allowing it to
    inspect column metadata without retrieving data. Useful for understanding
    query structure before execution.
    
    Use this tool to:
    - Understand the structure of query results before executing
    - Validate column types match expectations
    - Compare schemas between queries or databases
    - Build type-safe code based on query results
    - Debug schema mismatches during migrations
    
    Examples:
        # Analyze columns from a simple query
        db_get_query_columns("SELECT id, name, email FROM users WHERE active = 1")
        # Returns: {"columns": [
        #   {"name": "id", "type": "int", "nullable": false, ...},
        #   {"name": "name", "type": "varchar", "nullable": false, ...},
        #   {"name": "email", "type": "varchar", "nullable": true, ...}
        # ]}
        
        # Analyze columns from a JOIN
        db_get_query_columns('''
            SELECT u.id, u.name, o.total 
            FROM users u 
            JOIN orders o ON u.id = o.user_id
        ''')
        # Returns column metadata including types and nullability
        
        # Works with aggregations and expressions
        db_get_query_columns("SELECT COUNT(*) as total, category FROM products GROUP BY category")
        # Returns: {"columns": [{"name": "total", ...}, {"name": "category", ...}]}
    
    Args:
        query: A SELECT query to analyze
        database: Database name (call db_list_databases() first, uses default if not specified)
        
    Returns:
        Dictionary with "columns" key containing list of column metadata dictionaries.
        Each column dict includes: name, type, nullable, precision, scale, etc.
    """
    validate_readonly_sql(query)
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        columns = backend.get_query_columns(query)
        return {"columns": columns}
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "columns": []}


@mcp.tool()
def db_sum_query_column(query: str, column: str, database: str | None = None) -> dict[str, Any]:
    """
    Sum a specific column from a SELECT query's results.
    
    This tool wraps your query to compute SUM(column) efficiently. Pass your base
    query; the tool handles the SUM aggregation.
    
    Use this tool to:
    - Compute totals from filtered results (e.g., revenue, transaction amounts)
    - Validate aggregate values match expectations
    - Compare totals across databases during migrations
    - Verify financial/transaction totals
    
    Examples:
        # Sum transaction amounts for 2024
        db_sum_query_column("SELECT amount FROM transactions WHERE year = 2024", "amount")
        # Returns: {"sum": 12345.67}
        
        # Sum with complex filtering
        db_sum_query_column(
            "SELECT price FROM products WHERE category = 'electronics' AND in_stock = 1",
            "price"
        )
        # Returns: {"sum": 98765.43}
        
        # Sum from a JOIN
        db_sum_query_column('''
            SELECT o.total 
            FROM orders o 
            JOIN users u ON o.user_id = u.id 
            WHERE u.region = 'west'
        ''', "total")
        # Returns: {"sum": 54321.00}
    
    Args:
        query: A SELECT query that returns rows with the column to sum
        column: Name of the column to sum (must exist in query results)
        database: Database name (call db_list_databases() first, uses default if not specified)
        
    Returns:
        Dictionary with "sum" key containing the sum value (numeric) or None if all values are NULL
    """
    validate_readonly_sql(query)
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        sum_val = backend.sum_query_column(query, column)
        return {"sum": sum_val}
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "sum": None}


@mcp.tool()
def db_measure_query(query: str, max_rows: int = 1000, database: str | None = None) -> dict[str, Any]:
    """
    Measure query execution time and retrieve limited rows for performance testing.
    
    This tool executes your query and measures how long it takes, stopping after
    fetching a specified number of rows. Useful for performance benchmarking.
    
    Use this tool to:
    - Measure query performance (execution time)
    - Test query speed before running full queries
    - Validate queries return expected row counts
    - Performance benchmarking and optimization
    
    Examples:
        # Measure a simple query
        db_measure_query("SELECT * FROM users WHERE active = 1")
        # Returns: {"execution_time_ms": 45.2, "row_count": 850, "hit_limit": false}
        
        # Measure with custom row limit
        db_measure_query("SELECT * FROM large_table", max_rows=100)
        # Returns: {"execution_time_ms": 123.5, "row_count": 100, "hit_limit": true}
        
        # Benchmark complex query
        db_measure_query('''
            SELECT u.name, COUNT(o.id) as order_count
            FROM users u
            LEFT JOIN orders o ON u.id = o.user_id
            GROUP BY u.id, u.name
        ''', max_rows=500)
    
    Args:
        query: SQL SELECT query to measure (must be SELECT, read-only)
        max_rows: Maximum number of rows to retrieve (default: 1000). Query stops after this limit.
        database: Database name (call db_list_databases() first, uses default if not specified)
        
    Returns:
        Dictionary with:
        - execution_time_ms: Query execution time in milliseconds
        - row_count: Number of rows actually retrieved
        - hit_limit: Boolean indicating if max_rows limit was reached
    """
    validate_readonly_sql(query)
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        result = backend.measure_query(query, max_rows)
        return result
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "execution_time_ms": None, "row_count": 0, "hit_limit": False}


@mcp.tool()
def db_preview(query: str, max_rows: int = 100, database: str | None = None) -> dict[str, Any]:
    """
    Sample N rows from a query result to preview actual data.
    
    **Requires data access permission** (set DB_MCP_ALLOW_DATA_ACCESS=true or DB_MCP_ALLOW_PREVIEW=true in .env).
    
    This tool executes your query and returns a limited number of actual data rows.
    Useful for spot-checking data quality and debugging.
    
    Use this tool to:
    - Preview query results before processing all data
    - Spot-check data values and quality
    - Validate data format and content
    - Debug data issues
    - Compare sample data across databases
    
    Examples:
        # Preview first 10 users
        db_preview("SELECT * FROM users WHERE active = 1 ORDER BY created_at DESC", max_rows=10)
        # Returns: {"rows": [{"id": 1, "name": "Alice", ...}, ...]}
        
        # Preview with specific columns
        db_preview("SELECT id, email, created_at FROM users WHERE role = 'admin'", max_rows=5)
        
        # Preview aggregated results
        db_preview("SELECT category, COUNT(*) as count FROM products GROUP BY category")
    
    Args:
        query: SQL SELECT query to preview (must be SELECT, read-only)
        max_rows: Maximum number of rows to return (default: 100)
        database: Database name (call db_list_databases() first, uses default if not specified)
        
    Returns:
        Dictionary with "rows" key containing list of row dictionaries.
        Each row is a dict mapping column names to values.
    """
    validate_readonly_sql(query)
    check_data_access("db_preview")  # Check permission
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        rows = backend.preview(query, max_rows)
        return {"rows": rows}
    except PermissionError:
        raise  # Re-raise permission errors
    except ValueError as e:
        # Re-raise ValueError from registry (includes available backends)
        raise
    except Exception as e:
        return {"error": str(e), "rows": []}


@mcp.tool()
def db_explain(query: str, database: str | None = None) -> dict[str, Any]:
    """
    Return database-native execution plan (EXPLAIN/EXPLAIN PLAN output).
    
    This tool analyzes how the database will execute your query, showing the
    execution strategy, index usage, and estimated costs.
    
    Use this tool to:
    - Analyze query performance characteristics
    - Identify missing indexes
    - Understand query execution strategy
    - Debug slow queries
    - Optimize query performance
    
    Examples:
        # Get execution plan for a simple query
        db_explain("SELECT * FROM users WHERE email = 'test@example.com'")
        # Returns: {"plan": "<execution plan XML/JSON>"}
        
        # Analyze JOIN performance
        db_explain('''
            SELECT u.name, o.total
            FROM users u
            JOIN orders o ON u.id = o.user_id
            WHERE o.status = 'completed'
        ''')
        
        # Check if index is used
        db_explain("SELECT * FROM products WHERE category = 'electronics' AND price > 100")
    
    Args:
        query: SQL SELECT query to explain (must be SELECT, read-only)
        database: Database name (call db_list_databases() first, uses default if not specified)
        
    Returns:
        Dictionary with "plan" key containing execution plan as string (format varies by database).
        For SQL Server: XML execution plan. For PostgreSQL: EXPLAIN output.
    """
    validate_readonly_sql(query)
    registry = get_registry()
    backend = registry.get(database)
    
    try:
        plan = backend.explain_query(query)
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
    
    **Perfect for migration validation!** This tool compares row counts, column schemas,
    and optionally sample data between two queries. Essential for verifying database
    migrations and query refactoring.
    
    Use this tool to:
    - Validate migrated queries produce matching results
    - Compare query performance and structure
    - Verify refactored queries maintain correctness
    - Test queries across dev/staging/prod environments
    - Compare Access queries to SQL Server equivalents
    
    Always call db_list_databases() first to discover available database names.
    
    Examples:
        # Compare queries in the same database
        db_compare_queries(
            "SELECT * FROM source_table",
            "SELECT * FROM target_table"
        )
        # Returns row count diff, column differences, type mismatches
        
        # Compare across databases (migration scenario)
        db_compare_queries(
            "SELECT * FROM customers WHERE active = 1",  # Access query
            "SELECT * FROM customers WHERE status = 'active'",  # SQL Server query
            database1="legacy",
            database2="new",
            compare_samples=True
        )
        
        # Verify refactored query
        db_compare_queries(
            "SELECT * FROM old_view",
            "SELECT * FROM new_view",
            compare_samples=False
        )
    
    Args:
        sql1: First SQL SELECT query to compare (must be SELECT, read-only)
        sql2: Second SQL SELECT query to compare (must be SELECT, read-only)
        compare_samples: If True, compare sample data (requires DB_MCP_ALLOW_DATA_ACCESS=true)
        database1: Database name for sql1 (call db_list_databases() first, uses default if not specified)
        database2: Database name for sql2 (call db_list_databases() first, uses database1 if not specified)
        
    Returns:
        Dictionary with:
        - row_count_diff: Difference in row counts (positive means sql2 has more)
        - row_count_1, row_count_2: Individual row counts
        - columns_missing_in_2, columns_missing_in_1: Column name differences
        - type_mismatches: List of columns with different types
        - database1, database2: Database names used
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
        count1 = backend1.count_query_results(sql1)
        count2 = backend2.count_query_results(sql2)
        row_count_diff = count2 - count1
        
        # Get column schemas
        cols1 = backend1.get_query_columns(sql1)
        cols2 = backend2.get_query_columns(sql2)
        
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
    
    This tool queries the database schema to discover all available tables,
    including row counts and other metadata.
    
    Use this tool to:
    - Explore the database schema
    - Discover available tables
    - Understand database structure
    - Find tables for queries
    
    Examples:
        # List all tables in default database
        db_list_tables()
        # Returns: {"tables": [{"name": "users", "schema": "dbo", "row_count": 1234}, ...]}
        
        # List tables in specific database
        db_list_tables(database="legacy")
        
        # Use with db_list_databases() to explore all databases
        databases = db_list_databases()
        for db in databases["databases"]:
            tables = db_list_tables(database=db["name"])
    
    Args:
        database: Database name (call db_list_databases() first, uses default if not specified)
    
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
    
    This tool queries the database schema to discover all views and their
    underlying SQL definitions.
    
    Use this tool to:
    - Discover available views
    - Understand view definitions and logic
    - Compare views across databases
    - Debug view-related issues
    
    Examples:
        # List all views in default database
        db_list_views()
        # Returns: {"views": [{"name": "active_users", "schema": "dbo", "definition": "SELECT ..."}, ...]}
        
        # List views in specific database
        db_list_views(database="new")
        
        # Compare views across databases
        legacy_views = db_list_views(database="legacy")
        new_views = db_list_views(database="new")
    
    Args:
        database: Database name (call db_list_databases() first, uses default if not specified)
    
    Returns:
        Dictionary with "views" key containing list of view metadata dictionaries.
        Each view dict includes: name, schema, definition (SQL), and other metadata.
        
    Note:
        For Access COM backend, list_views() returns query names without SQL definitions
        (SQL extraction is expensive). Use db_get_access_query_definition() to get SQL for specific queries.
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
def db_check_readonly_status(database: str | None = None) -> dict[str, Any]:
    """
    Verify that the database connection is read-only for safety confirmation.
    
    This tool checks database permissions to confirm the connection cannot
    perform write operations, providing safety verification before operations.
    
    Use this tool to:
    - Confirm database safety before operations
    - Validate read-only configuration
    - Check security settings
    - Verify permissions are correctly restricted
    
    Examples:
        # Check default database
        db_check_readonly_status()
        # Returns: {"readonly": true, "details": "✓ Read-only verification passed"}
        
        # Check specific database
        db_check_readonly_status(database="prod")
        
        # Verify all databases are read-only
        for db in db_list_databases()["databases"]:
            status = db_check_readonly_status(database=db["name"])
            print(f"{db['name']}: {status['readonly']}")
    
    Args:
        database: Database name (call db_list_databases() first, uses default if not specified)
    
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
def db_get_access_query_definition(name: str, database: str | None = None) -> dict[str, Any]:
    """
    Get Access query SQL definition by name (requires access_com backend).
    
    This tool retrieves the native SQL definition of a saved Access query by name.
    Only available with the access_com backend (not access_odbc).
    
    Use this tool to:
    - Retrieve native SQL from Access queries by name
    - Get query definitions for migration workflows
    - Understand Access query structure
    - Extract queries for conversion to other database systems
    
    **Note**: This tool requires the `access_com` backend (not `access_odbc`).
    Set DB_MCP_DATABASE=access_com to use this functionality.
    
    Examples:
        # Get a specific Access query by name
        db_get_access_query_definition("ActiveCustomers")
        # Returns: {"name": "ActiveCustomers", "sql": "SELECT * FROM Customers WHERE Active = True", "type": "Select"}
        
        # Get query for migration
        query = db_get_access_query_definition("MonthlyRevenue", database="legacy")
        # Use the SQL to create equivalent query in new database
        
        # List all queries then get specific ones
        views = db_list_views(database="legacy")
        for view in views["views"]:
            definition = db_get_access_query_definition(view["name"], database="legacy")
    
    Args:
        name: Name of the Access query to retrieve
        database: Database name (call db_list_databases() first, uses default if not specified)
    
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
            f"db_get_access_query_definition requires access_com backend, but database '{database or 'default'}' "
            f"uses {type(backend).__name__}. Set DB_MCP_DATABASE=access_com to use this feature."
        )
    
    try:
        query = backend.get_query_by_name(name)
        return query
    except ValueError as e:
        # Re-raise ValueError (query not found, wrong backend, etc.)
        raise
    except RuntimeError as e:
        # Re-raise RuntimeError (COM access issues, etc.)
        raise
    except Exception as e:
        # For other unexpected errors, provide detailed error info
        raise RuntimeError(
            f"Unexpected error retrieving query '{name}': {e}"
        ) from e


@mcp.tool()
def db_list_databases() -> dict[str, Any]:
    """
    List all available database backends that have been configured.
    
    **IMPORTANT: Always call this tool first** to discover available database names before using
    other tools. Database names are user-defined and configured via environment variables.
    
    This tool returns which databases are available in the current configuration,
    allowing you to discover and work with multiple databases simultaneously.
    
    Use this tool when:
    - Starting any database operation to see what databases are available
    - Working with multi-database configurations (migrations, testing, etc.)
    - You need to know which database is the default
    
    Examples:
        # List all configured databases
        db_list_databases()
        # Returns: {
        #   "databases": [
        #     {"name": "legacy", "is_default": true},
        #     {"name": "new", "is_default": false}
        #   ],
        #   "default": "legacy"
        # }
        
        # Use to iterate over all databases
        dbs = db_list_databases()
        for db in dbs["databases"]:
            tables = db_list_tables(database=db["name"])
            print(f"{db['name']} has {len(tables['tables'])} tables")
    
    Returns:
        Dictionary with "databases" key containing list of database names and default indicator.
        Each database entry has "name" and "is_default" fields.
        Also includes "default" key with the default database name.
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
