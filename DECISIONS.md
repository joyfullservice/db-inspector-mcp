# log-decision

> This file is a reverse-chronological journal of architectural and strategic
> decisions made during development. It is maintained by AI agents at the end
> of each working session and intended to be consumed by both humans and AI
> agents for future context. Agents should read this file before beginning
> work on any module referenced here. Newest entries are at the top.

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

**Trigger**: The `bench_dao_vs_odbc.py` benchmark script hung indefinitely when run against the password-protected `SecTbl.accdb` database. Access was showing a password dialog, blocking the script.

**Options explored**:
- **Pass password to `OpenCurrentDatabase`** — correct approach, but the script was calling `GetObject(db_path)` first, which triggered Windows to launch Access and open the file *without* a password via OLE moniker resolution, before `OpenCurrentDatabase` was ever reached.
- **Use `DBEngine.OpenDatabase` with connect string** — works for DAO but doesn't set up `CurrentDb()` for subsequent operations.
- **Skip `GetObject(db_path)` entirely** — go straight to `EnsureDispatch("Access.Application")` then `OpenCurrentDatabase(path, False, password)`. This avoids the moniker-based file open that triggers the password dialog.

**Decision**: Skip `GetObject(db_path)` in the benchmark script. Create Access via `EnsureDispatch`, then open the database explicitly with `OpenCurrentDatabase(db_path, False, password)`. The production code (`access_com.py`) uses `GetObject` successfully because users typically already have the database open (password already entered). The benchmark starts from scratch with no Access running, so `GetObject` with a password-protected file triggers the dialog.

**What this rules out**: The benchmark script can't automatically attach to an already-open Access instance. This is acceptable since the benchmark needs a controlled, fresh environment anyway. The production `_get_access_app()` in `access_com.py` is unchanged — it still tries `GetObject` first, which works when the user has the database open.

**Relevant files**: `benchmarks/bench_dao_vs_odbc.py`

**Commits**:
