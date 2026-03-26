# Agent Guidelines

This file provides guidance for AI coding agents working on this project.

## Before Making Architectural Changes

Read [DECISIONS.md](DECISIONS.md) before modifying backend implementations or making architectural decisions. It contains a reverse-chronological journal of design decisions with context on what was considered, what was chosen, and why.

Relevant when working on:
- `src/db_inspector_mcp/backends/` — backend implementations (Access COM, Access ODBC, MSSQL, PostgreSQL)
- `src/db_inspector_mcp/config.py` — configuration and backend initialization
- `src/db_inspector_mcp/tools.py` — MCP tool definitions
- Connection lifecycle, caching, or pooling changes

## After Making Architectural Decisions

Append a new entry to the top of [DECISIONS.md](DECISIONS.md) following the format and guidelines in its header.

## Running Tests

This project uses a virtual environment. **Activate it before running tests:**

```powershell
# Windows (PowerShell)
venv\Scripts\Activate.ps1
pytest
```

```bash
# macOS/Linux
source venv/bin/activate
pytest
```

Or run pytest directly via the venv Python without activating:

```powershell
venv\Scripts\python.exe -m pytest          # Windows
./venv/bin/python -m pytest                # macOS/Linux
```

## Usage Logs

When `DB_MCP_ENABLE_LOGGING=true` is set in a client project's `.env`, all MCP tool calls are logged to a JSON Lines file.

**Log file location (default — uvx / pip install):**
```
~/.db-inspector-mcp/logs/usage.jsonl
```

**Log file location (development / editable install):**
```
logs/usage.jsonl          ← relative to this project root
```

**Override:** Set `DB_MCP_LOG_DIR` to use a custom directory.

Each line in `usage.jsonl` is a JSON object with fields like `timestamp`, `event`, `tool`, `database`, `dialect`, `parameters`, `success`, `error`, `error_pattern`, and `execution_time_ms`.

See [CONTRIBUTING.md § Usage Logging](CONTRIBUTING.md#usage-logging-for-improvement-analysis) for log format details, error pattern categories, and rotation settings.

## Project Conventions

- See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and code style
- See [README.md](README.md) for user-facing documentation and configuration
