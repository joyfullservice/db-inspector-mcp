# db-inspector-mcp

A lightweight, extensible, cross-database MCP server (Model Context Protocol server) written in Python, designed to help AI coding assistants (e.g., Cursor, Claude Code, and other MCP-compatible tools) introspect, analyze, and verify SQL-based database systems.

## Features

- **Database Schema Discovery**: List tables and views with their definitions
- **Query Inspection**: Analyze query structure, columns, and metadata
- **Query Performance Measurement**: Measure execution time and row counts
- **Execution Plan Retrieval**: Get database-native execution plans
- **Data Sanity Checks**: Compare queries, validate aggregates, and spot-check data
- **Multi-Database Support**: Connect to multiple databases simultaneously (SQL Server, PostgreSQL, Access)
- **Cross-Database Comparison**: Compare queries across different database systems for migration validation
- **Multi-Database Support**: Connect to multiple databases simultaneously (SQL Server, PostgreSQL, Access)
- **Cross-Database Comparison**: Compare queries across different database systems for migration validation
- **Read-Only by Default**: Designed for safe introspection with explicit permission controls
- **Security Guardrails**: SQL validation prevents write operations

## Prerequisites

- **Python**: 3.10 or higher
- **Database Drivers**:
  - **SQL Server**: ODBC Driver 17 (or later) for SQL Server. Download from [Microsoft](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
  - **PostgreSQL**: No additional driver needed (uses `psycopg2-binary`)
  - **Microsoft Access**: Microsoft Access Database Engine (ACE) - usually pre-installed on Windows, or download from [Microsoft](https://www.microsoft.com/en-us/download/details.aspx?id=54920)

## Installation

### Basic Installation

```bash
# Clone the repository
git clone https://github.com/joyfullservice/db-inspector-mcp.git
cd db-inspector-mcp

# Create and activate a virtual environment (recommended)
python -m venv venv

# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install in development mode
pip install -e ".[dev]"
```

### Windows PATH Configuration

After installation, you may see warnings about scripts not being on PATH. If you need to run `db-inspector-mcp` from the command line, add the Python Scripts directory to your PATH:

**PowerShell (run as Administrator):**
```powershell
$scriptsPath = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\Scripts"
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$scriptsPath*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$scriptsPath", "User")
}
```

**Note:** The exact path may vary based on your Python installation. Check the warning message for the correct path, or find it with:
```powershell
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

After updating PATH, restart your terminal (or Cursor) for changes to take effect.

### Environment Configuration

Copy the example environment file and configure it:

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your database connection details
# See Configuration section below for details
```

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DB_BACKEND` | Database backend: `sqlserver`, `postgres`, or `access` | - | Yes* |
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

#### Microsoft Access

```bash
# Using ODBC connection string (for .accdb files)
DB_CONNECTION_STRING="Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\path\\to\\database.accdb;"

# For .mdb files
DB_CONNECTION_STRING="Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\path\\to\\database.mdb;"
```

### Multi-Database Configuration

The tool supports connecting to multiple databases simultaneously, which is especially useful for migration scenarios where you need to compare queries between a source database (e.g., Access) and a destination database (e.g., SQL Server).

#### Configuration Pattern

Use the pattern `DB_<name>_BACKEND` and `DB_<name>_CONNECTION_STRING` to configure multiple databases:

```bash
# Source database (Access)
DB_SOURCE_BACKEND=access
DB_SOURCE_CONNECTION_STRING="Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\path\\to\\source.accdb;"

# Destination database (SQL Server)
DB_DEST_BACKEND=sqlserver
DB_DEST_CONNECTION_STRING="Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password"
```

The first database configured (or one named "default") becomes the default backend. You can also use the legacy single-database configuration for backward compatibility:

```bash
# Legacy single-database configuration (registered as "default")
DB_BACKEND=sqlserver
DB_CONNECTION_STRING="Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password"
```

#### Using Multiple Databases in MCP

When multiple databases are configured, all tools accept an optional `database` parameter to specify which backend to use:

```python
# Query the source database
db_row_count("SELECT * FROM customers", database="source")

# Query the destination database
db_row_count("SELECT * FROM customers", database="dest")

# Compare queries across databases
db_compare_queries(
    "SELECT * FROM customers WHERE active = 1",
    "SELECT * FROM customers WHERE status = 'active'",
    database1="source",
    database2="dest"
)
```

## MCP Integration

### Cursor Integration

#### Single Database Configuration

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

#### Multi-Database Configuration (Migration Scenario)

For migration scenarios where you need to compare Access and SQL Server:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "db-inspector-mcp",
      "env": {
        "DB_SOURCE_BACKEND": "access",
        "DB_SOURCE_CONNECTION_STRING": "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\path\\to\\source.accdb;",
        "DB_DEST_BACKEND": "sqlserver",
        "DB_DEST_CONNECTION_STRING": "Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password",
        "DB_QUERY_TIMEOUT_SECONDS": "30",
        "DB_ALLOW_DATA_ACCESS": "true",
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

### Database Management

#### `db_list_databases()`

List all available database backends that have been configured.

**Example:**
```python
db_list_databases()
# Returns: {
#   "databases": [
#     {"name": "source", "is_default": True},
#     {"name": "dest", "is_default": False}
#   ],
#   "default": "source"
# }
```

### Query Analysis Tools

#### `db_row_count(sql: str, database: str | None = None)`

Return the number of rows an arbitrary SQL query would produce.

**Args:**
- `sql`: SQL SELECT query to count rows for
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
# Use default database
db_row_count("SELECT * FROM users WHERE active = 1")
# Returns: {"count": 1234}

# Use specific database
db_row_count("SELECT * FROM users WHERE active = 1", database="source")
```

#### `db_columns(sql: str, database: str | None = None)`

Return column names, data types, nullability, and precision/scale.

**Args:**
- `sql`: SQL SELECT query to get columns for
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_columns("SELECT * FROM users", database="source")
# Returns: {"columns": [{"name": "id", "type": "int", "nullable": false, ...}, ...]}
```

#### `db_sum_column(sql: str, column: str, database: str | None = None)`

Compute the SUM() of a single column for validation scenarios.

**Args:**
- `sql`: SQL SELECT query to sum a column from
- `column`: Column name to sum
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_sum_column("SELECT amount FROM transactions", "amount", database="source")
# Returns: {"sum": 12345.67}
```

#### `db_measure_query(sql: str, max_rows: int = 1000, database: str | None = None)`

Return execution time, number of rows retrieved, and whether row cap was hit.

**Args:**
- `sql`: SQL SELECT query to measure
- `max_rows`: Maximum number of rows to retrieve (default: 1000)
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_measure_query("SELECT * FROM large_table", max_rows=1000, database="source")
# Returns: {"execution_time_ms": 123.45, "row_count": 1000, "hit_limit": true}
```

#### `db_preview(sql: str, max_rows: int = 100, database: str | None = None)`

Sample N rows from a query result. **Requires data access permission** (`DB_ALLOW_DATA_ACCESS=true` or `DB_ALLOW_PREVIEW=true`).

**Args:**
- `sql`: SQL SELECT query to preview
- `max_rows`: Maximum number of rows to return (default: 100)
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_preview("SELECT * FROM users ORDER BY created_at DESC", max_rows=10, database="source")
# Returns: {"rows": [{"id": 1, "name": "Alice", ...}, ...]}
```

#### `db_explain(sql: str, database: str | None = None)`

Return database-native execution plan.

**Args:**
- `sql`: SQL SELECT query to explain
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_explain("SELECT * FROM users JOIN orders ON users.id = orders.user_id", database="dest")
# Returns: {"plan": "<execution plan XML/JSON>"}
```

### Comparison Tool

#### `db_compare_queries(sql1: str, sql2: str, compare_samples: bool = False, database1: str | None = None, database2: str | None = None)`

Compare two queries side-by-side, optionally from different databases. This is especially useful for migration scenarios where you want to compare a query from a source database (e.g., Access) with a query from a destination database (e.g., SQL Server) to ensure they produce matching results.

If `compare_samples=True`, requires data access permission.

**Args:**
- `sql1`: First SQL SELECT query to compare
- `sql2`: Second SQL SELECT query to compare
- `compare_samples`: If True, compare sample data (requires data access permission)
- `database1`: Name of the database backend for sql1 (optional, uses default if not specified)
- `database2`: Name of the database backend for sql2 (optional, uses database1 if not specified)

**Example:**
```python
# Compare queries in the same database
db_compare_queries(
    "SELECT * FROM source_table",
    "SELECT * FROM target_table",
    compare_samples=False
)

# Compare queries across different databases (migration scenario)
db_compare_queries(
    "SELECT * FROM customers WHERE active = 1",  # Access query
    "SELECT * FROM customers WHERE status = 'active'",  # SQL Server query
    database1="source",  # Access database
    database2="dest",    # SQL Server database
    compare_samples=True
)
# Returns: {
#   "row_count_diff": 0,
#   "row_count_1": 1234,
#   "row_count_2": 1234,
#   "columns_missing_in_2": [],
#   "columns_missing_in_1": [],
#   "type_mismatches": [],
#   "database1": "source",
#   "database2": "dest",
#   "sample_differences": {...}
# }
```

### Schema Introspection Tools

#### `db_list_tables(database: str | None = None)`

List all tables in the database with metadata.

**Args:**
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_list_tables(database="source")
# Returns: {"tables": [{"name": "users", "schema": "dbo", "row_count": 1234}, ...]}
```

#### `db_list_views(database: str | None = None)`

List all views in the database with their SQL definitions.

**Args:**
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_list_views(database="source")
# Returns: {"views": [{"name": "active_users", "schema": "dbo", "definition": "SELECT ..."}, ...]}
```

### Security Tool

#### `db_verify_readonly(database: str | None = None)`

Verify that the database connection is read-only. Can be called by agents to confirm safety.

**Args:**
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_verify_readonly(database="source")
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

### Database Migration Validation

When migrating queries from one database system to another (e.g., Access to SQL Server), use multi-database support to validate that the migrated query produces matching results:

```python
# 1. Get schema information from source database
source_columns = db_columns("SELECT * FROM customers WHERE active = 1", database="source")
source_count = db_row_count("SELECT * FROM customers WHERE active = 1", database="source")
source_sum = db_sum_column("SELECT * FROM customers WHERE active = 1", "revenue", database="source")

# 2. Build and test query on destination database
dest_columns = db_columns("SELECT * FROM customers WHERE status = 'active'", database="dest")

# 3. Compare queries across databases
comparison = db_compare_queries(
    "SELECT * FROM customers WHERE active = 1",  # Access query
    "SELECT * FROM customers WHERE status = 'active'",  # SQL Server query
    database1="source",
    database2="dest",
    compare_samples=True
)

# 4. Iterate until row counts, columns, and samples match
if comparison["row_count_diff"] == 0 and not comparison["type_mismatches"]:
    print("Migration successful! Queries produce matching results.")
```

### Data Conversion Validation

When migrating data between systems, use `db_compare_queries` to validate:

```python
# Compare row counts across databases
db_compare_queries(
    "SELECT * FROM source",
    "SELECT * FROM target",
    database1="source",
    database2="dest"
)

# Validate aggregates
db_sum_column("SELECT amount FROM transactions", "amount", database="source")
db_sum_column("SELECT amount FROM transactions", "amount", database="dest")

# Spot-check samples (requires permission)
db_preview("SELECT * FROM source ORDER BY id", max_rows=10, database="source")
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

### Complete Setup

1. **Clone and navigate to the repository:**
   ```bash
   git clone https://github.com/joyfullservice/db-inspector-mcp.git
   cd db-inspector-mcp
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   
   # Windows:
   venv\Scripts\activate
   # macOS/Linux:
   source venv/bin/activate
   ```

3. **Install the package with development dependencies:**
   ```bash
   pip install -e ".[dev]"
   ```

4. **Set up environment variables:**
   ```bash
   # Copy the example file
   cp .env.example .env
   
   # Edit .env with your database connection details
   # Required: DB_BACKEND and DB_CONNECTION_STRING
   ```

5. **Verify installation:**
   ```bash
   # Check that the command is available
   db-inspector-mcp --help
   
   # Or test with Python
   python -c "from db_inspector_mcp import main; print('Import successful')"
   ```

6. **Run tests:**
   ```bash
   # Run all tests
   pytest
   
   # Run with coverage report
   pytest --cov=db_inspector_mcp --cov-report=html
   
   # Run specific test file
   pytest tests/test_backends.py
   ```

### Testing Database Connections

Before running the MCP server, verify your database connection:

```bash
# Set environment variables (or use .env file)
$env:DB_BACKEND = "sqlserver"  # or "postgres"
$env:DB_CONNECTION_STRING = "your-connection-string"

# Test the connection (will show configuration error if connection string is missing)
db-inspector-mcp
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

