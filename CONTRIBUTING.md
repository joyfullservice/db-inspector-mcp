# Contributing to db-inspector-mcp

This guide is for developers actively working on improving the db-inspector-mcp tool itself.

## Development Setup

### 1. Clone and Install as Editable

```bash
git clone https://github.com/joyfullservice/db-inspector-mcp.git
cd db-inspector-mcp

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

**Why editable mode?** Changes to the source code take effect immediately without reinstalling. This also enables the usage logging feature to store logs within the project directory.

### 2. Run Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=db_inspector_mcp --cov-report=html

# Run specific test file
pytest tests/test_backends.py -v

# Skip integration tests (require Access installed)
pytest -m "not integration"
```

## Usage Logging for Improvement Analysis

When installed in editable mode, db-inspector-mcp can collect usage logs from any client project where logging is enabled. This creates a feedback loop for identifying issues and improvements.

### How It Works

1. **Client projects** enable logging via `DB_MCP_ENABLE_LOGGING=true` in their `.env`
2. **All tool calls** are logged to `{project_root}/logs/usage.jsonl` within your dev checkout
3. **You analyze logs** alongside the source code to identify patterns and improvements

### Enabling Logging in Client Projects

In any project using this MCP server, add to `.env`:

```bash
DB_MCP_ENABLE_LOGGING=true
```

Logs are automatically collected in your development checkout at:
```
db-inspector-mcp/
├── logs/
│   ├── usage.jsonl      # Current log file
│   ├── usage.jsonl.1    # Rotated backup
│   └── ...
├── src/
└── ...
```

### Log Format

Each log entry is a JSON object on its own line:

```json
{
  "timestamp": "2026-02-05T14:32:01.123456+00:00",
  "event": "tool_call",
  "tool": "db_count_query_results",
  "database": "legacy",
  "dialect": "access",
  "parameters": {
    "query": "SELECT * FROM users INNER JOIN orders ON..."
  },
  "success": false,
  "error": "Syntax error (missing operator) in query expression...",
  "error_pattern": "missing_operator_join",
  "execution_time_ms": 45.23
}
```

### Error Pattern Categories

The logging system automatically categorizes errors to help identify common issues:

| Pattern | Likely Cause |
|---------|--------------|
| `missing_operator_join` | Access requires parenthesized JOINs |
| `missing_operator_case` | Access needs IIF() instead of CASE |
| `syntax_error_limit` | Access uses TOP N, not LIMIT |
| `data_type_mismatch` | Type conversion issue |
| `too_few_parameters` | Missing parameter in query |
| `permission_denied` | Database access issue |
| `timeout` | Query exceeded timeout |

### Log Rotation

Logs are automatically rotated to prevent unbounded growth:

| Setting | Default | Description |
|---------|---------|-------------|
| `DB_MCP_LOG_MAX_SIZE_MB` | 10 | Max file size before rotation |
| `DB_MCP_LOG_BACKUP_COUNT` | 5 | Number of backup files to keep |

Total max storage: ~60MB (current + 5 backups × 10MB)

## Improvement Workflow

### Analyzing Logs with an AI Agent

The recommended workflow for improving the tool:

1. **Collect usage data** by enabling logging in client projects
2. **Open the db-inspector-mcp project** in Cursor (or similar AI-enabled editor)
3. **Ask the agent to analyze logs** and cross-reference with source code

Example prompts:

> "Read the logs in the logs/ folder and identify the most common errors. What patterns do you see?"

> "Look at the errors with pattern 'missing_operator_join' in the logs. Review the db_sql_help tool in tools.py - is the guidance sufficient? How could we improve it?"

> "Analyze the log entries where queries failed, then succeeded on retry. What syntax changes did agents make? Should we add hints for these patterns?"

### What to Look For

When analyzing logs, consider:

1. **Common error patterns** - Which errors occur most? Are they preventable with better guidance?

2. **Retry patterns** - When a query fails then succeeds, what changed? This reveals where agents struggle.

3. **Dialect confusion** - Are agents using standard SQL syntax with Access databases? Do they call `db_sql_help` before or after errors?

4. **Missing help topics** - Are there Access SQL patterns not covered by `db_sql_help`?

5. **Tool usage patterns** - Which tools are used most? Are agents following the recommended workflow?

### Making Improvements

Based on log analysis, common improvements include:

| Finding | Improvement |
|---------|-------------|
| Agents often hit JOIN syntax errors | Enhance `db_sql_help("joins")` examples |
| Agents don't call `db_sql_help` proactively | Update MCP instructions to recommend it |
| New error pattern not categorized | Add to `_extract_error_pattern()` in usage_logging.py |
| Agents confused about which tool to use | Improve tool docstrings |

## Project Structure

```
db-inspector-mcp/
├── src/db_inspector_mcp/
│   ├── __init__.py
│   ├── main.py              # MCP server entry point
│   ├── tools.py             # MCP tool definitions and SQL help content
│   ├── config.py            # Configuration management
│   ├── security.py          # SQL validation
│   ├── usage_logging.py     # Usage logging system
│   └── backends/
│       ├── base.py          # Abstract base class (includes sql_dialect)
│       ├── access_com.py    # Access via COM automation
│       ├── access_odbc.py   # Access via ODBC
│       ├── mssql.py         # SQL Server
│       ├── postgres.py      # PostgreSQL
│       └── registry.py      # Backend registry
├── tests/
│   ├── test_backends.py     # Backend tests
│   ├── test_tools.py        # Tool tests
│   ├── test_config.py       # Config tests
│   └── test_security.py     # Security tests
├── logs/                    # Usage logs (gitignored, dev mode only)
├── .env.example             # Example configuration
├── pyproject.toml           # Package configuration
└── README.md                # User documentation
```

## Adding New Features

### Adding a New Backend

1. Create `backends/newdb.py` implementing `DatabaseBackend`
2. Implement the `sql_dialect` property (return dialect identifier)
3. Implement all abstract methods
4. Register in `config.py` `_create_backend()`
5. Add tests in `tests/test_backends.py`
6. Update README with connection string examples

### Adding a New Tool

1. Add the function in `tools.py` with `@mcp.tool()` decorator
2. Add `@with_logging("tool_name")` decorator for usage tracking
3. Write comprehensive docstring (shown to AI agents)
4. Handle errors gracefully (return error dict, don't crash)
5. Add tests in `tests/test_tools.py`
6. Update README with usage examples

### Adding SQL Help Topics

1. Add new topic to `_SQL_HELP` dict in `tools.py`
2. Include: title, description, examples with SQL, pattern
3. Update `db_sql_help` docstring to list new topic
4. Add test for new topic

## Code Style

- Type hints on all function signatures
- Docstrings with Args/Returns sections
- Error messages should be actionable (tell user how to fix)
- Tools should return dicts, not raise exceptions for user errors
- Use `validate_readonly_sql()` for any user-provided SQL

## Questions?

Open an issue on GitHub for questions about contributing.
