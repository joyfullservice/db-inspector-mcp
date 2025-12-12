# db-inspector-mcp

A lightweight, extensible, cross-database MCP server (Model Context Protocol server) written in Python, designed to help AI coding assistants (e.g., Cursor, Claude Code, and other MCP-compatible tools) introspect, analyze, and verify SQL-based database systems.

## Features

- **Database Schema Discovery**: List tables and views with their definitions
- **Query Inspection**: Analyze query structure, columns, and metadata
- **Query Performance Measurement**: Measure execution time and row counts
- **Execution Plan Retrieval**: Get database-native execution plans
- **Data Sanity Checks**: Compare queries, validate aggregates, and spot-check data
- **Multi-Database Support**: SQL Server and PostgreSQL (with more planned)
- **Read-Only by Default**: Designed for safe introspection with explicit permission controls
- **Security Guardrails**: SQL validation prevents write operations

## Installation

```bash
# Clone the repository
git clone https://github.com/joyfullservice/db-inspector-mcp.git
cd db-inspector-mcp

# Install in development mode
pip install -e .

# Or install with dev dependencies for testing
pip install -e ".[dev]"
```

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DB_BACKEND` | Database backend: `sqlserver` or `postgres` | - | Yes |
| `DB_CONNECTION_STRING` | Database connection string | - | Yes |
| `DB_QUERY_TIMEOUT_SECONDS` | Query timeout in seconds | `30` | No |
| `DB_ALLOW_DATA_ACCESS` | Global flag to enable data access tools | `false` | No |
| `DB_ALLOW_PREVIEW` | Per-tool override for `db_preview` | `false` | No |
| `DB_VERIFY_READONLY` | Verify read-only at startup | `true` | No |
| `DB_READONLY_FAIL_ON_WRITE` | Fail startup if write permissions detected | `false` | No |

### Connection Strings

#### SQL Server

```bash
# Using ODBC connection string
DB_CONNECTION_STRING="Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password"

# Or using DSN
DB_CONNECTION_STRING="DSN=MySQLServerDSN"
```

#### PostgreSQL

```bash
# Using connection string format
DB_CONNECTION_STRING="dbname=mydb user=postgres password=secret host=localhost port=5432"
```

## MCP Integration

### Cursor Integration

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "db-inspector-mcp",
      "env": {
        "DB_BACKEND": "sqlserver",
        "DB_CONNECTION_STRING": "Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password",
        "DB_QUERY_TIMEOUT_SECONDS": "30",
        "DB_ALLOW_DATA_ACCESS": "false",
        "DB_VERIFY_READONLY": "true"
      }
    }
  }
}
```

### Claude Code Integration

Add to your Claude Code MCP configuration (similar format):

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "db-inspector-mcp",
      "env": {
        "DB_BACKEND": "postgres",
        "DB_CONNECTION_STRING": "dbname=mydb user=postgres password=secret host=localhost port=5432",
        "DB_ALLOW_DATA_ACCESS": "false"
      }
    }
  }
}
```

## Available Tools

### Query Analysis Tools

#### `db_row_count(sql: str)`

Return the number of rows an arbitrary SQL query would produce.

**Example:**
```python
db_row_count("SELECT * FROM users WHERE active = 1")
# Returns: {"count": 1234}
```

#### `db_columns(sql: str)`

Return column names, data types, nullability, and precision/scale.

**Example:**
```python
db_columns("SELECT * FROM users")
# Returns: {"columns": [{"name": "id", "type": "int", "nullable": false, ...}, ...]}
```

#### `db_sum_column(sql: str, column: str)`

Compute the SUM() of a single column for validation scenarios.

**Example:**
```python
db_sum_column("SELECT amount FROM transactions", "amount")
# Returns: {"sum": 12345.67}
```

#### `db_measure_query(sql: str, max_rows: int = 1000)`

Return execution time, number of rows retrieved, and whether row cap was hit.

**Example:**
```python
db_measure_query("SELECT * FROM large_table", max_rows=1000)
# Returns: {"execution_time_ms": 123.45, "row_count": 1000, "hit_limit": true}
```

#### `db_preview(sql: str, max_rows: int = 100)`

Sample N rows from a query result. **Requires data access permission** (`DB_ALLOW_DATA_ACCESS=true` or `DB_ALLOW_PREVIEW=true`).

**Example:**
```python
db_preview("SELECT * FROM users ORDER BY created_at DESC", max_rows=10)
# Returns: {"rows": [{"id": 1, "name": "Alice", ...}, ...]}
```

#### `db_explain(sql: str)`

Return database-native execution plan.

**Example:**
```python
db_explain("SELECT * FROM users JOIN orders ON users.id = orders.user_id")
# Returns: {"plan": "<execution plan XML/JSON>"}
```

### Comparison Tool

#### `db_compare_queries(sql1: str, sql2: str, compare_samples: bool = False)`

Compare two queries side-by-side. If `compare_samples=True`, requires data access permission.

**Example:**
```python
db_compare_queries(
    "SELECT * FROM source_table",
    "SELECT * FROM target_table",
    compare_samples=False
)
# Returns: {
#   "row_count_diff": 0,
#   "columns_missing_in_2": [],
#   "type_mismatches": []
# }
```

### Schema Introspection Tools

#### `db_list_tables()`

List all tables in the database with metadata.

**Example:**
```python
db_list_tables()
# Returns: {"tables": [{"name": "users", "schema": "dbo", "row_count": 1234}, ...]}
```

#### `db_list_views()`

List all views in the database with their SQL definitions.

**Example:**
```python
db_list_views()
# Returns: {"views": [{"name": "active_users", "schema": "dbo", "definition": "SELECT ..."}, ...]}
```

### Security Tool

#### `db_verify_readonly()`

Verify that the database connection is read-only. Can be called by agents to confirm safety.

**Example:**
```python
db_verify_readonly()
# Returns: {"readonly": true, "details": "✓ Read-only verification passed"}
```

## Security Model

### Read-Only by Default

This tool is designed to be **read-only by default**. All SQL queries are validated to reject write operations (INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, etc.).

### Data Access Permissions

Some tools require explicit authorization to access actual data:

- **`db_preview`**: Requires `DB_ALLOW_DATA_ACCESS=true` or `DB_ALLOW_PREVIEW=true`
- **`db_compare_queries` with `compare_samples=True`**: Requires data access permission

Metadata tools (row counts, column schemas, execution plans) are always available without special permissions.

### Read-Only Verification

At startup, the tool verifies that the database connection is read-only (if `DB_VERIFY_READONLY=true`). This checks:

- Ability to create temp tables (expected for read-only users)
- Role membership (SQL Server) or privileges (PostgreSQL)
- Superuser status (PostgreSQL)

If write permissions are detected:
- A warning is logged to stderr
- If `DB_READONLY_FAIL_ON_WRITE=true`, the server exits with an error
- Otherwise, the server continues with a warning

## Use Cases

### Data Conversion Validation

When migrating data between systems, use `db_compare_queries` to validate:

```python
# Compare row counts
db_compare_queries("SELECT * FROM source", "SELECT * FROM target")

# Validate aggregates
db_sum_column("SELECT amount FROM source", "amount")
db_sum_column("SELECT amount FROM target", "amount")

# Spot-check samples (requires permission)
db_preview("SELECT * FROM source ORDER BY id", max_rows=10)
```

### Breaking Change Detection

When modifying views or stored procedures:

```python
# List all views
db_list_views()

# Compare column schemas
db_columns("SELECT * FROM old_view")
db_columns("SELECT * FROM new_view")

# Check execution plans
db_explain("SELECT * FROM modified_view")
```

### Performance Tuning

```python
# Measure query performance
db_measure_query("SELECT * FROM large_table WHERE condition = 'value'")

# Get execution plan
db_explain("SELECT * FROM large_table WHERE condition = 'value'")
```

## Development

### Setup

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=db_inspector_mcp --cov-report=html
```

### Project Structure

```
db-inspector-mcp/
├── src/
│   └── db_inspector_mcp/
│       ├── __init__.py
│       ├── main.py          # MCP server entry point
│       ├── tools.py          # MCP tool definitions
│       ├── config.py         # Configuration management
│       ├── security.py       # SQL validation and permissions
│       └── backends/
│           ├── base.py       # Abstract base class
│           ├── mssql.py       # SQL Server implementation
│           └── postgres.py    # PostgreSQL implementation
└── tests/                    # Test suite
```

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## Planned Enhancements

- MySQL / MariaDB backend support
- SQLite backend support
- Schema resources (list tables, views, indexes) as MCP Resources
- Query linting / formatting
- Caching layer for performance
- Index recommendation assistant
- Access migration helpers

