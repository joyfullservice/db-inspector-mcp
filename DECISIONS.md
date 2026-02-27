# log-decision

> This file is a reverse-chronological journal of architectural and strategic
> decisions made during development. It is maintained by AI agents at the end
> of each working session and intended to be consumed by both humans and AI
> agents for future context. Agents should read this file before beginning
> work on any module referenced here. Newest entries are at the top.

---

## 2026-02-27 — Correct Access SQL dialect guidance (DISTINCT and wildcards)

**Trigger**: While fixing the TOP N injection bugs (below), we questioned whether the Access SQL guidance in `db_sql_help` and the MCP server instructions was empirically accurate. Two claims were tested against live Access databases via ODBC and found to be wrong.

**Claims tested** (10 live query pairs for DISTINCT, 4 for wildcards):

1. **"SELECT DISTINCT is unreliable in Access"** — FALSE. DISTINCT returned identical results to GROUP BY in every scenario tested: single column, multiple columns, table aliases, multiple parenthesized JOINs, IIF expressions, NULLs, CStr conversions, and complex WHERE clauses with 3-table joins. The "unreliable" claim likely originated from old Jet engine lore or from confusion with the separate issue of unparenthesized JOINs (which cause `missing operator` errors regardless of DISTINCT).

2. **"Access uses * and ? for LIKE wildcards, not % and _"** — BACKWARDS for ODBC. The Access ODBC driver operates in ANSI SQL mode where `%` and `_` are the correct wildcards. `*` and `?` are Access-native (Jet/ACE) wildcards used only in the Access query designer and DAO. Through ODBC, `LIKE '*Wire*'` matches zero rows while `LIKE '%Wire%'` matches correctly.

**Other claims verified correct**: `<>` (not `!=`), `IIF` (not `CASE WHEN`), `TOP N` (not `LIMIT`), `AND`/`OR` (not `&&`/`||`), `#date#` literals.

**Decision**: Updated all Access SQL guidance to reflect empirical reality:
- **DISTINCT**: Removed "unreliable" language. New guidance says both DISTINCT and GROUP BY work; if DISTINCT fails, check JOIN parentheses first.
- **Wildcards**: Flipped from `*`/`?` to `%`/`_`. New guidance explains ODBC uses ANSI wildcards and that Access-native wildcards don't work through ODBC.
- **Error hint for DISTINCT**: Removed entirely (was steering agents to GROUP BY for a non-existent problem).
- **Error hint for wildcards**: Updated to detect `*`/`?` in the query and tell agents to use `%`/`_` instead.

**What this rules out**: If there ARE edge cases where Access DISTINCT truly fails via ODBC, we no longer warn about them preemptively. The JOIN-parentheses hint should catch the most common false attribution. The wildcard guidance is now correct for ODBC but would be wrong for DAO — however, the DAO fallback path (access_com backend with VBA UDFs) is a rare codepath and agents don't construct LIKE queries differently for it.

**Relevant files**:
- `src/db_inspector_mcp/tools.py` — server instructions, `_ACCESS_ERROR_HINTS`, `db_sql_help` topics (distinct, wildcards, all)

---

## 2026-02-27 — Fix TOP N injection and subquery wrapping for whitespace, DISTINCT, and CTEs

**Trigger**: Three bugs discovered during production use on a SQL Server database. (1) Queries with leading whitespace before `SELECT` produced invalid SQL — the `TOP N` insertion point was calculated from the original string offset, not the stripped position, so `"\nSELECT col"` became `"SELECT TOP 10 ECT col"` (fragmenting `SELECT` and creating an `Invalid column name 'T'` error). (2) `SELECT DISTINCT` queries got `TOP N` inserted between `SELECT` and `DISTINCT`, producing the invalid `SELECT TOP N DISTINCT ...` instead of the correct `SELECT DISTINCT TOP N ...`. (3) CTE queries (`WITH ... AS`) broke all tools — subquery wrapping placed `WITH` inside parentheses (`FROM (WITH cte AS (...) SELECT ...)`), and TOP injection failed because the query starts with `WITH`, not `SELECT`.

All three bugs were confirmed in usage logs showing repeated failures with `Invalid column name 'T'`, `Incorrect syntax near the keyword 'DISTINCT'`, and `Incorrect syntax near the keyword 'WITH'`.

**Options explored**:
- **Inline fixes in each backend method** — patch the `query[6:]` offset, add DISTINCT detection, and add CTE splitting in each of the 15+ affected call sites. Error-prone and duplicative.
- **Shared utility module (chosen)** — extract the SQL manipulation logic into `sql_utils.py` with two functions: `inject_top_clause(query, n)` for TOP injection and `split_cte_prefix(query)` for CTE-aware subquery wrapping. Each backend method calls these instead of doing its own string manipulation.

**Decision**: New `backends/sql_utils.py` module with three public functions:

1. `inject_top_clause(query, n)` — strips whitespace, detects DISTINCT/ALL modifiers (inserts TOP after them), handles CTEs (finds the final top-level SELECT via parenthesis-depth tracking), and skips injection when TOP is already present.
2. `split_cte_prefix(query)` — splits a CTE query into `(cte_definitions, final_select)` so callers can wrap only the final SELECT while keeping CTE definitions at the top level. Returns `("", query)` for non-CTE queries.
3. `_find_final_select_pos(sql)` — internal helper that finds the last SELECT keyword at parenthesis depth 0, skipping SELECTs inside subqueries, CTEs, and single-quoted string literals.

The `_find_final_select_pos` parser tracks three things: parenthesis depth, single-quoted string boundaries (with escaped quote handling), and keyword word boundaries. It does not track SQL comments (`--`, `/* */`), which is a known limitation unlikely to matter in practice.

Affected backend methods (all updated to use the shared helpers):
- **TOP injection**: `measure_query` and `preview` in `MSSQLBackend`, `AccessODBCBackend`, and `AccessCOMBackend` DAO fallback
- **Subquery wrapping**: `count_query_results`, `get_query_columns`, and `sum_query_column` in all three backends

PostgreSQL is unaffected: it uses LIMIT (appended at the end, so whitespace/DISTINCT don't matter) and supports CTEs inside subqueries natively.

**What this rules out**: Nothing. The shared helpers are purely additive. The `"TOP " in query.upper()` guard still prevents double-injection but could false-positive on `TOP` inside a string literal or column name — this is a pre-existing edge case, not a regression.

**Relevant files**:
- `src/db_inspector_mcp/backends/sql_utils.py` — new module with `inject_top_clause`, `split_cte_prefix`, `_find_final_select_pos`
- `src/db_inspector_mcp/backends/mssql.py` — 5 query methods updated to use helpers
- `src/db_inspector_mcp/backends/access_odbc.py` — 5 query methods updated to use helpers
- `src/db_inspector_mcp/backends/access_com.py` — 5 DAO fallback methods updated to use helpers
- `tests/test_sql_utils.py` — 45 tests covering all three bugs and edge cases

---

## 2026-02-27 — VBA UDF support via ODBC-first, DAO-fallback in Access COM backend

**Trigger**: Access queries that reference VBA user-defined functions or domain aggregate functions (`DLookup`, `DCount`, `DSum`, etc.) fail when executed through the ODBC driver because it lacks the Access Application context.  The COM backend already delegates all SQL execution to an internal ODBC backend, so these queries fail even though the Application is available.

**Options explored**:

- **DAO-only execution** — Replace ODBC with DAO for all queries in the COM backend.  Simpler code path, but DAO Recordset iteration is slower than ODBC for large result sets (out-of-process COM call per row), and loses ODBC's connection-pooling benefits.
- **ODBC-first with DAO fallback (chosen)** — Try ODBC first.  If it fails with a UDF-related error ("undefined function" or "too few parameters"), retry via DAO `CurrentDb().OpenRecordset()`.  Zero overhead on the happy path; transparent fallback for UDF queries.
- **Explicit DAO mode parameter** — Add a `use_dao=True` flag to query tools.  Most explicit, but requires API changes and puts the burden on the caller to know when DAO is needed.

**Decision**: ODBC-first with DAO fallback.  Each public query method (`count_query_results`, `get_query_columns`, `sum_query_column`, `measure_query`, `preview`) catches exceptions, checks `_is_udf_error(e)` against two regex patterns, and retries via a parallel `_dao_*` method if matched.

Key design details:

- **CurrentDb requirement**: VBA modules are compiled into the Application's CurrentDb project.  `DBEngine.OpenDatabase()` cannot resolve them.  A dedicated `_dao_currentdb()` context manager guarantees `CurrentDb()` by calling `_ensure_current_db(app)` before yielding the DAO Database.
- **CurrentDb lifecycle**: The yielded DAO Database is owned by the Application — callers must NOT call `.Close()` on it.  Only Recordsets opened from it are explicitly closed (in `_dao_execute`'s `try/finally`).  The COM proxy goes out of scope when the context manager exits.
- **Error detection heuristic**: `_UDF_ERROR_PATTERNS` matches "undefined function" (explicit) and "too few parameters" (ODBC treats unrecognised function calls as parameter placeholders).  Non-matching errors propagate without retry.
- **DAO field types**: `_DAO_FIELD_TYPES` maps DAO `Field.Type` integer codes to human-readable names for `get_query_columns` output.

Also added:
- UDF error hints in `_ACCESS_ERROR_HINTS` (for ODBC-only backends — suggests switching to `access_com`)
- `db_sql_help('udfs')` topic with VBA UDF and domain function examples
- MCP server instructions updated to mention VBA UDF support

**What this rules out**: Pure DAO execution mode (no way to force DAO without first failing on ODBC).  If a query triggers a "too few parameters" error for a reason unrelated to UDFs, it will be retried via DAO (adding latency but producing a potentially more descriptive DAO error).

**Relevant files**:
- `src/db_inspector_mcp/backends/access_com.py` — `_dao_currentdb()`, `_dao_execute()`, `_is_udf_error()`, `_dao_*` query methods, ODBC→DAO fallback in public methods
- `src/db_inspector_mcp/tools.py` — UDF error hints, `db_sql_help('udfs')`, updated MCP instructions
- `tests/test_backends.py` — 8 new unit tests for DAO fallback behaviour

---

## 2026-02-27 — Composite `@db_tool` decorator for config hot-reload and logging

**Trigger**: Two interrelated bugs discovered during production use. (1) `.env` hot-reload only triggered on 2 of 13 tools (`db_preview` and `db_compare_queries` via `check_data_access()` -> `load_config()`). The other 11 tools never called `load_config()`, so editing `.env` had no effect until server restart. (2) The logging system cached `_logging_enabled = False` permanently when `_initialize_logging()` ran before the lazy `.env` load populated `DB_MCP_ENABLE_LOGGING`. Logging was silently disabled for two weeks after the lazy-init change on Feb 17.

**Options explored**:
- **Add `load_config()` call to each tool** — simple but error-prone. Developers adding new tools must remember two separate concerns (config refresh + logging), and forgetting either is a silent bug.
- **Separate `@with_config_refresh` decorator** — three stacked decorators per tool (`@mcp.tool()` + `@with_config_refresh` + `@with_logging`). Ordering matters and is easy to get wrong.
- **Composite `@db_tool("name")` decorator (chosen)** — single decorator replaces `@mcp.tool()` + `@with_logging("name")`. Internally composes `load_config()` -> `with_logging` -> tool body, guaranteeing correct call order. Impossible to forget config refresh on new tools.

**Decision**: Composite `@db_tool("name")` decorator in `tools.py`. Call order:

```
FastMCP dispatch
  -> with_refresh (load_config — one stat() call, reload if .env changed)
    -> with_logging (check _initialize_logging() with fresh env vars, time execution, log result)
      -> tool function body
```

Config refresh runs first so `_initialize_logging()` sees current env vars. The logging timer measures only tool execution, not config reload overhead.

Additional fixes in this change:
- `_initialize_logging()` no longer caches `False` when disabled. This lets it re-check `os.getenv()` on each call until logging is successfully initialized or a real I/O failure occurs (which *is* cached to avoid retry spam).
- `reset_logging()` function added to `usage_logging.py`. Closes the file handler and clears all module state.
- `load_config()` calls `reset_logging()` unconditionally when `.env` is reloaded (not just when backend config changes), so logging config changes take effect immediately.

**Known limitation**: In the lazy-init scenario (user-level MCP config), the first `db_list_databases` call won't be logged. The lazy-init loads `.env` inside the tool body (after `with_logging` already checked and found no `DB_MCP_ENABLE_LOGGING`). All subsequent calls log correctly.

**What this rules out**: Per-tool customisation of reload behaviour. All tools share the same config refresh + logging lifecycle.

**Relevant files**: `src/db_inspector_mcp/tools.py` (decorator + all 13 tool registrations), `src/db_inspector_mcp/usage_logging.py` (`_initialize_logging` fix, `reset_logging`), `src/db_inspector_mcp/config.py` (`reset_logging` call), `tests/test_logging.py` (new), `tests/test_config.py` (3 new tests), `CONTRIBUTING.md` (updated instructions)

---

## 2026-02-26 — Hot-reload `.env` files via mtime check

**Trigger**: When running the MCP server globally (user-level `mcp.json`), changing per-project data-access permissions in `.env` required restarting the server. This was friction for users toggling `DB_MCP_ALLOW_DATA_ACCESS` or `DB_MCP_ALLOW_PREVIEW` across different projects.

**Options explored**:
- **File watcher (`watchdog`)** — true hot-reload via OS-level file events. Adds a dependency, requires a background thread, and needs thread-safe config access. Overkill for monitoring one or two files.
- **Periodic polling (mtime check per tool call)** — compare the `.env` file's modification time on each `load_config()` call. Zero dependencies, no background threads, effectively instant because every tool call checks. Cost is a single `os.stat()` per tool call (~microseconds).
- **Explicit `db_reload_config` tool** — the AI or user invokes a tool to reload. Simplest, but not automatic.

**Decision**: Mtime-based polling in `load_config()`. On every tool call, `_check_env_reload()` compares the stored mtime of `.env` and `.env.local` against the current value. If changed, the environment is re-read and config takes effect immediately.

**How it works**:

```mermaid
flowchart TD
    ToolCall["Tool call begins"] --> LoadConfig["load_config()"]
    LoadConfig --> CheckMtime["_check_env_reload()"]
    CheckMtime -->|"mtime unchanged"| ReadEnv["Read from os.getenv()"]
    CheckMtime -->|"mtime changed"| Reload["Re-load .env files"]
    Reload --> DetectChanges["Compare old vs new config"]
    DetectChanges -->|"Only permission flags changed"| ReadEnv
    DetectChanges -->|"Backend config changed"| ReInit["Clear registry, re-initialize backends"]
    ReInit --> VerifyReadonly["Re-verify readonly status"]
    VerifyReadonly --> ReadEnv
    ReadEnv --> ReturnConfig["Return config dict"]
```

Key behaviours:
- **First load** uses `load_dotenv(override=False)` so MCP `env`-section values take precedence.
- **Reloads** use `load_dotenv(override=True)` so the user's edits replace old values.
- **Permission-only changes** (e.g. `DB_MCP_ALLOW_DATA_ACCESS`) take effect immediately — no backend re-init needed.
- **Backend config changes** (e.g. `DB_MCP_DATABASE`, `DB_MCP_CONNECTION_STRING`) trigger `registry.clear()` and `initialize_backends()`, followed by read-only verification.
- **Removed variables**: if a line is deleted from `.env`, the old value remains in `os.environ` until server restart. This is documented as a known limitation — the alternative (tracking every key loaded from `.env`) adds complexity for a rare edge case.
- **Backend re-init safety**: old backend objects are discarded. Access connections expire via 5 s TTL; MSSQL/Postgres connections close via `__del__` on GC.

**What this rules out**: Full variable removal detection without restart. Acceptable trade-off for simplicity.

**Relevant files**: `src/db_inspector_mcp/config.py`, `src/db_inspector_mcp/backends/registry.py`, `tests/test_config.py`

---

## 2026-02-26 — Module-scoped fixtures for Access COM integration tests

**Trigger**: The three integration tests in `tests/test_backends.py` took ~25s total because the `temp_access_db` fixture (function-scoped) launched and quit Access per test, and each test then launched Access again through the backend. That's 4+ launch/quit cycles at ~5s each, plus `gc.collect()` + `time.sleep()` delays. The tests were verifying database operations (list_tables, list_views, get_query_by_name), not the Application launch lifecycle.

**Options explored**:
- **Function-scoped fixture (status quo)** — isolated per test but extremely slow. Each test creates a fresh database in a fresh Access instance, quits, then the test launches Access again.
- **Session-scoped fixture** — one Access for the entire pytest session. Rejected: too broad — other test modules don't need Access, and session scope complicates test selection with `-m integration`.
- **Module-scoped fixtures (chosen)** — split into `access_app` (manages Application lifecycle) and `temp_access_db` (creates database using the shared app). Access launches once, database is created once, all three tests attach via `GetObject`, Access quits once in module teardown.

**Decision**: Module-scoped fixtures. The `access_app` fixture launches Access once and quits in teardown. The `temp_access_db` fixture creates the test database using the shared app and leaves it open as CurrentDb — backends connect instantly via `GetObject(db_path)` (~10ms vs ~5s cold start). Test teardown simplified to a `_release_test_backend()` helper that cancels the TTL timer and releases the COM reference (no quit). `_safe_quit_test_access` removed entirely. `test_access_com_with_closed_database` renamed to `test_access_com_backend_connects_and_queries` since it no longer tests the cold-start path (covered by mock unit tests).

Also added `_suppress_com_seh()` context manager to suppress harmless `RPC_E_DISCONNECTED` SEH tracebacks that Python's `faulthandler` prints during COM teardown when Access has already been quit.

**What this rules out**: The integration tests no longer exercise the cold-start `EnsureDispatch` path (Access not running at all). This path is covered by the mock unit test `test_access_com_get_query_by_name`. If the cold-start path needs integration-level coverage in the future, a dedicated test with its own function-scoped fixture could be added.

**Relevant files**:
- `tests/test_backends.py` — restructured fixtures, simplified teardowns, added `_suppress_com_seh()` and `_release_test_backend()`

---

## 2026-02-26 — Fix stack overflow in Access COM unit tests (mock setup)

**Trigger**: Four unit tests in `tests/test_backends.py` (`test_access_com_get_query_by_name`, `test_access_com_dao_database_closes_on_error`, `test_access_com_dao_database_uses_currentdb_when_available`, `test_access_com_list_views`) caused a fatal Windows stack overflow, crashing the entire pytest process. This was the first thing any contributor would hit when running the test suite.

**Options explored**:
- **Patch `_get_access_app` directly to return mock_app** — would bypass the COM acquisition code entirely, making tests simpler but less thorough. Rejected: the tests implicitly exercise the acquisition path, and bypassing it hides future bugs in that code.
- **Fix the mock setup to properly intercept all COM entry points (chosen)** — patch `gencache` alongside `win32com.client`, and configure `mock_app.hWndAccessApp.return_value` to prevent mock recursion. Keeps the existing test structure and exercises more production code.

**Decision**: Three fixes applied across all four tests:

1. **Patched `gencache` separately from `win32com.client`**. The production code calls `gencache.EnsureDispatch()`, not `win32com.client.Dispatch()`. Since `gencache` is imported at module level via `from win32com.client import gencache`, patching `win32com.client` does not intercept it — `gencache` is already bound as a direct reference. Each test now patches `db_inspector_mcp.backends.access_com.gencache` and sets `mock_gencache.EnsureDispatch.return_value = mock_app`.

2. **Set `mock_app.hWndAccessApp.return_value = 0`**. The `_set_access_visible()` helper calls `app.hWndAccessApp()`. On an unconfigured `MagicMock`, this triggers infinite recursion in Python's `_mock_add_spec` internals, causing a fatal stack overflow. Setting an explicit return value short-circuits the mock chain.

3. **Added timer cleanup** after each test (`backend._close_timer.cancel()`) to prevent daemon timer threads from leaking between tests.

Additionally, two tests were silently broken by the earlier MSysObjects refactoring (2026-02-12 decision) and were fixed:
- `test_access_com_dao_database_uses_currentdb_when_available`: `mock_current_db.Name` was never set to match `self._db_path`, so `_paths_match()` always returned False and the test never actually exercised the CurrentDb path. Fixed by setting `Name = "C:\\test.accdb"` and adding `OpenRecordset.side_effect = Exception(...)` to trigger the TableDefs fallback.
- `test_access_com_list_views`: `list_views()` now uses MSysObjects SQL first, but the mock had no `OpenRecordset` failure configured, so the recordset's `EOF` (a truthy MagicMock) caused an empty result. Fixed by adding `OpenRecordset.side_effect = Exception(...)` to trigger the QueryDefs fallback.

**What this rules out**: Nothing. The fixes are purely in test infrastructure. If `gencache` is ever replaced with a different dispatch mechanism in production code, the test patches would need updating to match.

**Relevant files**:
- `tests/test_backends.py` — fixed mock setup in 4 tests, added timer cleanup

---

## 2026-02-20 — Skip project-level mcp.json in setup prompt when globally registered

**Trigger**: The `setup_db_inspector` MCP prompt always included a step instructing the AI to create a project-level `.cursor/mcp.json`, even when the server was already registered in the global `~/.cursor/mcp.json`. For users who ran `db-inspector-mcp init` or manually added the server to their global config, this step was redundant — it would create a project-level file that shadows the global entry with identical content.

**Options explored**:
- **Always include the mcp.json step (status quo)** — simple but creates unnecessary project-level config files. Users following the prompt would end up with both a global and project-level entry for the same server.
- **Never include the mcp.json step** — assumes global registration is always present. Breaks for users who only use project-level configs without a global entry.
- **Conditionally include based on global registration check (chosen)** — read `~/.cursor/mcp.json` at prompt invocation time to check if `db-inspector-mcp` is already registered. If yes, skip the step. If no, include it. The check is cheap (one file read) and handles missing/corrupt files gracefully.

**Decision**: Added `is_globally_registered()` to `init.py` — a public helper that reads `~/.cursor/mcp.json` and returns `True` if `db-inspector-mcp` exists in `mcpServers`. Returns `False` for missing files, corrupt JSON, or OS errors. The `setup_db_inspector` prompt in `tools.py` calls this function and conditionally omits the "create `.cursor/mcp.json`" step, adjusting step numbering accordingly.

**What this rules out**: Nothing. The prompt still includes the mcp.json step when the server is not globally registered. If a user has a global entry but wants a project-level override with different settings (e.g., `env` overrides), they would need to create it manually — but this is an advanced scenario not covered by the setup prompt regardless.

**Relevant files**:
- `src/db_inspector_mcp/init.py` — added `is_globally_registered()`
- `src/db_inspector_mcp/tools.py` — updated `setup_db_inspector` prompt to conditionally skip mcp.json step
- `tests/test_init.py` — 4 new tests for `is_globally_registered()` (registered, not registered, file missing, corrupt JSON)

---

## 2026-02-20 — Lazy backend imports for lightweight global MCP startup

**Trigger**: When db-inspector-mcp is configured globally (user-level `~/.cursor/mcp.json`), it loads for every project — including projects that have no `.env` file and will never use database tools. At startup, all four backend modules were imported eagerly at module level, pulling in heavy C-extension database drivers (`pyodbc`, `pywin32`/COM, `psycopg2`) before the server even checked whether a `.env` file existed. This added unnecessary memory and startup time for projects that don't use the tool.

**Options explored**:
- **Status quo (eager imports)** — `config.py` imported all four backend classes at the top level. `tools.py` imported `AccessCOMBackend` at the top level for an `isinstance` check. `backends/__init__.py` re-exported all backend classes. Every startup loaded `pyodbc` (C ext), `pythoncom`/`pywintypes`/`win32com.client` (C ext), regardless of need.
- **Conditional imports behind a "has .env" check** — check for `.env` before importing anything. Rejected: the `.env` discovery logic itself lives in `config.py`, creating a chicken-and-egg problem. Also fragile if config comes from env vars instead of `.env`.
- **Lazy imports at point of use (chosen)** — move backend class imports into the specific `if/elif` branches of `_create_backend()` where they are actually constructed. Each driver is only loaded when its backend type is requested. For `tools.py`, move the `AccessCOMBackend` import into the single function that uses it (`db_get_access_query_definition`).
- **Optional dependencies in `pyproject.toml`** — make `pyodbc`, `psycopg2-binary`, and `pywin32` optional extras instead of hard dependencies. Would reduce install footprint but is a larger change affecting docs, install commands, and the `init` CLI. Deferred as a follow-up since lazy imports solve the runtime cost without changing the install experience.

**Decision**: Three targeted changes to defer all heavy imports:
1. `config.py` — removed four top-level backend imports (`AccessCOMBackend`, `AccessODBCBackend`, `MSSQLBackend`, `PostgresBackend`). Each is now imported inside its corresponding branch of `_create_backend()`. The lightweight `DatabaseBackend` base class and `BackendRegistry` imports remain (stdlib-only dependencies).
2. `tools.py` — removed top-level `from .backends.access_com import AccessCOMBackend`. Moved into the body of `db_get_access_query_definition()`, the only function that uses it for an `isinstance` check.
3. `backends/__init__.py` — removed re-exports of all four concrete backend classes. Now only exports `DatabaseBackend`, `BackendRegistry`, and `get_registry`. No current code imports from this path, but it was a latent risk if any future code did `from .backends import AccessCOMBackend`.

After these changes, startup for projects without a `.env` file loads only: stdlib, `python-dotenv`, `mcp`, and the lightweight base/registry modules. No C extensions. The `postgres` backend was already partially lazy (uses `TYPE_CHECKING` guard), so it required no changes.

**What this rules out**: Nothing. The lazy imports are transparent — backends load on first use exactly as before. The optional-dependencies approach (`pyproject.toml` extras) remains available as a follow-up to reduce install footprint. The trigger to pursue that would be: users reporting slow `pip install` times or disk usage concerns from database drivers they don't need.

**Relevant files**:
- `src/db_inspector_mcp/config.py` — removed 4 top-level imports, added 4 inline imports in `_create_backend()`
- `src/db_inspector_mcp/tools.py` — moved `AccessCOMBackend` import into function body
- `src/db_inspector_mcp/backends/__init__.py` — removed 4 concrete backend re-exports

---

## 2026-02-20 — CLI `init` command and MCP prompt for project setup

**Trigger**: Developers integrating db-inspector-mcp into a new project had to manually copy `.env.example` to `.env` and create/edit `.cursor/mcp.json`. This multi-step process was error-prone and undiscoverable. The goal was a one-command setup experience.

**Options explored**:
- **MCP Prompt only (slash command)** — MCP prompts surface as user-invokable workflows in clients. Attractive for in-IDE use, but Cursor's support for surfacing MCP prompts as slash commands is still maturing and may not be discoverable. Not reliable as the primary path.
- **MCP Tool (`db_get_env_template`)** — would return template content for the AI to write. Less discoverable than a prompt and redundant if the prompt already embeds the content. Deferred.
- **CLI init command only** — proven pattern (`git init`, `npm init`). Works everywhere, no IDE dependency. Reliable but doesn't help users who are already in a Cursor chat session.
- **CLI init + MCP prompt (chosen)** — CLI `init` is the documented primary path; MCP prompt is a bonus for in-IDE discovery. Low marginal effort to implement both since they share the same template loader.
- **Post-install hook to auto-register in global mcp.json** — ideal UX, but modern pip (PEP 517) does not support post-install hooks. `setup.py cmdclass` is deprecated and unreliable with `pip install -e .`. Rejected.
- **Separate entry point (`db-inspector-mcp-init`)** — simpler than refactoring `main()`, but two binaries is confusing for users. Rejected in favor of argparse subcommand dispatch.
- **Duplicate template file in `src/`** — would simplify `importlib.resources` packaging, but creates a maintenance burden keeping two files in sync. Rejected; runtime path resolution from `__file__` is used for editable installs, with `importlib.resources` fallback for wheel installs.

**Decision**: Added `db-inspector-mcp init` as an argparse-dispatched subcommand in `main.py`. The command: (1) copies `.env` from the bundled `.env.example` template, failing if `.env` exists unless `--force`; (2) registers the server in `~/.cursor/mcp.json` (idempotent). Also added `@mcp.prompt()` named `setup_db_inspector` that returns the template content with setup instructions for the AI.

Critical design choice: the global `~/.cursor/mcp.json` entry contains **only the command** (`{"command": "db-inspector-mcp"}`), with no `env` overrides. This is because `mcp.json` env values become process-level environment variables that take precedence over `.env` files (which are loaded with `override=False` via python-dotenv). Putting settings like `DB_MCP_ALLOW_DATA_ACCESS` in the global config would prevent per-project `.env` customization.

Template loading uses a two-strategy approach: first tries `Path(__file__).parent.parent.parent / ".env.example"` (works for editable installs where source is directly linked), then falls back to `importlib.resources.files("db_inspector_mcp").joinpath(".env.example")` (for wheel installs with package-data). No duplicate file is maintained.

**What this rules out**: The `init` command currently targets Cursor only (writes to `~/.cursor/mcp.json`). Supporting other MCP clients (Claude Desktop, VS Code Copilot) would require additional registration logic or a `--client` flag. The `importlib.resources` fallback for wheel installs depends on `[tool.setuptools.package-data]` correctly bundling `.env.example` — this needs verification when publishing to PyPI (the `package-data` path `"../../.env.example"` relative to the package dir is fragile for non-editable installs).

**Relevant files**: `src/db_inspector_mcp/init.py` (new), `src/db_inspector_mcp/main.py`, `src/db_inspector_mcp/tools.py`, `pyproject.toml`, `tests/test_init.py` (new), `README.md`

---

## 2026-02-20 — Inline cursor rule for test execution and shell commands

**Trigger**: Across multiple development sessions, AI agents repeatedly failed to run the test suite correctly. Three recurring failure patterns were observed in chat transcripts: (1) running bare `python -m pytest` or `pytest` without the venv, getting "No module named pytest"; (2) using bash-only commands like `head` or `tail` on Windows PowerShell; (3) attempting `pip install` system-wide to fix missing dependencies instead of using the existing venv. The project's `AGENTS.md` already contained venv instructions, but the existing `.cursor/rules/agents.mdc` only said "read AGENTS.md" — agents either didn't follow the indirection or skipped the testing section.

**Options explored**:
- **Rely on `AGENTS.md` (status quo)** — instructions exist but are behind a "go read this file" indirection in the cursor rule. Agents frequently skip or skim the file. Observed to fail repeatedly across sessions.
- **Add more detail to `agents.mdc`** — would work for Cursor but the rule was designed as a lightweight pointer to `AGENTS.md` (which also serves Claude Code and human readers). Bloating it with testing commands would duplicate content.
- **Dedicated `testing.mdc` cursor rule with `alwaysApply: true` (chosen)** — puts the exact commands and warnings directly in the rule, so they're injected into every agent session without indirection. Covers the venv Python path, the "do NOT run pip install" warning, and PowerShell command equivalents.

**Decision**: Created `.cursor/rules/testing.mdc` with `alwaysApply: true`. Contains the full venv pytest command, explicit "do NOT" warnings for common mistakes, and a PowerShell command reference replacing bash-only tools. Kept `agents.mdc` unchanged — it continues to point to `AGENTS.md` for architectural guidance, which is a lower-frequency concern that tolerates the indirection.

**What this rules out**: Nothing. The rule can be extended with additional commands as new failure patterns emerge. If the project moves to a different OS or shell, the PowerShell-specific guidance would need updating.

**Relevant files**: `.cursor/rules/testing.mdc`

---

## 2026-02-20 — Relative path support for Access database files in .env

**Trigger**: Access database paths in `.env` connection strings required absolute paths (e.g., `DBQ=C:\Projects\myapp\database.accdb`). Since the database file is often in the same directory as the `.env` file, users wanted to write `DBQ=.\database.accdb` or just `database.accdb` for portability across machines.

**Options explored**:
- **Resolve in the backends** — have `AccessODBCBackend` and `AccessCOMBackend` resolve relative paths internally. Rejected: requires passing the base directory into the backends, changing their interface, and the backends shouldn't need to know about `.env` file locations.
- **New env var `DB_MCP_DATABASE_DIR`** — explicit base directory for relative path resolution. Rejected: unnecessary configuration — the `.env` file's directory is the natural and obvious base.
- **Resolve in `config.py` at backend initialization time (chosen)** — a `_resolve_connection_string_paths()` helper resolves relative `DBQ=` paths (and bare file paths) against the project root before connection strings reach the backends. Backends receive fully resolved absolute paths and are unchanged.

**Decision**: Added `_resolve_connection_string_paths()` in `config.py`. It extracts the path from `DBQ=...` (regex, case-insensitive) or treats the whole string as a bare path (when no `DBQ=` or `Driver=` is present). If the path is relative (`not Path.is_absolute()`), it's resolved against the stored project root. A diagnostic message is logged to stderr on resolution, and a warning is emitted if the resolved file doesn't exist on disk. The project root is now stored in a module-level `_project_root` variable, set during `.env` loading (in `_load_env_files()`, `_load_env_from_directory()`, and `initialize_from_workspace()`), with a `_get_project_root()` getter that falls back to `_find_project_root()`.

The resolver is called in two places: `get_backend()` and `initialize_backends()`, both just before `_create_backend()`. Only Access backends (`access_odbc`, `access_com`) are affected; other backend types pass through unchanged.

**What this rules out**: Relative paths are resolved against the project root (where `.env` was loaded from), not the CWD. If `_find_project_root()` matches on `pyproject.toml` or `.cursor/mcp.json` but the `.env` is in a different directory, the base directory might not be what the user expects — but this scenario is unlikely in practice since these markers co-locate. When env vars are set via `mcp.json` env section (no `.env` file), the project root is still discovered via CWD or `DB_MCP_PROJECT_DIR`.

**Relevant files**:
- `src/db_inspector_mcp/config.py` — `_project_root`, `_get_project_root()`, `_resolve_connection_string_paths()`, wired into `get_backend()` and `initialize_backends()`
- `tests/test_config.py` — 12 new tests for path resolution
- `.env.example` — added relative path examples and explanation
- `README.md` — added "Relative paths" note in Access Connection Strings section

---

## 2026-02-18 — Suppress noisy MCP SDK logging and deduplicate startup diagnostics

**Trigger**: The Cursor MCP tool log window showed many `[error]`-tagged lines during a normal, healthy startup. Two root causes: (1) the MCP Python SDK's `FastMCP` defaults to `log_level="INFO"`, which configures Python's `logging.basicConfig()` with a `RichHandler` writing to stderr — and Cursor labels all stderr output as `[error]`; (2) `_load_env_files()` in `config.py` was called twice during startup (once from `get_config()`, again from `initialize_backends()` → `load_config()`), printing the "Working directory / Resolved project root / No .env file" messages twice.

**Options explored**:
- **Redirect logging to a file** — would eliminate all stderr noise but lose visibility in Cursor's log pane entirely. Rejected: startup diagnostics in the log pane are useful for debugging configuration issues.
- **Set MCP SDK log level to WARNING (chosen for issue 1)** — `FastMCP(log_level="WARNING")` suppresses the per-request INFO messages ("Processing request of type ListToolsRequest" etc.) while keeping warnings and errors visible. The INFO messages carry no diagnostic value for users.
- **Add `_env_loaded` guard to `_load_env_files()` (chosen for issue 2)** — a module-level boolean flag ensures the function's side effects (loading dotenv files and printing diagnostics) only execute once per process, regardless of how many callers invoke `load_config()`.
- **Restructure callers to avoid double-calling `load_config()`** — would work but is more invasive and fragile; the guard is simpler and self-contained.

**Decision**: Applied both targeted fixes. The `log_level="WARNING"` parameter on `FastMCP()` stops the SDK's per-request INFO messages from reaching stderr. The `_env_loaded` guard in `_load_env_files()` prevents duplicate startup diagnostic output. The remaining stderr output (working directory, project root, .env status) still appears once in the log pane, which is the intended behavior for configuration diagnostics.

**What this rules out**: If a user wants to see per-request MCP protocol logging for debugging, they would need to change the `log_level` parameter or set `FASTMCP_LOG_LEVEL=INFO` (the SDK reads settings from environment variables with `FASTMCP_` prefix). The `_env_loaded` guard means that if something dynamically changes environment variables and re-calls `load_config()`, the dotenv files won't be reloaded — but this is intentional since env files should only be loaded once at startup.

**Relevant files**: `src/db_inspector_mcp/tools.py`, `src/db_inspector_mcp/config.py`

---

## 2026-02-18 — Expand MCP server instructions based on transcript review

**Trigger**: Reviewed all recent agent transcripts to identify gaps in the MCP server instructions that agents receive. The existing instructions covered the basic workflow and Access SQL differences, but several features added during development (object counts, name filters, Access query definitions, error hints, EXPLAIN limitations, data preview permissions) were not reflected in the instructions agents see.

**Options explored**:
- **Keep instructions minimal** — rely on individual tool docstrings for details. Rejected: agents don't read all docstrings upfront, so they miss important context like the `name_filter` pattern for large databases or the Access EXPLAIN limitation.
- **Expand server-level instructions with key guidance (chosen)** — add the missing items to the `instructions` string in `FastMCP()`. This is what agents see before making any tool calls, so it front-loads the most impactful guidance.

**Decision**: Expanded the server instructions to cover seven gaps:
1. `object_counts` + `name_filter` pattern for large databases (>200 objects)
2. `db_get_access_query_definition` in the workflow for Access migration
3. DISTINCT vs GROUP BY unreliability in Access
4. CTEs not supported in Access
5. EXPLAIN not supported in Access (use `db_measure_query` instead)
6. Error messages include actionable hints for Access SQL failures — read before retrying
7. `db_preview` requires `DB_MCP_ALLOW_DATA_ACCESS=true` — fall back to count/columns tools

Also removed the CTE example from `db_count_query_results` docstring (CTEs don't work in Access, the primary use case), and added an Access-specific note to the `db_explain` docstring.

> **⚠ Partially superseded** (2026-02-27): Item 3 ("DISTINCT vs GROUP BY unreliability") was empirically disproven — see "Correct Access SQL dialect guidance" entry above. The DISTINCT guidance was removed from the server instructions. Item 4 (wildcards using `*` and `?`) was also corrected — Access ODBC uses ANSI `%` and `_`.

**What this rules out**: Nothing. Instructions can be further refined as real external agent usage patterns emerge. The current transcripts were primarily development sessions, so these changes are based on code analysis rather than observed agent failures.

**Relevant files**: `src/db_inspector_mcp/tools.py`

---

## 2026-02-17 — Safe test teardown: never close a user's Access session

**Trigger**: Integration tests for the Access COM backend called `backend._app.Quit()` in their `finally` blocks for cleanup. If a test ran while the user had Access open with a database, and the backend attached to the user's instance via `GetObject` (instead of creating a new one), the teardown would quit the user's Access session — closing their work in progress.

The production code already follows the ownership principle: `_release_app()` only sets `self._app = None` and never calls `CloseCurrentDatabase()` or `Quit()`. The tests did not follow this principle.

**Dangerous pattern** (three integration tests):
```python
finally:
    if backend._app is not None:
        backend._app.Quit()  # Could quit the user's Access!
```

**Options explored**:
- **Always call `Quit()` on `backend._app`** (status quo) — dangerous. The backend may have attached to a user's existing instance via `GetObject(db_path)` rather than creating a new one. `Quit()` would close the user's session.
- **Never call `Quit()`, only release the reference** — safe but leaves orphaned Access instances from tests running indefinitely. Since `UserControl = True`, they wouldn't exit on their own.
- **Verify the instance has the test's temp DB before quitting (chosen)** — `_safe_quit_test_access(app, expected_db_path)` checks `app.CurrentDb().Name` against the test's temporary database path. If they match, it's an instance the test created for the temp DB — safe to quit. If they don't match, the instance belongs to the user — only the COM reference is released.

**Decision**: Added `_safe_quit_test_access()` helper to `tests/test_backends.py`. All three integration tests (`test_access_com_getobject_existing_database`, `test_access_com_with_closed_database`, `test_access_com_no_lock_between_operations`) now use this helper instead of direct `Quit()` calls. Added an "Access COM Test Safety" section to `CONTRIBUTING.md` documenting the principle and the helper.

**What this rules out**: Nothing. The helper is strictly safer than the previous pattern. If a test needs to create and control its own Access instance (not via the backend), it should use the same `_safe_quit_test_access()` helper rather than calling `Quit()` directly.

**Relevant files**:
- `tests/test_backends.py` — added `_safe_quit_test_access()`, updated all integration test teardowns
- `CONTRIBUTING.md` — added "Access COM Test Safety" section

---

## 2026-02-17 — Lazy backend initialization via MCP roots for user-level configs

**Trigger**: When the MCP server is configured at the user level (global Cursor settings) rather than the project level (`.cursor/mcp.json`), Cursor sets the working directory to the user's home folder (`C:\Users\akw`), not the open workspace. The server's `.env` search starts from CWD and walks upward, so it never finds the project's `.env` file. The server crashed at startup with "No database configuration found."

**Options explored**:
- **Rely on CWD** (status quo) — works for project-level configs where Cursor sets CWD to the workspace root. Confirmed broken for user-level configs via diagnostic logging added during the session.
- **`DB_MCP_PROJECT_DIR` env var only** — explicit override the user sets in their `mcp.json` `env` section. Works but not dynamic: the user must change it per project, defeating the point of a single user-level config.
- **MCP `roots/list` protocol call** — after the protocol handshake the server can ask the client for workspace folders. These are `file://` URIs pointing at the open workspace root(s). Fully dynamic but only available after the handshake, not at startup. Requires async code and a `Context` object, which is only available during tool calls.
- **IDE-specific env vars** (e.g., `CURSOR_WORKSPACE_FOLDER`) — speculative; no evidence Cursor sets such variables for MCP servers. Fragile and IDE-specific.
- **Lazy initialization via MCP roots on first tool call (chosen)** — don't crash at startup. On the first call to `db_list_databases` (which the MCP instructions already tell agents to call first), use `ctx.session.list_roots()` to discover the workspace, load `.env` from it, and initialize backends. `DB_MCP_PROJECT_DIR` kept as an explicit fallback.

**Decision**: Two-phase initialization. Phase 1 (startup): try to find `.env` from CWD as before. If it fails, log a message and continue — don't `sys.exit(1)`. Phase 2 (first tool call): `db_list_databases` is now `async` with a `Context` parameter. `_ensure_backends_initialized(ctx)` calls `ctx.session.list_roots()`, converts each `file://` URI to a local path, checks for `.env`, and calls the new `initialize_from_workspace()` to load it and register backends. A module-level `_lazy_init_attempted` flag ensures this runs at most once. The `BackendRegistry.get()` error message was improved to tell agents to call `db_list_databases()` first when no backends are registered.

Supporting changes: `with_logging` decorator updated to support async tool functions. `_verify_readonly()` extracted from `main()` so it can be called from the lazy-init path too. `_file_uri_to_path()` handles Windows `file:///C:/path` URIs. Diagnostic logging in `_load_env_files()` prints CWD, resolved project root, and which `.env` files were loaded to stderr.

**What this rules out**: Only `db_list_databases` triggers lazy init. If an agent calls another tool first (e.g., `db_list_tables`), it will get a clear error message directing it to call `db_list_databases()`. This aligns with the existing MCP instructions. If a future MCP SDK version exposes an `on_initialized` server hook, the lazy init could move there, removing the requirement that `db_list_databases` be called first.

**Relevant files**:
- `src/db_inspector_mcp/main.py` — no longer exits on `ValueError`; extracted `_verify_readonly()`
- `src/db_inspector_mcp/config.py` — added `initialize_from_workspace()`, `_load_env_from_directory()`, `DB_MCP_PROJECT_DIR` support, diagnostic logging
- `src/db_inspector_mcp/tools.py` — `db_list_databases` is now async with `Context`; added `_ensure_backends_initialized()`, `_file_uri_to_path()`
- `src/db_inspector_mcp/usage_logging.py` — `with_logging` supports async functions
- `src/db_inspector_mcp/backends/registry.py` — improved empty-registry error message
- `README.md` — new "User-Level MCP Configuration" section
- `.env.example` — documented `DB_MCP_PROJECT_DIR`
- `AGENTS.md` — added venv activation instructions for tests
- `tests/test_config.py` — 6 new tests for `_find_project_root`
- `tests/test_tools.py` — updated `test_db_list_databases_includes_dialect` for async

---

## 2026-02-12 — ROT enumeration for multi-instance Access discovery

**Trigger**: The previous fix for password-protected databases replaced `GetObject(db_path)` (which triggers a password dialog) with `GetObject(None, "Access.Application")`. However, `GetObject(None, ...)` only returns whichever instance the Running Object Table (ROT) hands back first. If the user has 5 Access instances open and our password-protected database is in one of them, there's an ~80% chance we get the wrong instance. We'd then create a 6th instance and try to `OpenCurrentDatabase` again, potentially causing locking/concurrency issues (shared mode instead of exclusive).

**Design constraint**: `GetObject(db_path)` is the only `GetObject` variant that reliably finds a specific instance in multi-instance scenarios. But for password-protected databases it triggers OLE moniker binding which opens the file without a password, causing a dialog. We need the reliability of file-path-based lookup without the moniker binding side effect.

**Key insight**: `IRunningObjectTable::GetObject(moniker)` only checks the ROT — if the moniker isn't registered, it throws `MK_E_UNAVAILABLE` and does NOT fall through to moniker binding. We can use `pythoncom.CreateFileMoniker(path)` + `rot.GetObject(moniker)` to safely probe for our database without any risk of opening the file or showing a dialog.

**Options explored**:
- **`GetObject(None, "Access.Application")` (previous approach)** — rejected: only finds one arbitrary instance from the ROT. Unreliable with multiple Access windows.
- **Check `.laccdb` file existence before `GetObject(db_path)`** — rejected: race-prone, doesn't indicate which instance has the file, and `.laccdb` may exist due to other locking (not necessarily Access).
- **Timeout wrapper around `GetObject(db_path)`** — rejected: Win32 COM doesn't support timeouts on `GetObject`. Thread-based workarounds are fragile.
- **Two-tier ROT enumeration (chosen)** — Tier 1: direct file moniker lookup via `CreateFileMoniker` + `rot.GetObject` (~1 ms, safe, no binding). Tier 2: enumerate all ROT entries via `EnumRunning()`, QI each for IDispatch, call `CurrentDb()`, compare against our path (~10-50 ms). Finds the right instance regardless of how many Access windows are open.

**Decision**: Added `_find_existing_instance()` method to `AccessCOMBackend` using `pythoncom.GetRunningObjectTable`, `CreateFileMoniker`, and `EnumRunning`. `_acquire_password_protected` now calls this instead of `GetObject(None, "Access.Application")`. The non-password path (`_acquire_for_open_db`) continues to use `GetObject(db_path)` since it's safe and reliable for non-password databases.

**What this rules out**: Nothing permanent. If Tier 2 proves too slow (unlikely with a handful of Access instances), it could be gated behind a flag or timeout. The `pythoncom` import is already available via `pywin32>=306` and is conditionally imported alongside `pywintypes` and `win32com.client`.

**Relevant files**: `src/db_inspector_mcp/backends/access_com.py`

---

## 2026-02-12 — Fix password dialog and auto-close for COM Application acquisition

**Trigger**: Two issues observed when `db_list_databases` triggered Application acquisition for a password-protected Access database:
1. **Password dialog**: `GetObject(db_path)` uses OLE moniker resolution to open the file. For password-protected databases, the moniker opens the file WITHOUT a password, causing Access to show a blocking password dialog — even though the password was already configured in the connection string via `PWD=`.
2. **Auto-close**: After the TTL timer released the COM reference (`self._app = None`), the Access instance created by `EnsureDispatch` exited automatically because `UserControl` defaults to `False` for programmatically-created instances. This violated the ownership principle (the user manages closing).

The first issue was previously identified in the benchmark script (see "Fix GetObject password prompt in benchmark script" below) but the production `_get_access_app()` was left unchanged under the assumption that users would typically already have the database open. That assumption doesn't hold for the `db_list_databases` cold-start case.

**Options explored**:

*For the password dialog:*
- **Timeout on GetObject(db_path)** — rejected: Win32 COM doesn't support timeouts on `GetObject`. Would require thread-based workaround that adds fragile complexity.
- **Check for .laccdb before GetObject** — rejected: race-prone and doesn't reliably indicate whether a specific Access instance has the file open.
- **Skip GetObject(db_path) when password is present (chosen)** — use `GetObject(None, "Access.Application")` to find an existing instance that already has our database open (user entered the password themselves), then fall back to `EnsureDispatch` + `OpenCurrentDatabase(path, False, password)`. This is less reliable than `GetObject(db_path)` in multi-instance scenarios (it returns whichever instance the ROT provides), but it's the only safe approach for password-protected databases.

*For the auto-close:*
- **Set `UserControl = True` on new instances (chosen)** — when `UserControl` is `True`, Access persists after all COM references are released, as if the user started it. When the TTL timer fires and drops `self._app`, Access remains open and visible. The user can close it manually. On the next tool call, `GetObject` re-acquires the reference cheaply (~10 ms).
- **Never release the reference** — rejected: defeats the TTL mechanism and prevents Access from exiting when the user closes it via the UI.

*Additional improvement:*
- **Open database as CurrentDb on new instances** — previously, when `EnsureDispatch` created a new instance, the database was NOT opened as CurrentDb. It was opened per-request via `DBEngine.OpenDatabase()` in `_open_dao_database()`, leaving the user with an empty Access window. Now, `OpenCurrentDatabase()` is called during acquisition, so the user always sees Access with the database open. This also means `_open_dao_database()` finds the database via `CurrentDb()` and returns `needs_close=False`, avoiding per-request open/close overhead.

**Decision**: Restructured `_get_access_app()` into two acquisition paths:
1. **`_acquire_password_protected(password)`** — skips `GetObject(db_path)`, checks existing instances via `GetObject(None, "Access.Application")`, creates new instance + `OpenCurrentDatabase(path, False, password)` if needed.
2. **`_acquire_for_open_db()`** — uses `GetObject(db_path)` (unchanged, reliable for non-password databases), creates new instance + `OpenCurrentDatabase(path, False)` if not found.

Both paths set `UserControl = True` and make newly created instances visible. The existing `_ensure_current_db()` method (used by `call_vba_function`) remains as a safety net.

**What this rules out**: For password-protected databases in multi-instance scenarios, we can no longer find the specific Access instance that has our database open — `GetObject(None, ...)` returns whichever instance the ROT provides. If it returns the wrong one, we create a new instance (user may see two Access windows). This is acceptable given the alternative (blocking password dialog).

**Relevant files**: `src/db_inspector_mcp/backends/access_com.py`

---

## 2026-02-12 — COM Application TTL to release .laccdb lock after inactivity

**Trigger**: Users reported that the `.laccdb` lock file persisted indefinitely after querying an Access database via the MCP tool, until the MCP server was disabled. The root cause was that the COM Application reference (`self._app`) was cached for the lifetime of the MCP server process. This prevented Access from fully exiting when the user closed it — COM reference counting kept the process alive (invisible), so the `.laccdb` lock was never released. The ODBC TTL (added earlier) only managed the ODBC connection; the COM Application reference was unmanaged.

**Design constraint**: `GetObject(db_path)` must be kept for Application discovery. It is the only reliable way to find the specific Access instance that has our database open when the user has multiple Access windows running with different databases. `GetObject(None, "Access.Application")` is unreliable in multi-instance scenarios because it returns whichever instance the ROT provides. We do not manipulate the Running Object Table directly.

**Ownership principle**: We never close the database or quit the Access application — that is the user's responsibility. Users may have opened the database in a special way (e.g., bypassing startup code with Shift) and we must not interfere with their session. Cleanup means releasing our COM reference only.

**Options explored**:
- **Database TTL (close CurrentDb on timer, keep Application alive)** — rejected: would close the user's database if they had it open. Disruptive and violates the ownership principle.
- **Full Application TTL (close database + quit Access on timer)** — rejected: would quit the user's Access. Even with ownership tracking (`_we_launched` flag), distinguishing "user had it open" from "GetObject launched it" is unreliable and adds fragile complexity.
- **COM reference TTL (release `self._app` on timer, never close/quit) (chosen)** — simplest approach. After a period of inactivity, set `self._app = None`. This drops Python's COM reference, which allows Access to exit normally when the user closes it. On the next tool call, `GetObject(db_path)` reattaches to the same instance in ~10 ms (if Access is still running) or falls through to `EnsureDispatch` for a cold start (~2-5 s).

**Decision**: TTL-based COM reference release, mirroring the ODBC TTL pattern:
1. **Timer infrastructure** added to `AccessCOMBackend`: `threading.Lock`, `threading.Timer`, configurable TTL (default 5 s, shared with the ODBC TTL via `DB_MCP_ACCESS_CONN_TTL`).
2. **After each COM operation** (`_dao_database` exit, `call_vba_function` exit), a daemon timer is scheduled. If another call arrives before it fires, the timer is cancelled and the cached reference is reused (burst-friendly). When the timer fires, `self._app` is set to `None` — nothing else.
3. **`_get_access_app()`** retains the original `GetObject(db_path)` → `EnsureDispatch` acquisition flow. A stale-reference check (`self._app.Name`) validates the cached reference before returning it. If the reference is stale (user closed Access), it is cleared and re-acquired.
4. **`_ensure_current_db()`** added as a safety net for `call_vba_function()`. If the Application was created via `EnsureDispatch` (no database open as CurrentDb), this calls `OpenCurrentDatabase()` before `Application.Run`. When connected to the user's instance via `GetObject(path)`, it is a no-op.
5. **`_open_dao_database()`** now verifies that `CurrentDb().Name` matches our database path before using it, preventing accidental queries against the wrong database.

**What this rules out**: Nothing permanent. The TTL is tunable (`DB_MCP_ACCESS_CONN_TTL=0` for immediate release after every operation). If a future scenario requires actively closing the database or quitting Access, the timer infrastructure is already in place — `_release_app()` can be extended.

**Relevant files**: `src/db_inspector_mcp/backends/access_com.py`

---

## 2026-02-12 — Enrich database discovery and metadata for large databases

**Trigger**: A production Access database had hundreds of tables and thousands of queries. Agents calling `db_list_tables`/`db_list_views` received massive result sets that consumed context window without adding value. Agents had no way to know the database was large before dumping everything.

**Benchmarking performed** (see `benchmarks/BENCH_COM_LEVELS_RESULTS.md`):
- Standalone `DAO.DBEngine.120` (in-process COM) is 33x faster than `Access.Application` (out-of-process) for metadata iteration due to RPC marshalling overhead.
- MSysObjects provides rich object-type metadata (forms, reports, macros, modules) but requires the Application context — standalone DAO and ODBC are denied read permission.
- `Nz()`, `DLookup()`, and other Access domain functions require the Application context; `IIf()` works everywhere.

**Options explored**:
- **Always auto-start Access for counts** — rejected: cold start is ~2.3s, too expensive for a discovery call.
- **Add summary guidance text in responses** — rejected: makes responses non-deterministic, which is problematic for multi-agent pipelines. Tool descriptions are the right place for behavioral guidance.
- **Always acquire Application for Access COM counts** — `get_object_counts()` goes through `_get_access_app()` and MSysObjects `GROUP BY Type` for a full inventory (tables, linked_tables, queries, forms, reports, macros, modules). Initially a two-tier approach was implemented (standalone DAO when Application not running) but real-world usage logs showed: (a) standalone DAO cold start was ~500ms per database, not the 20ms from warm benchmarks; (b) agents always follow `db_list_databases` with `list_tables`/`list_views` which need the Application anyway; (c) without full counts, agents can't reason about object types they haven't seen (e.g., forms). Front-loading the Application startup in `get_object_counts` is a net win for session performance.
- **Filtering via `name_filter`** — add an optional case-insensitive substring filter to `list_tables`/`list_views` so agents can search without dumping everything.

**Decisions**:
1. `get_object_counts()` added to `DatabaseBackend` base class (non-abstract, default returns nulls). Each backend overrides with the cheapest available path. `db_list_databases` response now includes `object_counts` with dialect-appropriate keys.
2. `list_tables`/`list_views` accept optional `name_filter` parameter across all backends. Access COM switched from TableDefs/QueryDefs iteration to MSysObjects SQL queries (supports filtering and returns richer type info). MSSQL/Postgres use `LIKE`/`ILIKE` in SQL. Tool descriptions guide agents to use filtering when counts exceed 200.
3. Stale COM reference detection added to `_get_access_app()` — validates cached `self._app` with a lightweight `self._app.Name` call before returning it. If the reference is disconnected, clears and re-acquires. `_dao_database()` retries once on COM disconnect.
4. Response schema is deterministic: `object_counts` is always present, but only includes keys the backend can actually measure. A key with value 0 means "we checked and found none"; a missing key means "we cannot determine this." This avoids null values that an agent could misinterpret as "zero objects of this type." No dynamic guidance text in responses.

**What this rules out**: Nothing permanent. The `name_filter` is substring-only (no wildcards/regex) — could be extended later if agents need more sophisticated patterns.

**Relevant files**: `src/db_inspector_mcp/backends/base.py`, `src/db_inspector_mcp/backends/access_com.py`, `src/db_inspector_mcp/backends/access_odbc.py`, `src/db_inspector_mcp/backends/mssql.py`, `src/db_inspector_mcp/backends/postgres.py`, `src/db_inspector_mcp/tools.py`

---

## 2026-02-12 — Contextual error hints for Access SQL failures

**Trigger**: When an AI agent sends a query with standard SQL syntax to an Access backend, the raw ODBC error (e.g., "missing operator") gives no guidance on what went wrong. Agents retry blindly, wasting multiple tool calls before stumbling onto the correct Access syntax. The error patterns and fixes were already documented but not surfaced at error time.

**Options explored**:
- **Pre-execution query transformation** — detect and rewrite standard SQL to Access syntax before executing. Rejected: too fragile, risks silently changing query semantics, and hard to cover all edge cases.
- **Separate error-handling middleware/decorator** — wrap each tool in a decorator that catches and enriches errors. Viable but adds indirection; the enrichment logic is small enough to not warrant a separate module.
- **Inline enrichment helper called in each tool's except block (chosen)** — a single `_enrich_access_error()` function in `tools.py` that pattern-matches the error message (and optionally the query text) against known Access failure patterns and appends a hint. Each tool's `except Exception` block calls it before returning the error dict.

**Decision**: Inline helper in `tools.py`. Five patterns are matched: missing-operator+JOIN (parenthesized JOINs), missing-operator+CASE (use IIF), syntax-error+LIMIT (use TOP N), LIKE-related errors (use * and ? wildcards), and missing-operator+DISTINCT (use GROUP BY). The helper is a no-op for non-Access dialects. This keeps the fix localized and easy to extend — adding a new pattern is one tuple in `_ACCESS_ERROR_HINTS`.

> **⚠ Partially superseded** (2026-02-27): The DISTINCT hint was removed (DISTINCT works fine; the errors were caused by unparenthesized JOINs, not DISTINCT itself). The wildcard hint was corrected — Access ODBC uses ANSI wildcards (`%` and `_`), not `*` and `?`. See "Correct Access SQL dialect guidance" entry above.

**What this rules out**: Nothing permanent. If the hint list grows large or needs to be shared with other modules (e.g., usage_logging pattern detection), it could be extracted to a shared module. The current approach is sufficient for the 5 known patterns.

**Relevant files**: `src/db_inspector_mcp/tools.py`

---

## 2026-02-12 — Added "distinct" topic to db_sql_help for Access

> **⚠ Superseded** (2026-02-27): The premise of this entry — that SELECT DISTINCT is unreliable in Access — was empirically disproven by testing 10 query scenarios against live Access databases via ODBC. The topic was rewritten to say DISTINCT works fine and to point agents to JOIN parenthesization as the likely cause of errors. See "Correct Access SQL dialect guidance" entry above.

**Trigger**: Real-world usage showed that `SELECT DISTINCT` is unreliable in Access and agents don't know to use `GROUP BY` instead. The `db_sql_help` tool already had 8 Access-specific topics but was missing this one.

**Options explored**:
- **Add to "all" summary only** — minimal change, but agents calling `db_sql_help("distinct")` would get a "topic not found" error. Not helpful.
- **Full topic entry with examples (chosen)** — added a `"distinct"` key to `_SQL_HELP["access"]` with three examples (the failing DISTINCT, the GROUP BY fix, multi-column), plus an entry in the `"all"` summary. Also referenced from the new error hint for DISTINCT failures.

**Decision**: Full topic entry. Follows the existing pattern for other topics (title, description, examples list, pattern string). Updated the docstring and Args to list the new topic. One of the contextual error hints (task 1) now directs agents to `db_sql_help('distinct')`.

**What this rules out**: Nothing. More topics can be added following the same pattern.

**Relevant files**: `src/db_inspector_mcp/tools.py`

---

## 2026-02-12 — Three-layer agent discoverability for DECISIONS.md

**Trigger**: `DECISIONS.md` existed but had no guaranteed discovery path. AI agents making architectural changes would not reliably find it before proceeding. Needed a way for agents across different tools (Cursor, Claude Code) and human contributors to discover this file automatically.

**Options explored**:
- **README reference only** — a line in the Contributing or Development section. Humans would find it, but AI agents skim the README for task-relevant sections and would likely miss a reference buried at the bottom. Not reliable for the primary goal.
- **`AGENTS.md` only** — Claude Code reads this file automatically. But Cursor does not — Cursor agents would miss it entirely. Covers one tool ecosystem but not the other.
- **`.cursor/rules/` only** — Cursor auto-injects these into agent context. But Cursor rules are Cursor-specific; Claude Code and other tools ignore them. Also, duplicating the guidance in both a rule and `AGENTS.md` creates a maintenance burden.
- **Cursor rule → `AGENTS.md` → `DECISIONS.md` (chosen)** — the Cursor rule is a lightweight pointer to `AGENTS.md`, which is the single source of truth for agent guidance. `AGENTS.md` then directs agents to read `DECISIONS.md` before architectural changes. No duplication; one file to maintain.

**Decision**: Three-layer approach. `AGENTS.md` is the authoritative agent instruction file, containing the directive to read `DECISIONS.md` and guidance on documenting new decisions. A `.cursor/rules/agents.mdc` rule (always-apply) points Cursor agents to `AGENTS.md`. The README Development section references both files for human contributors. This covers Cursor agents (rule → AGENTS.md), Claude Code agents (AGENTS.md directly), and humans (README).

**What this rules out**: Agents using tools that read neither `AGENTS.md` nor `.cursor/rules/` won't get the guidance automatically. The README reference is the fallback for those cases. If a new tool convention emerges (e.g., `.ai/` config), a new pointer could be added without changing the content in `AGENTS.md`.

**Relevant files**:
- `AGENTS.md` — created; authoritative agent guidance
- `.cursor/rules/agents.mdc` — created; always-apply rule pointing to `AGENTS.md`
- `README.md` — added reference in Development section; fixed duplicate Feature bullets

---

## 2026-02-12 — Skip COM TTL caching; revisit later

**Trigger**: After implementing ODBC TTL caching, explored whether the Access COM backend should also have TTL-based lifecycle management for the Application object and/or DAO database references.

**Options explored**:
- **Database TTL only** — keep the Access Application alive but call `CloseCurrentDatabase()` after idle. Releases the `.laccdb` lock while keeping Access warm for fast restart (~500ms vs ~2-5s cold launch). Best balance of safety and performance.
- **Application + Database TTL** — also release the Access Application after longer idle. Full cleanup but expensive restart. Mitigates risks of stale COM references, modal dialog blocking, and memory footprint.
- **Skip COM TTL for now** — leave the COM backend as-is (Application cached indefinitely, DAO per-request). The ODBC TTL already covers the SQL execution path since `AccessCOMBackend` delegates SQL to its internal `AccessODBCBackend`.

**Decision**: Skip COM TTL for now. The ODBC TTL fix already benefits both `access_odbc` and `access_com` backends' SQL operations. The COM Application is already cached indefinitely and the DAO per-request pattern for metadata (list_tables, list_views) is fast enough (~10-20ms). COM TTL adds complexity (stale reference detection, modal dialog handling, Access single-database constraint) for limited additional gain.

**What this rules out**: Nothing permanently. COM TTL can be added later if users report issues with stale COM references, Access memory footprint, or lock contention. The trigger to revisit would be: (a) moving scalar SQL execution to DAO/OpenRecordset (which would hold the database open longer), or (b) reports of the MCP server hanging due to Access modal dialogs.

**Relevant files**: `src/db_inspector_mcp/backends/access_com.py`

---

## 2026-02-12 — TTL-cached ODBC connections for Access backend (5-second default)

**Trigger**: Benchmarking showed Access ODBC connect-per-request costs ~220ms per call, purely in connection overhead. During a typical MCP conversation the LLM fires 3-10 tool calls in quick succession, wasting 660-2200ms on reconnections. A persistent ODBC connection delivers 0.2ms queries but holds the `.laccdb` lock indefinitely, blocking other users.

**Options explored**:
- **Connect-per-request** (status quo) — safe (lock released immediately) but ~220ms overhead per call. This was the original pattern, chosen to avoid lock contention.
- **Persistent connection** (like MSSQL/Postgres backends) — fastest (0.2ms) but holds `.laccdb` lock for the lifetime of the MCP server. Not acceptable for Access where users need to open the database concurrently.
- **TTL-cached connection with `threading.Timer`** — cache the connection after use, schedule a timer to close it after N seconds of inactivity. Gets near-persistent performance during bursts, releases lock when idle.

**Decision**: TTL-cached connection with 5-second default. After each operation completes, a daemon `threading.Timer` is scheduled to close the connection. If a new call arrives before the timer fires, the timer is cancelled and the connection is reused. Thread safety via `threading.Lock`. Stale connections (raising `pyodbc.Error`) are automatically discarded. TTL=0 disables caching (connect-per-request fallback).

**What this rules out**: The 5-second window means the `.laccdb` lock is held for up to 5 seconds after the last MCP call. Users who need immediate lock release can set `DB_MCP_ACCESS_CONN_TTL=0` in their `.env`. If 5 seconds proves too long or too short, it's tunable without code changes.

**Relevant files**:
- `src/db_inspector_mcp/backends/access_odbc.py` — core TTL logic in `_connection()`, `_schedule_close()`, `_close_connection_on_timer()`, `_discard_connection()`
- `src/db_inspector_mcp/backends/access_com.py` — updated `__init__` to forward `connection_ttl_seconds` to internal ODBC backend
- `src/db_inspector_mcp/config.py` — added `_get_access_conn_ttl()` reading `DB_MCP_ACCESS_CONN_TTL` env var, wired into `_create_backend()` for both Access backends
- `tests/test_backends.py` — 7 new tests: TTL defaults, custom TTL, TTL=0 fallback, connection reuse, timer expiry, stale connection discard, non-pyodbc error handling

---

## 2026-02-12 — DAO vs ODBC benchmark results and hybrid strategy recommendation

**Trigger**: Previous benchmarks identified ~220ms ODBC connection overhead. User asked whether COM/DAO should replace ODBC entirely for Access, and specifically requested benchmarking `CurrentDb.Execute` with `dbFailOnError`.

**Options explored**:
- **ODBC for everything** (status quo) — portable, works without Access installed, but ~220ms connect-per-request overhead dominates.
- **DAO for everything** — eliminates ODBC overhead for scalar queries (COUNT: 12.9ms vs 232.8ms). But row iteration through COM is extremely slow due to per-field marshalling (SELECT TOP 10: 863ms vs 222ms for ODBC). Not viable as a full replacement.
- **Hybrid: DAO for scalars, ODBC for row sets** — DAO via `CurrentDb + OpenRecordset` for COUNT/SUM/metadata (~10-13ms). ODBC (ideally persistent/pooled) for multi-row results where bulk fetch is faster. `Database.Execute` with `dbFailOnError` for action queries (~0.9ms).

**Decision**: Recommended hybrid approach based on benchmark data, but deferred implementation. The immediate win (TTL connection caching) was implemented first since it benefits all operations without changing the execution path. The hybrid DAO+ODBC architecture remains a future optimization.

Key benchmark numbers (30 iterations, median):
- ODBC connect-per-request COUNT(*): 232.8ms
- ODBC persistent COUNT(*): 0.2ms
- DAO CurrentDb COUNT(*): 12.9ms (cached ref: 6.0ms)
- DAO SELECT TOP 10: 863.3ms (ODBC is 4x faster here)
- DAO Execute INSERT: 0.9ms
- DAO Execute CREATE+DROP: 3.3ms

**What this rules out**: Nothing. The hybrid approach is additive — it would layer DAO scalar execution on top of the existing ODBC path. The TTL caching already narrows the gap significantly for burst workloads.

**Relevant files**: `benchmarks/bench_dao_vs_odbc.py`

---

## 2026-02-12 — Fix GetObject password prompt in benchmark script

**Trigger**: The `bench_dao_vs_odbc.py` benchmark script hung indefinitely when run against a password-protected Access database. Access was showing a password dialog, blocking the script.

**Options explored**:
- **Pass password to `OpenCurrentDatabase`** — correct approach, but the script was calling `GetObject(db_path)` first, which triggered Windows to launch Access and open the file *without* a password via OLE moniker resolution, before `OpenCurrentDatabase` was ever reached.
- **Use `DBEngine.OpenDatabase` with connect string** — works for DAO but doesn't set up `CurrentDb()` for subsequent operations.
- **Skip `GetObject(db_path)` entirely** — go straight to `EnsureDispatch("Access.Application")` then `OpenCurrentDatabase(path, False, password)`. This avoids the moniker-based file open that triggers the password dialog.

**Decision**: Skip `GetObject(db_path)` in the benchmark script. Create Access via `EnsureDispatch`, then open the database explicitly with `OpenCurrentDatabase(db_path, False, password)`. The production code (`access_com.py`) uses `GetObject` successfully because users typically already have the database open (password already entered). The benchmark starts from scratch with no Access running, so `GetObject` with a password-protected file triggers the dialog.

**What this rules out**: The benchmark script can't automatically attach to an already-open Access instance. This is acceptable since the benchmark needs a controlled, fresh environment anyway. The production `_get_access_app()` in `access_com.py` is unchanged — it still tries `GetObject` first, which works when the user has the database open.

**Relevant files**: `benchmarks/bench_dao_vs_odbc.py`
