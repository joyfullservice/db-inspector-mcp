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
# Run default test suite (Access integration tests are opt-in)
pytest

# Run with coverage
pytest --cov=db_inspector_mcp --cov-report=html

# Run specific test file
pytest tests/test_backends.py -v

# Skip integration tests even when Access is installed
pytest -m "not integration"

# Access integration tests run automatically when Access is detected.
# To force them off: DB_MCP_RUN_ACCESS_INTEGRATION=false pytest
# To force them on:  DB_MCP_RUN_ACCESS_INTEGRATION=true  pytest
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

1. Add the function in `tools.py` with the `@db_tool("tool_name")` decorator (this registers the tool with FastMCP, adds `.env` hot-reload, and enables usage logging in a single decorator)
2. Write comprehensive docstring (shown to AI agents)
3. Handle errors gracefully (return error dict, don't crash)
4. Add tests in `tests/test_tools.py`
5. Update README with usage examples

### Adding SQL Help Topics

1. Add new topic to `_SQL_HELP` dict in `tools.py`
2. Include: title, description, examples with SQL, pattern
3. Update `db_sql_help` docstring to list new topic
4. Add test for new topic

## Access COM Test Safety

**Never close a user's Access session from automated tests.**

The production code follows an ownership principle: we never call `CloseCurrentDatabase()` or `Quit()` on the Access Application — that is the user's responsibility. Tests must follow the same principle, because:

- The backend may attach to a user's already-open Access via `GetObject` instead of creating a new instance
- Calling `Quit()` on `backend._app` could close the user's work-in-progress
- Even `CloseCurrentDatabase()` would disrupt a user who opened the database in a special way (e.g., bypassing startup code with Shift)

**Current integration-test safety model:**

```python
# Integration tests auto-run when Access is detected via registry check.
# Override with DB_MCP_RUN_ACCESS_INTEGRATION=true/false if needed.

# The fixture creates its own Access instance via Dispatch().
app = win32com.client.Dispatch("Access.Application")

# The fixture owns this instance and calls app.Quit() in teardown.
# CloseCurrentDatabase() is guarded — only closes the temp test DB.

# _release_test_backend() only cancels the TTL timer and releases the
# COM reference — it never calls Quit() or CloseCurrentDatabase().
```

**Do NOT:**
- Call `app.Quit()` or `backend._app.Quit()` directly in test teardown
- Call `app.CloseCurrentDatabase()` without verifying it's the test database
- Assume that `backend._app` is an instance the test created

## Publishing a Release

Releases are published to [PyPI](https://pypi.org/project/db-inspector-mcp/) automatically via GitHub Actions when you create a GitHub Release. End users install with `uvx db-inspector-mcp`.

CI runs on push/PR and validates tests (excluding Access integration tests), package build, and package metadata checks.

### Release checklist

1. **Update the version number** in both places (they must match):
   - `pyproject.toml` — the `version` field
   - `src/db_inspector_mcp/__init__.py` — the `__version__` string

2. **Verify quality gates locally:**
   ```bash
   python -m pytest tests -v -m "not integration"
   python -m build
   twine check dist/*
   ```

3. **Commit and push** the version bump to `main`.

4. **Create a GitHub Release:**
   - Go to the repo on GitHub → Releases → Draft a new release
   - Create a new tag matching the version (e.g., `v0.2.0`)
   - Write release notes summarizing what changed
   - Click **Publish release**

5. **Verify the release workflows:**
   - Go to Actions tab → check that the CI workflow passed for the release commit/tag
   - Check that "Publish to PyPI" completed successfully (tests + build + `twine check` run before publish)
   - Visit `https://pypi.org/project/db-inspector-mcp/` to confirm the new version appears

6. **Test the published package:**
   ```bash
   uvx db-inspector-mcp@latest --version
   ```

## Code Style

- Type hints on all function signatures
- Docstrings with Args/Returns sections
- Error messages should be actionable (tell user how to fix)
- Tools should return dicts, not raise exceptions for user errors
- Use `validate_readonly_sql()` for any user-provided SQL

## Questions?

Open an issue on GitHub for questions about contributing.
