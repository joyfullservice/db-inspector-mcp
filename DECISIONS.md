# log-decision

> This file is a reverse-chronological journal of architectural and strategic
> decisions made during development. It is maintained by AI agents at the end
> of each working session and intended to be consumed by both humans and AI
> agents for future context. Agents should read this file before beginning
> work on any module referenced here. Newest entries are at the top.

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

**Commits**:

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

**What this rules out**: Nothing. Instructions can be further refined as real external agent usage patterns emerge. The current transcripts were primarily development sessions, so these changes are based on code analysis rather than observed agent failures.

**Relevant files**: `src/db_inspector_mcp/tools.py`

**Commits**:

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

**Commits**:

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

**What this rules out**: Nothing permanent. If the hint list grows large or needs to be shared with other modules (e.g., usage_logging pattern detection), it could be extracted to a shared module. The current approach is sufficient for the 5 known patterns.

**Relevant files**: `src/db_inspector_mcp/tools.py`

**Commits**:

---

## 2026-02-12 — Added "distinct" topic to db_sql_help for Access

**Trigger**: Real-world usage showed that `SELECT DISTINCT` is unreliable in Access and agents don't know to use `GROUP BY` instead. The `db_sql_help` tool already had 8 Access-specific topics but was missing this one.

**Options explored**:
- **Add to "all" summary only** — minimal change, but agents calling `db_sql_help("distinct")` would get a "topic not found" error. Not helpful.
- **Full topic entry with examples (chosen)** — added a `"distinct"` key to `_SQL_HELP["access"]` with three examples (the failing DISTINCT, the GROUP BY fix, multi-column), plus an entry in the `"all"` summary. Also referenced from the new error hint for DISTINCT failures.

**Decision**: Full topic entry. Follows the existing pattern for other topics (title, description, examples list, pattern string). Updated the docstring and Args to list the new topic. One of the contextual error hints (task 1) now directs agents to `db_sql_help('distinct')`.

**What this rules out**: Nothing. More topics can be added following the same pattern.

**Relevant files**: `src/db_inspector_mcp/tools.py`

**Commits**:

---

## 2025-02-12 — Three-layer agent discoverability for DECISIONS.md

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

**Commits**:

---

## 2025-02-12 — Skip COM TTL caching; revisit later

**Trigger**: After implementing ODBC TTL caching, explored whether the Access COM backend should also have TTL-based lifecycle management for the Application object and/or DAO database references.

**Options explored**:
- **Database TTL only** — keep the Access Application alive but call `CloseCurrentDatabase()` after idle. Releases the `.laccdb` lock while keeping Access warm for fast restart (~500ms vs ~2-5s cold launch). Best balance of safety and performance.
- **Application + Database TTL** — also release the Access Application after longer idle. Full cleanup but expensive restart. Mitigates risks of stale COM references, modal dialog blocking, and memory footprint.
- **Skip COM TTL for now** — leave the COM backend as-is (Application cached indefinitely, DAO per-request). The ODBC TTL already covers the SQL execution path since `AccessCOMBackend` delegates SQL to its internal `AccessODBCBackend`.

**Decision**: Skip COM TTL for now. The ODBC TTL fix already benefits both `access_odbc` and `access_com` backends' SQL operations. The COM Application is already cached indefinitely and the DAO per-request pattern for metadata (list_tables, list_views) is fast enough (~10-20ms). COM TTL adds complexity (stale reference detection, modal dialog handling, Access single-database constraint) for limited additional gain.

**What this rules out**: Nothing permanently. COM TTL can be added later if users report issues with stale COM references, Access memory footprint, or lock contention. The trigger to revisit would be: (a) moving scalar SQL execution to DAO/OpenRecordset (which would hold the database open longer), or (b) reports of the MCP server hanging due to Access modal dialogs.

**Relevant files**: `src/db_inspector_mcp/backends/access_com.py`

**Commits**:

---

## 2025-02-12 — TTL-cached ODBC connections for Access backend (5-second default)

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

**Commits**:

---

## 2025-02-12 — DAO vs ODBC benchmark results and hybrid strategy recommendation

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

**Commits**:

---

## 2025-02-12 — Fix GetObject password prompt in benchmark script

**Trigger**: The `bench_dao_vs_odbc.py` benchmark script hung indefinitely when run against a password-protected Access database. Access was showing a password dialog, blocking the script.

**Options explored**:
- **Pass password to `OpenCurrentDatabase`** — correct approach, but the script was calling `GetObject(db_path)` first, which triggered Windows to launch Access and open the file *without* a password via OLE moniker resolution, before `OpenCurrentDatabase` was ever reached.
- **Use `DBEngine.OpenDatabase` with connect string** — works for DAO but doesn't set up `CurrentDb()` for subsequent operations.
- **Skip `GetObject(db_path)` entirely** — go straight to `EnsureDispatch("Access.Application")` then `OpenCurrentDatabase(path, False, password)`. This avoids the moniker-based file open that triggers the password dialog.

**Decision**: Skip `GetObject(db_path)` in the benchmark script. Create Access via `EnsureDispatch`, then open the database explicitly with `OpenCurrentDatabase(db_path, False, password)`. The production code (`access_com.py`) uses `GetObject` successfully because users typically already have the database open (password already entered). The benchmark starts from scratch with no Access running, so `GetObject` with a password-protected file triggers the dialog.

**What this rules out**: The benchmark script can't automatically attach to an already-open Access instance. This is acceptable since the benchmark needs a controlled, fresh environment anyway. The production `_get_access_app()` in `access_com.py` is unchanged — it still tries `GetObject` first, which works when the user has the database open.

**Relevant files**: `benchmarks/bench_dao_vs_odbc.py`

**Commits**:
