# db-inspector-mcp

A lightweight, extensible, cross-database MCP server (Model Context Protocol server) written in Python, designed to help AI coding assistants (e.g., Cursor, Claude Code, and other MCP-compatible clients) introspect, analyze, and verify SQL-based database systems.

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

## Getting Started

The quickest way to get running is with [uvx](https://docs.astral.sh/uv/guides/tools/) (the tool runner from uv). No cloning or virtual environments needed.

### 1. Install uv

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Register the MCP server

Add the server entry to your MCP client config. Both Cursor and Claude Code use the same `mcpServers` format, just in different files:

| Client | Project-level config | User-level (global) config |
|--------|---------------------|---------------------------|
| Cursor | `.cursor/mcp.json` | `~/.cursor/mcp.json` |
| Claude Code | `.mcp.json` | `~/.claude.json` |

Add this to the appropriate config file:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "uvx",
      "args": ["db-inspector-mcp@latest"]
    }
  }
}
```

The `@latest` suffix ensures `uvx` always pulls the latest version from [PyPI](https://pypi.org/project/db-inspector-mcp/) instead of using a cached copy.

**Alternative for Claude Code** -- you can use the CLI instead of editing JSON:

```bash
claude mcp add db-inspector-mcp -- uvx db-inspector-mcp@latest
```

**Shortcut** -- the built-in `init` command registers the server globally and creates a `.env` template in one step:

```bash
uvx db-inspector-mcp init
```

### 3. Configure your database connection

Create a `.env` file in your project root (or edit the one created by `init`):

```bash
# SQL Server
DB_MCP_DATABASE=sqlserver
DB_MCP_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password

# PostgreSQL
DB_MCP_DATABASE=postgres
DB_MCP_CONNECTION_STRING=dbname=mydb user=postgres password=secret host=localhost port=5432

# Microsoft Access (ODBC -- works without Access installed)
DB_MCP_DATABASE=access_odbc
DB_MCP_CONNECTION_STRING=Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\path\to\database.accdb;

# Microsoft Access (COM -- query-by-name, requires Access installed)
DB_MCP_DATABASE=access_com
DB_MCP_CONNECTION_STRING=Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\path\to\database.accdb;
```

See [Configuration](#configuration) for full details on connection strings, multi-database setups, and all environment variables.

### 4. Restart your client

Close and reopen Cursor or Claude Code. The MCP server will be detected and loaded automatically.

### 5. Try it out

Ask the AI assistant to use the database tools:

> "What tables are in the database? Use db_list_tables"

> "How many rows are in the users table? Use db_count_query_results"

> "Verify the database is read-only using db_check_readonly_status"

## Configuration

All configuration is done through environment variables, typically in a `.env` file in your project root. The server loads `.env` automatically at startup.

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DB_MCP_DATABASE` | Database type: `sqlserver`, `postgres`, `access_odbc`, or `access_com` | - | Yes* |
| `DB_MCP_CONNECTION_STRING` | Database connection string | - | Yes* |
| `DB_MCP_<name>_DATABASE` | Database type for named database (multi-database) | - | Yes* |
| `DB_MCP_<name>_CONNECTION_STRING` | Connection string for named database (multi-database) | - | Yes* |
| `DB_MCP_PROJECT_DIR` | Project directory for `.env` file lookup (see [User-Level Configuration](#user-level-configuration)) | auto-detected | No |
| `DB_MCP_QUERY_TIMEOUT_SECONDS` | Query timeout in seconds | `30` | No |
| `DB_MCP_ALLOW_DATA_ACCESS` | Global flag to enable data access tools | `false` | No |
| `DB_MCP_ALLOW_PREVIEW` | Per-tool override for `db_preview` | `false` | No |
| `DB_MCP_VERIFY_READONLY` | Verify read-only at startup | `true` | No |
| `DB_MCP_READONLY_FAIL_ON_WRITE` | Fail startup if write permissions detected | `false` | No |

*Either single-database (`DB_MCP_DATABASE` + `DB_MCP_CONNECTION_STRING`) or multi-database (`DB_MCP_<name>_DATABASE` + `DB_MCP_<name>_CONNECTION_STRING`) configuration is required.

### Connection Strings

#### SQL Server

```bash
DB_MCP_DATABASE=sqlserver

# ODBC connection string
DB_MCP_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password

# Or using DSN
DB_MCP_CONNECTION_STRING=DSN=MySQLServerDSN
```

#### PostgreSQL

```bash
DB_MCP_DATABASE=postgres
DB_MCP_CONNECTION_STRING=dbname=mydb user=postgres password=secret host=localhost port=5432
```

#### Microsoft Access

Two backends are available:

- **`access_odbc`** -- Standard SQL queries via ODBC (works without Access installed)
- **`access_com`** -- Query-by-name and native SQL extraction via COM (requires Access installed)

```bash
DB_MCP_DATABASE=access_odbc  # or access_com
DB_MCP_CONNECTION_STRING=Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\path\to\database.accdb;
```

Both backends support `.accdb`, `.accda`, and `.mdb` file formats. The driver name in the connection string is the same regardless of file extension.

**Relative paths** are resolved against the directory containing the `.env` file, making configurations portable:

```bash
DB_MCP_CONNECTION_STRING=Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=.\database.accdb;
# Or just the filename
DB_MCP_CONNECTION_STRING=database.accdb
```

Use `access_odbc` for standard SQL operations. Use `access_com` when you need to retrieve Access queries by name (see [`db_get_access_query_definition`](#db_get_access_query_definitionname-databasenone)).

### Multi-Database Configuration

Connect to multiple databases simultaneously for migration validation, testing, or comparison scenarios. Use the pattern `DB_MCP_<name>_DATABASE` and `DB_MCP_<name>_CONNECTION_STRING`:

```bash
# Migration scenario: Access to SQL Server
DB_MCP_LEGACY_DATABASE=access_com
DB_MCP_LEGACY_CONNECTION_STRING=Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\path\to\legacy.accdb;

DB_MCP_NEW_DATABASE=sqlserver
DB_MCP_NEW_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=localhost;Database=mydb;UID=user;PWD=password
```

Name databases to match your use case: `legacy`/`new`, `prod`/`dev`, `v1`/`v2`, etc. The first database configured (or one named "default") becomes the default.

When multiple databases are configured, all tools accept an optional `database` parameter:

```python
db_count_query_results("SELECT * FROM customers", database="legacy")
db_compare_queries(
    "SELECT * FROM customers WHERE active = 1",
    "SELECT * FROM customers WHERE status = 'active'",
    database1="legacy",
    database2="new"
)
```

Call `db_list_databases()` first to discover available database names.

### Environment Variable Precedence

1. **MCP server `env` section** (highest priority) -- values set in the MCP config file
2. **`.env.local`** -- personal overrides (gitignored)
3. **`.env`** -- project-specific configuration (lowest priority)

## MCP Client Setup

### Cursor

**Project-level** -- add `.cursor/mcp.json` to your project root (can be version-controlled for team sharing):

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "uvx",
      "args": ["db-inspector-mcp@latest"]
    }
  }
}
```

**User-level** -- add to `~/.cursor/mcp.json` to make the server available in all projects. The server automatically finds each project's `.env` file via workspace detection (see [User-Level Configuration](#user-level-configuration) below).

### Claude Code

**Project-level** -- add `.mcp.json` to your project root:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "uvx",
      "args": ["db-inspector-mcp@latest"]
    }
  }
}
```

**User-level** -- add to `~/.claude.json` to make the server available in all projects.

**CLI alternative** -- register without editing JSON:

```bash
claude mcp add db-inspector-mcp -- uvx db-inspector-mcp@latest
```

### Other MCP Clients

Any MCP-compatible client can use this server. The configuration format is the same `mcpServers` object shown above -- consult your client's documentation for where to place it.

### User-Level Configuration

When configured at the user level (global config), you don't need a per-project MCP config file. The server finds each project's `.env` file automatically:

1. **At startup** -- searches upward from the working directory for `.env`, `.cursor/mcp.json`, or `pyproject.toml`
2. **On first tool call** -- if no `.env` was found at startup, asks the client for its workspace roots via the MCP protocol

This works even when the working directory is not the project root (the typical case for user-level configs).

**Fallback:** If automatic detection doesn't work, set `DB_MCP_PROJECT_DIR` explicitly in your MCP config:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "uvx",
      "args": ["db-inspector-mcp@latest"],
      "env": {
        "DB_MCP_PROJECT_DIR": "C:\\Users\\me\\projects\\my-project"
      }
    }
  }
}
```

### Development Install

For contributing or running from source, see [CONTRIBUTING.md](CONTRIBUTING.md). The short version:

```bash
git clone https://github.com/joyfullservice/db-inspector-mcp.git
cd db-inspector-mcp
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -e ".[dev]"
```

For development installs, use `python -m db_inspector_mcp.main` as the command in your MCP config:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "command": "python",
      "args": ["-m", "db_inspector_mcp.main"]
    }
  }
}
```

