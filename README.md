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
- **Read-Only by Default**: Designed for safe introspection with explicit permission controls
- **Security Guardrails**: SQL validation prevents write operations

## Prerequisites

- **Python**: 3.10 or higher
- **Database Drivers**:
  - **SQL Server**: ODBC Driver 17 (or later) for SQL Server. Download from [Microsoft](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
  - **PostgreSQL**: No additional driver needed (uses `psycopg2-binary`)
  - **Microsoft Access**: Microsoft Access Database Engine (ACE) - usually pre-installed on Windows, or download from [Microsoft](https://www.microsoft.com/en-us/download/details.aspx?id=54920)
    - **32-bit Access compatibility**: If you need to connect to 32-bit versions of Microsoft Access, you must install a 32-bit version of Python so that the ODBC drivers are compatible. Note that some databases like PostgreSQL may not have 32-bit ODBC drivers available.

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

# Install in development mode (editable install)
pip install -e ".[dev]"
```

**Note:** The `-e` flag installs the package in "editable" mode, which means changes to the source code are immediately reflected without needing to reinstall. This is recommended for development. See the [Building and Installing After Making Changes](#building-and-installing-after-making-changes) section for more details.

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

## Quick Start

Get up and running with db-inspector-mcp in Cursor in just a few steps:

### Step 1: Install the Package

Make sure the package is installed (if you haven't already):

```bash
pip install -e ".[dev]"
```

Verify the command is available:

```bash
db-inspector-mcp --help
```

### Step 2: Configure Cursor

The MCP configuration file (`.cursor/mcp.json`) is already created in this repository. It contains the basic settings needed to connect the MCP server to Cursor.

**Note:** If you're using this in a different project, create `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "db-inspector-mcp",
      "env": {
        "DB_MCP_QUERY_TIMEOUT_SECONDS": "30",
        "DB_MCP_ALLOW_DATA_ACCESS": "false",
        "DB_MCP_VERIFY_READONLY": "true"
      }
    }
  }
}
```

### Step 3: Set Up Your Database Connection

Create a `.env` file in your project root with your database connection details:

**For SQL Server:**
```bash
DB_MCP_DATABASE=sqlserver
DB_MCP_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password
```

**For PostgreSQL:**
```bash
DB_MCP_DATABASE=postgres
DB_MCP_CONNECTION_STRING=dbname=mydb user=postgres password=secret host=localhost port=5432
```

**For Microsoft Access:**

Two backends are available:

- **`access_odbc`**: Standard SQL queries via ODBC (works without Access installed)
- **`access_com`**: Query-by-name and native SQL extraction via COM (requires Access installed)

```bash
# ODBC backend (standard SQL queries)
DB_MCP_DATABASE=access_odbc
DB_MCP_CONNECTION_STRING=Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\path\\to\\database.accdb;

# COM backend (query-by-name, requires Access installed)
DB_MCP_DATABASE=access_com
DB_MCP_CONNECTION_STRING=Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\path\\to\\database.accdb;

# Note: Both backends also support .accda files (Access Database Executable)
# Example: DBQ=C:\\path\\to\\database.accda;
```

See the [Connection Strings](#connection-strings) section below for more details.

### Step 4: Restart Cursor

After creating the `.env` file:
1. **Close Cursor completely** (not just the window - fully quit the application)
2. **Reopen Cursor** in your project directory
3. Cursor will automatically detect and load the MCP server

### Step 5: Test the Tool

Once Cursor restarts, the MCP tools will be available. You can test them by asking the AI assistant in Cursor to:

- **List available databases:**
  > "Can you list the available databases using db_list_databases?"

- **Explore your database schema:**
  > "What tables are in the database? Use db_list_tables"

- **Query row counts:**
  > "How many rows are in the users table? Use db_count_query_results with a SELECT query"

- **Get column information:**
  > "What are the columns in the users table? Use db_get_query_columns"

- **Verify read-only status:**
  > "Verify the database is read-only using db_check_readonly_status"

### Troubleshooting

If the MCP server doesn't load:

1. **Check MCP logs in Cursor:**
   - Open Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`)
   - Search for "MCP" or check the Output panel for MCP-related messages

2. **Verify the command is available:**
   - Run `db-inspector-mcp --help` in your terminal
   - If it's not found, make sure the package is installed and the virtual environment is activated

3. **Test the connection manually:**
   ```powershell
   # Set environment variables
   $env:DB_MCP_DATABASE = "sqlserver"
   $env:DB_MCP_CONNECTION_STRING = "your-connection-string"
   
   # Test the server (should show initialization messages)
   db-inspector-mcp
   ```

4. **Check your `.env` file:**
   - Make sure it's in the project root (same directory as `.cursor/mcp.json`)
   - Verify the connection string format matches your database type
   - Ensure there are no syntax errors

5. **Alternative command format:**
   If `db-inspector-mcp` isn't in your PATH, you can update `.cursor/mcp.json` to use Python directly:
   ```json
   {
     "mcpServers": {
       "db-inspector-mcp": {
         "command": "python",
         "args": ["-m", "db_inspector_mcp.main"],
         "env": {
           "DB_MCP_QUERY_TIMEOUT_SECONDS": "30",
           "DB_MCP_ALLOW_DATA_ACCESS": "false",
           "DB_MCP_VERIFY_READONLY": "true"
         }
       }
     }
   }
   ```

For more advanced configuration options, see the [Configuration](#configuration) section below.

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DB_MCP_DATABASE` | Database type: `sqlserver`, `postgres`, `access_odbc`, or `access_com` (single database) | - | Yes* |
| `DB_MCP_CONNECTION_STRING` | Database connection string (single database) | - | Yes* |
| `DB_MCP_<name>_DATABASE` | Database type for named database (multi-database) | - | Yes* |
| `DB_MCP_<name>_CONNECTION_STRING` | Connection string for named database (multi-database) | - | Yes* |
| `DB_MCP_PROJECT_DIR` | Project directory for `.env` file lookup (see [User-Level MCP Configuration](#user-level-mcp-configuration)) | auto-detected | No |
| `DB_MCP_QUERY_TIMEOUT_SECONDS` | Query timeout in seconds | `30` | No |
| `DB_MCP_ALLOW_DATA_ACCESS` | Global flag to enable data access tools | `false` | No |
| `DB_MCP_ALLOW_PREVIEW` | Per-tool override for `db_preview` | `false` | No |
| `DB_MCP_VERIFY_READONLY` | Verify read-only at startup | `true` | No |
| `DB_MCP_READONLY_FAIL_ON_WRITE` | Fail startup if write permissions detected | `false` | No |

*Required for single-database configuration (`DB_MCP_DATABASE`/`DB_MCP_CONNECTION_STRING`) or multi-database configuration (`DB_MCP_<name>_DATABASE`/`DB_MCP_<name>_CONNECTION_STRING`).

### Connection Strings

#### SQL Server

```bash
# Using ODBC connection string
DB_MCP_CONNECTION_STRING="Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password"

# Or using DSN
DB_MCP_CONNECTION_STRING="DSN=MySQLServerDSN"
```

#### PostgreSQL

```bash
# Using connection string format
DB_MCP_CONNECTION_STRING="dbname=mydb user=postgres password=secret host=localhost port=5432"
```

#### Microsoft Access

Two backends are available:

**`access_odbc`** - Standard SQL queries (works without Access installed):
```bash
DB_MCP_DATABASE=access_odbc
DB_MCP_CONNECTION_STRING="Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\path\\to\\database.accdb;"
```

**`access_com`** - Query-by-name and native SQL extraction (requires Access installed):
```bash
DB_MCP_DATABASE=access_com
DB_MCP_CONNECTION_STRING="Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\path\\to\\database.accdb;"
```

**Note:** Both backends support `.accdb`, `.accda` (Access Database Executable), and `.mdb` file formats. The driver name in the connection string remains the same regardless of file extension.

**Relative paths:** The `DBQ=` path can be relative — it will be resolved against the directory containing the `.env` file. This makes configurations portable across machines:
```bash
# Relative to .env location (resolved at startup)
DB_MCP_CONNECTION_STRING="Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=.\database.accdb;"
# Or just provide the filename directly
DB_MCP_CONNECTION_STRING=database.accdb
```

**Important - 32-bit Access compatibility**: If you need to connect to 32-bit versions of Microsoft Access, you must install a 32-bit version of Python so that the ODBC drivers are compatible. Note that some databases like PostgreSQL may not have 32-bit ODBC drivers available.

Use `access_odbc` for standard SQL operations. Use `access_com` when you need to retrieve Access queries by name (see `db_get_access_query` tool).

### Multi-Database Configuration

The tool supports connecting to multiple databases simultaneously, which is useful for migration scenarios, refactoring validation, testing, and feature development.

#### Configuration Pattern

Use the pattern `DB_MCP_<name>_DATABASE` and `DB_MCP_<name>_CONNECTION_STRING` to configure multiple databases:

```bash
# Example: Migration scenario (Access to SQL Server)
DB_MCP_LEGACY_DATABASE=access_com  # Use COM backend for query-by-name
DB_MCP_LEGACY_CONNECTION_STRING="Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\path\\to\\legacy.accdb;"
# Note: Also supports .accda and .mdb files

DB_MCP_NEW_DATABASE=sqlserver
DB_MCP_NEW_CONNECTION_STRING="Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password"
```

You can name databases according to your use case:
- **Migration**: `legacy`/`new`, `old`/`refactored`
- **Testing**: `prod`/`dev`, `staging`/`test`
- **Versioning**: `v1`/`v2`, `before`/`after`

The first database configured (or one named "default") becomes the default database.

#### Using Multiple Databases in MCP

When multiple databases are configured, all tools accept an optional `database` parameter to specify which database to use:

```python
# Query the legacy database
db_count_query_results("SELECT * FROM customers", database="legacy")

# Query the new database
db_count_query_results("SELECT * FROM customers", database="new")

# Compare queries across databases
db_compare_queries(
    "SELECT * FROM customers WHERE active = 1",
    "SELECT * FROM customers WHERE status = 'active'",
    database1="legacy",
    database2="new"
)
```

**Important**: Call `db_list_databases()` first to discover available database names.

## Project-Specific Configuration

This tool supports per-project database configurations, similar to how git works - same commands, different repositories per project. This enables a hybrid approach where non-sensitive settings are in version-controlled `.cursor/mcp.json` and credentials are in gitignored `.env` files.

### Why `DB_MCP_` Prefix?

All environment variables use the `DB_MCP_` prefix to avoid collisions with other database-related variables (like `DB_HOST`, `DB_NAME`, etc.) that might already exist in your project's `.env` file. This allows the tool to coexist peacefully with other database configurations.

### Why `_DATABASE` Suffix?

The `_DATABASE` suffix (instead of `_BACKEND`) makes it clearer that you're configuring a database connection, not an internal backend component.

### Configuration Pattern

#### `.cursor/mcp.json` (Version Controlled)

Store non-sensitive settings that can be shared with your team:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "db-inspector-mcp",
      "env": {
        "DB_MCP_QUERY_TIMEOUT_SECONDS": "30",
        "DB_MCP_ALLOW_DATA_ACCESS": "false",
        "DB_MCP_VERIFY_READONLY": "true"
      }
    }
  }
}
```

#### `.env` (Gitignored, Project-Specific)

Store sensitive credentials and project-specific database connections:

```bash
# Single database configuration
DB_MCP_DATABASE=sqlserver
DB_MCP_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password

# Or multi-database configuration
DB_MCP_LEGACY_DATABASE=access_com  # Use COM backend for query-by-name
DB_MCP_LEGACY_CONNECTION_STRING=Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\path\to\legacy.accdb;
# Note: Also supports .accda and .mdb files
# Note: Also supports .accda and .mdb files

DB_MCP_NEW_DATABASE=sqlserver
DB_MCP_NEW_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password
```

#### `.env.local` (Optional, Gitignored)

For personal overrides that shouldn't affect the team:

```bash
# Override timeout for local development
DB_MCP_QUERY_TIMEOUT_SECONDS=60
```

### Environment Variable Precedence

1. **MCP server `env` section** (highest priority) - values in `.cursor/mcp.json` take precedence
2. **`.env.local`** - personal overrides
3. **`.env`** - project-specific configuration (lowest priority)

This allows `.cursor/mcp.json` to override `.env` values when needed.

### Use Case Examples

#### Migration Scenario

```bash
# .env
DB_MCP_LEGACY_DATABASE=access_com  # Use COM backend for query-by-name
DB_MCP_LEGACY_CONNECTION_STRING=Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\path\to\legacy.accdb;
# Note: Also supports .accda and .mdb files
# Note: Also supports .accda and .mdb files

DB_MCP_NEW_DATABASE=sqlserver
DB_MCP_NEW_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password
```

#### Refactoring Validation

```bash
# .env - same database, different queries
DB_MCP_PROD_DATABASE=sqlserver
DB_MCP_PROD_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=prod;Database=mydb;UID=user;PWD=password
```

Use `db_compare_queries()` to compare old and new query versions on the same database.

#### Multi-Environment Testing

```bash
# .env
DB_MCP_DEV_DATABASE=sqlserver
DB_MCP_DEV_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=dev;Database=mydb;UID=user;PWD=password

DB_MCP_STAGING_DATABASE=sqlserver
DB_MCP_STAGING_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=staging;Database=mydb;UID=user;PWD=password

DB_MCP_PROD_DATABASE=sqlserver
DB_MCP_PROD_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=prod;Database=mydb;UID=user;PWD=password
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
        "DB_MCP_QUERY_TIMEOUT_SECONDS": "30",
        "DB_MCP_ALLOW_DATA_ACCESS": "false",
        "DB_MCP_VERIFY_READONLY": "true"
      }
    }
  }
}
```

Then add credentials to `.env`:

```bash
DB_MCP_DATABASE=sqlserver
DB_MCP_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password
```

#### Multi-Database Configuration

For migration scenarios where you need to compare Access and SQL Server:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "db-inspector-mcp",
      "env": {
        "DB_MCP_QUERY_TIMEOUT_SECONDS": "30",
        "DB_MCP_ALLOW_DATA_ACCESS": "true",
        "DB_MCP_VERIFY_READONLY": "true"
      }
    }
  }
}
```

Then add database connections to `.env`:

```bash
DB_MCP_LEGACY_DATABASE=access_com  # Use COM backend for query-by-name
DB_MCP_LEGACY_CONNECTION_STRING=Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\path\to\legacy.accdb;
# Note: Also supports .accda and .mdb files

