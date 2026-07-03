# Cursor shared MCP process — wrong-project resolution

## Problem

When db-inspector-mcp is configured at **user level** (`~/.cursor/mcp.json`) and multiple Cursor windows are open, Cursor may run the server in a **`[Shared MCP process]`** that multiplexes all windows onto one stdio session.

Symptoms:

- `db_list_databases()` shows backends from another project
- `workspace_root` in the response points at a sibling repo
- stderr logs: `Selected workspace root: C:\Repos\other-project` while working in a different folder

Root cause: **`roots/list` returns one window's workspace for all callers** in shared mode. The server cannot distinguish windows over stdio.

## Spike conclusion (2026-06-23): HTTP does not help

We tested whether streamable-HTTP gives Cursor a distinct MCP session per window, reading the transport-level `mcp-session-id` header plus a GC-safe per-session serial. Across **three** Cursor windows over HTTP:

- **Same `mcp_session_id`** (`ae605222…`) for every window
- **Same `session_serial`** (`93d2c8fe…`), `live_session_count: 1`
- `roots/list` returned the **union of all open windows' folders** to every window

Cursor collapses all windows onto **one MCP session over both stdio and HTTP**. There is no client- or transport-level per-window identifier. **Agent-supplied `workspace_root` is the only reliable fix.** The HTTP transport plumbing was removed after this spike (no benefit, extra maintenance). `db_debug_session` and session-identity primitives remain for diagnostics; **stdio is the only supported transport**.

## Immediate workaround (stdio)

1. Call `db_list_databases()` first and confirm `workspace_root` matches your open project.
2. If it does not, pass **`workspace_root`** on every tool call with your Cursor **Workspace Path** (the folder containing `.env`).

Example:

```json
{
  "workspace_root": "C:\\Repos\\db-if-portal-sync"
}
```

3. Prefer backend names from `db_list_databases()` (e.g. `offline`, not `Purple_Offline`).

## HTTP transport spike (diagnostics only — already concluded)

> **Result:** The spike is complete and showed HTTP does **not** isolate windows
> (see "Spike conclusion" above). The HTTP transport code was removed from the
> server. These steps remain as a historical reference; re-running the spike
> would require re-adding HTTP support. For normal use, use stdio + `workspace_root`.

To re-test whether **streamable-HTTP** gives Cursor a distinct MCP session per window:

**Important:** With a `url` entry in `mcp.json`, Cursor connects to an **already-running** server — it does **not** spawn one (unlike stdio `command`). You must start the server in a terminal first and leave it running.

### 1. Start the server manually (keep this terminal open)

```powershell
cd C:\Repos\db-inspector-mcp
$env:DB_MCP_TRANSPORT = "http"
$env:DB_MCP_SPIKE_LOGGING = "true"
uv run python -m db_inspector_mcp.main
```

Wait until stderr shows:

```
Starting streamable-HTTP on http://127.0.0.1:8765/mcp
```

Default URL: `http://127.0.0.1:8765/mcp`

### 2. Point Cursor at HTTP (backup stdio config first)

In `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "db-inspector-mcp": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

Restart Cursor (or reload MCP servers) **after** the HTTP server is running.

### Troubleshooting `ERR_CONNECTION_REFUSED`

| Cause | Fix |
|-------|-----|
| Server not started | Run step 1 above; leave the terminal open |
| Server exited on startup | Check stderr for bind errors; ensure port 8765 is free |
| Wrong URL in mcp.json | Must match stderr, e.g. `http://127.0.0.1:8765/mcp` |
| Cursor started before server | Start server first, then reload MCP in Cursor |

### 3. Two-window test

1. Open two Cursor windows on different projects (each with its own `.env`).
2. In each window, ask the agent to call `db_debug_session()` and `db_list_databases()`.
3. Inspect `%USERPROFILE%\.db-inspector-mcp\logs\spike.jsonl`.

### Decision criteria

Compare `db_debug_session()` output across the two windows. Use **`mcp_session_id`**
(transport header) and **`session_serial`** (GC-safe per-session id) — **not**
the raw `session_id`, which is an `id()` memory address that can be recycled
after GC and produce false "same session" matches.

| Outcome | Interpretation | Next step |
|--------|----------------|-----------|
| Different `mcp_session_id` **and/or** different `session_serial`, `live_session_count` >= 2, correct `roots/list` per window | Cursor gives each window its own MCP session over HTTP | Resolve per session (key cache on `mcp_session_id`), fetch `roots/list` per session — no agent param needed |
| Same `session_serial` and `live_session_count` == 1 (or same `mcp_session_id`) | Sessions are shared/collapsed even on HTTP | Keep using **`workspace_root`** on tool calls (agent-supplied path) |

> The first spike used `id(ctx.session)` compared across calls 70s apart, which
> is unreliable (address reuse). `session_serial` + `live_session_count` fix
> that; `mcp_session_id` is Cursor's own per-connection token.

## Diagnostics

| Log | Purpose |
|-----|---------|
| `~/.db-inspector-mcp/logs/resolution.jsonl` | Every workspace resolution (durable) |
| `~/.db-inspector-mcp/logs/spike.jsonl` | HTTP spike / `DB_MCP_SPIKE_LOGGING=true` |
| `~/.db-inspector-mcp/logs/usage.jsonl` | Tool call outcomes |
| Cursor → MCP server stderr | Live probe order and selected root |

Tools:

- **`db_list_databases`** — returns `workspace_root`, `resolved_via`, `session_id`
- **`db_debug_session`** — full session/roots snapshot for debugging

## Launch-time pins (single-project dev)

When running one server for one project:

```powershell
$env:DB_MCP_PROJECT_DIR = "C:\Repos\db-if-portal-sync"
uv run python -m db_inspector_mcp.main
```

This does **not** fix multi-window shared stdio by itself; use `workspace_root` on calls.

## Upstream report (Cursor forum)

When filing a bug, include:

1. Evidence of `[Shared MCP process]` in Cursor MCP logs
2. stderr showing wrong `roots/list` root for the active window
3. `resolution.jsonl` or `spike.jsonl` lines with mismatched `client_roots` vs expected workspace
4. Note: `cursor.agent.legacyMcpMode` did not disable shared-process behavior in testing

Suggested title: *User-level stdio MCP server returns wrong window's workspace root in multi-window setup*