### Troubleshooting

If the MCP server doesn't load:

1. **Check MCP logs** -- In Cursor, open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) and look for MCP-related output. In Claude Code, check the terminal output.

2. **Verify the command works** -- run `uvx db-inspector-mcp --help` in your terminal.

3. **Test the connection manually:**

   ```bash
   DB_MCP_DATABASE=sqlserver DB_MCP_CONNECTION_STRING="your-connection-string" uvx db-inspector-mcp
   ```

4. **Check your `.env` file** -- make sure it's in the project root, the connection string format matches your database type, and there are no syntax errors.

## Available Tools

### Database Management

#### `db_list_databases()`

List all configured database backends.

```python
db_list_databases()
# Returns: {"databases": [{"name": "source", "is_default": True}, {"name": "dest", "is_default": False}], "default": "source"}
```

### Query Analysis Tools

#### `db_count_query_results(query, database=None)`

Count rows a SELECT query returns by wrapping it in `SELECT COUNT(*) FROM (your_query)`.

```python
db_count_query_results("SELECT * FROM users WHERE active = 1")
# Returns: {"count": 1234}
```

#### `db_get_query_columns(query, database=None)`

Analyze column schema of a query's results (fetches 0 rows, inspects metadata only).

```python
db_get_query_columns("SELECT * FROM users")
# Returns: {"columns": [{"name": "id", "type": "int", "nullable": false, ...}, ...]}
```

#### `db_sum_query_column(query, column, database=None)`

Sum a specific column from a query's results.

```python
db_sum_query_column("SELECT amount FROM transactions", "amount")
# Returns: {"sum": 12345.67}
```

#### `db_measure_query(sql, max_rows=1000, database=None)`

Return execution time, row count, and whether the row cap was hit.

```python
db_measure_query("SELECT * FROM large_table", max_rows=1000)
# Returns: {"execution_time_ms": 123.45, "row_count": 1000, "hit_limit": true}
```

#### `db_preview(sql, max_rows=100, database=None)`

Sample N rows from a query result. Requires `DB_MCP_ALLOW_DATA_ACCESS=true` or `DB_MCP_ALLOW_PREVIEW=true`.

```python
db_preview("SELECT * FROM users ORDER BY created_at DESC", max_rows=10)
# Returns: {"rows": [{"id": 1, "name": "Alice", ...}, ...]}
```

#### `db_explain(sql, database=None)`

Return database-native execution plan.

```python
db_explain("SELECT * FROM users JOIN orders ON users.id = orders.user_id")
# Returns: {"plan": "<execution plan XML/JSON>"}
```

### Comparison Tool

#### `db_compare_queries(sql1, sql2, compare_samples=False, database1=None, database2=None)`

Compare two queries side-by-side, optionally from different databases. Useful for migration validation.

If `compare_samples=True`, requires data access permission.

```python
db_compare_queries(
    "SELECT * FROM customers WHERE active = 1",
    "SELECT * FROM customers WHERE status = 'active'",
    database1="legacy",
    database2="new",
    compare_samples=True
)
# Returns: {"row_count_diff": 0, "columns_missing_in_2": [], "type_mismatches": [], ...}
```

### Schema Introspection Tools

#### `db_list_tables(database=None)`