DB_MCP_NEW_DATABASE=sqlserver
DB_MCP_NEW_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password
```

### User-Level MCP Configuration

You don't need a per-project `.cursor/mcp.json`. You can configure the MCP server once at the **user level** (global Cursor settings) and rely on a `.env` file in each project for database credentials.

The server uses two strategies to find your project's `.env` file:

1. **Startup** — searches upward from the working directory for `.env`, `.cursor/mcp.json`, or `pyproject.toml`.
2. **First tool call** — if no `.env` was found at startup, the server asks the client (Cursor) for its workspace roots via the MCP protocol and loads `.env` from there.

This means it works automatically even when the working directory is *not* your project root (which is the typical case for user-level MCP configs).

Diagnostic messages are printed to stderr so you can verify what happened in Cursor's MCP output pane:

```
Working directory: C:\Users\me
No .env file found at C:\Users\me\.env
No database configuration found at startup — will attempt workspace detection on first tool call.
Lazy init: loading .env from workspace root C:\Users\me\projects\my-project
Loaded .env from C:\Users\me\projects\my-project\.env
Initialized 2 backend(s) from workspace root: legacy, new
```

#### Fallback: `DB_MCP_PROJECT_DIR`

If the automatic workspace detection doesn't work in your environment, set `DB_MCP_PROJECT_DIR` explicitly:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "db-inspector-mcp",
      "env": {
        "DB_MCP_PROJECT_DIR": "C:\\Users\\me\\projects\\my-project",
        "DB_MCP_QUERY_TIMEOUT_SECONDS": "30",
        "DB_MCP_ALLOW_DATA_ACCESS": "false"
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
        "DB_MCP_QUERY_TIMEOUT_SECONDS": "30",
        "DB_MCP_ALLOW_DATA_ACCESS": "false"
      }
    }
  }
}
```