List all tables in the database with metadata.

```python
db_list_tables()
# Returns: {"tables": [{"name": "users", "schema": "dbo", "row_count": 1234}, ...]}
```

#### `db_list_views(database=None)`

List all views with their SQL definitions.

```python
db_list_views()
# Returns: {"views": [{"name": "active_users", "schema": "dbo", "definition": "SELECT ..."}, ...]}
```

**Note for Access COM backend**: Returns query names without SQL (extraction is expensive). Use `db_get_access_query_definition()` to get SQL for specific queries.

#### `db_get_access_query_definition(name, database=None)`

Get Access query SQL definition by name. Requires the `access_com` backend.

```python
db_get_access_query_definition("ActiveCustomers", database="legacy")
# Returns: {"name": "ActiveCustomers", "sql": "SELECT * FROM Customers WHERE Active = True", "type": "Select"}
```

### Security Tool

#### `db_check_readonly_status(database=None)`

Verify that the database connection is read-only.

```python
db_check_readonly_status()
# Returns: {"readonly": true, "details": "Read-only verification passed"}
```

## Security Model

### Read-Only by Default

All SQL queries are validated to reject write operations (INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, etc.).

### Data Access Permissions

Some tools require explicit authorization:

- **`db_preview`**: Requires `DB_MCP_ALLOW_DATA_ACCESS=true` or `DB_MCP_ALLOW_PREVIEW=true`
- **`db_compare_queries` with `compare_samples=True`**: Requires data access permission

Metadata tools (row counts, column schemas, execution plans) are always available.

### Data Access Considerations

By default, db-inspector-mcp exposes only schema metadata and aggregates — table names, column types, row counts, and execution plans. No actual row data leaves your database.

When you enable data access (`DB_MCP_ALLOW_DATA_ACCESS=true`), tools like `db_preview` return actual row values from your database. This data is sent to your AI provider as part of the conversation context. Depending on your provider and configuration, this data may be:

- **Retained** in conversation logs or audit trails
- **Used for model training**, potentially surfacing in future model outputs
- **Stored in regions** that may not align with your data residency requirements

Before enabling data access on databases that contain personally identifiable information (PII), protected health information (PHI), financial records, or other regulated data, verify your AI provider's data retention and model training policies. Most providers offer settings to opt out of training — ensure these are configured appropriately for your environment.

For granular control, use per-connection overrides to enable data access selectively — for example, allowing it on development databases while keeping it off for production:

```env
DB_MCP_DEV_ALLOW_DATA_ACCESS=true
DB_MCP_PROD_ALLOW_DATA_ACCESS=false
```

### Read-Only Verification

At startup (if `DB_MCP_VERIFY_READONLY=true`), the server verifies the database connection is read-only by checking role membership (SQL Server) or privileges (PostgreSQL). If write permissions are detected, a warning is logged. Set `DB_MCP_READONLY_FAIL_ON_WRITE=true` to exit on detection instead.

## CLI Commands

### `db-inspector-mcp`

Starts the MCP server (stdio transport). This is how MCP clients launch it.

### `db-inspector-mcp init`

Initialize db-inspector-mcp in a project directory:

1. Creates a `.env` file from the configuration template (use `--force` to overwrite)
2. Registers the server in `~/.cursor/mcp.json` and `~/.claude.json` for automatic discovery

```bash
db-inspector-mcp init                    # current directory
db-inspector-mcp init --force            # overwrite existing .env
db-inspector-mcp init --dir /path/to/project
```

### `db-inspector-mcp --version`

Show the installed version number.

### `db-inspector-mcp --help`

Show available commands.

## Development

For development setup, testing, project structure, and contribution guidelines, see [CONTRIBUTING.md](CONTRIBUTING.md).

For architectural decisions and design rationale, see [DECISIONS.md](DECISIONS.md).

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and adding new backends.

## Planned Enhancements

- MySQL / MariaDB backend support
- SQLite backend support
- Schema resources (list tables, views, indexes) as MCP Resources
- Query linting / formatting
- Caching layer for performance
- Index recommendation assistant
- Access migration helpers