Then add credentials to `.env`:

```bash
DB_MCP_DATABASE=postgres
DB_MCP_CONNECTION_STRING=dbname=mydb user=postgres password=secret host=localhost port=5432
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

#### `db_count_query_results(query: str, database: str | None = None)`

Count the number of rows a SELECT query returns.

This tool wraps your query in `SELECT COUNT(*) FROM (your_query)` to efficiently count results without fetching all data.

**Args:**
- `query`: A SELECT query to count results from
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
# Use default database
db_count_query_results("SELECT * FROM users WHERE active = 1")
# Returns: {"count": 1234}

# Use specific database
db_count_query_results("SELECT * FROM users WHERE active = 1", database="source")
```

#### `db_get_query_columns(query: str, database: str | None = None)`

Analyze the column schema of a SELECT query's results.

This tool executes your query with a limit to fetch 0 rows, inspecting column metadata without retrieving data.

**Args:**
- `query`: A SELECT query to analyze
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_get_query_columns("SELECT * FROM users", database="source")
# Returns: {"columns": [{"name": "id", "type": "int", "nullable": false, ...}, ...]}
```

#### `db_sum_query_column(query: str, column: str, database: str | None = None)`

Sum a specific column from a SELECT query's results.

This tool wraps your query to compute `SUM(column)` efficiently.

**Args:**
- `query`: A SELECT query that returns rows with the column to sum
- `column`: Name of the column to sum
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_sum_query_column("SELECT amount FROM transactions", "amount", database="source")
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

**Note for Access COM backend**: `list_views()` returns query names without SQL (SQL extraction is expensive). Use `db_get_access_query_definition()` to get SQL for specific queries.

#### `db_get_access_query_definition(name: str, database: str | None = None)`

Get Access query SQL definition by name. **Requires `access_com` backend** (not `access_odbc`).

**Args:**
- `name`: Name of the Access query to retrieve
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
# Get Access query by name (requires access_com backend)
query = db_get_access_query_definition("ActiveCustomers", database="legacy")
# Returns: {"name": "ActiveCustomers", "sql": "SELECT * FROM Customers WHERE Active = True", "type": "Select"}
```

**Note**: This tool requires the `access_com` backend. Set `DB_MCP_DATABASE=access_com` to use this functionality.

### Security Tool

#### `db_check_readonly_status(database: str | None = None)`

Verify that the database connection is read-only for safety confirmation.

**Args:**
- `database`: Name of the database backend to use (optional, uses default if not specified)

**Example:**
```python
db_check_readonly_status(database="source")
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
# 1. Discover available databases
databases = db_list_databases()
# Returns: {"databases": [{"name": "legacy", "is_default": True}, {"name": "new", "is_default": False}]}

# 2. Get schema information from legacy database
legacy_columns = db_get_query_columns("SELECT * FROM customers WHERE active = 1", database="legacy")
legacy_count = db_count_query_results("SELECT * FROM customers WHERE active = 1", database="legacy")
legacy_sum = db_sum_query_column("SELECT * FROM customers WHERE active = 1", "revenue", database="legacy")

# 3. Build and test query on new database
new_columns = db_get_query_columns("SELECT * FROM customers WHERE status = 'active'", database="new")

# 4. Compare queries across databases
comparison = db_compare_queries(
    "SELECT * FROM customers WHERE active = 1",  # Access query
    "SELECT * FROM customers WHERE status = 'active'",  # SQL Server query
    database1="legacy",
    database2="new",
    compare_samples=True
)

# 5. Iterate until row counts, columns, and samples match
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
db_sum_query_column("SELECT amount FROM transactions", "amount", database="legacy")
db_sum_query_column("SELECT amount FROM transactions", "amount", database="new")

# Spot-check samples (requires permission)
db_preview("SELECT * FROM transactions ORDER BY id", max_rows=10, database="legacy")
```

### Breaking Change Detection

When modifying views or stored procedures:

```python
# List all views
db_list_views()

# Compare column schemas
db_get_query_columns("SELECT * FROM old_view")
db_get_query_columns("SELECT * FROM new_view")

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

For architectural decisions and design rationale, see [DECISIONS.md](DECISIONS.md). AI agents should also see [AGENTS.md](AGENTS.md) for project conventions.

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

### Building and Installing After Making Changes

After modifying the source code, you need to rebuild and reinstall the package. The method depends on how it was originally installed:

#### If Installed in Editable Mode (Recommended for Development)

If you installed with `pip install -e ".[dev]"` (editable mode), **changes are automatically reflected** - no rebuild needed! Just:

1. Make your code changes
2. Test immediately - the changes are already active
3. Restart Cursor (if using the MCP server) to pick up changes

**Note:** Editable mode links directly to your source code, so changes take effect immediately without reinstalling.

#### If You Need to Reinstall

If you need to reinstall (e.g., after adding new dependencies or changing package metadata):

```bash
# Reinstall in editable mode with dev dependencies
pip install -e ".[dev]"

# Or reinstall without dev dependencies
pip install -e .
```

#### Building Distribution Packages

To create installable distribution packages (wheel or source distribution):

```bash
# Install build tool (if not already installed)
pip install build

# Build both wheel and source distribution
python -m build

# Or build just a wheel
python -m build --wheel

# Or build just a source distribution
python -m build --sdist
```

This creates packages in the `dist/` directory that can be installed with `pip install dist/db_inspector_mcp-*.whl`.

#### Verifying the Build

After building or reinstalling, verify it works:

```bash
# Check the command is available
db-inspector-mcp --help

# Or test the import
python -c "from db_inspector_mcp import main; print('Import successful')"
```

#### Quick Reference

| Scenario | Command | When Changes Take Effect |
|----------|---------|-------------------------|
| Development (editable) | `pip install -e ".[dev]"` | Immediately (no rebuild needed) |
| Reinstall after metadata changes | `pip install -e ".[dev]"` | After reinstall |
| Build distribution | `python -m build` | After installing the built package |
| Standard install | `pip install .` | After reinstall (not recommended for development) |

### Testing Database Connections

Before running the MCP server, verify your database connection:

```bash
# Set environment variables (or use .env file)
$env:DB_MCP_DATABASE = "sqlserver"  # or "postgres", "access_odbc", "access_com"
$env:DB_MCP_CONNECTION_STRING = "your-connection-string"

# Test the connection (will show configuration error if connection string is missing)
db-inspector-mcp
```

### Project Structure

```
db-inspector-mcp/
├── src/
│   └── db_inspector_mcp/
│       ├── __init__.py
│       ├── main.py            # MCP server entry point
│       ├── tools.py           # MCP tool definitions and SQL help content
│       ├── config.py          # Configuration management
│       ├── security.py        # SQL validation and permissions
│       ├── usage_logging.py   # Usage logging system
│       └── backends/
│           ├── __init__.py
│           ├── base.py        # Abstract base class (includes sql_dialect)
│           ├── access_com.py  # Access via COM automation
│           ├── access_odbc.py # Access via ODBC
│           ├── mssql.py       # SQL Server implementation
│           ├── postgres.py    # PostgreSQL implementation
│           └── registry.py    # Backend registry
└── tests/                     # Test suite
```

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

For developers actively working on improving this tool, see [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Development setup and testing
- Usage logging for improvement analysis
- How to analyze logs alongside source code to identify improvements
- Adding new backends and features

## Planned Enhancements

- MySQL / MariaDB backend support
- SQLite backend support
- Schema resources (list tables, views, indexes) as MCP Resources
- Query linting / formatting
- Caching layer for performance
- Index recommendation assistant
- Access migration helpers

