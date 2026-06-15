"""Per-workspace backend registry management."""

from __future__ import annotations

import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from .backends.registry import BackendRegistry
from .config import (
    build_registry_from_env,
    config_from_env,
    env_files_changed,
    parse_workspace_env,
    record_env_mtimes,
)
from .readonly import verify_readonly_for_registry

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context


def _root_uri_to_path(uri: str) -> Path | None:
    """Convert an MCP workspace root URI or path to a local Path."""
    if not uri or not isinstance(uri, str):
        return None

    text = uri.strip()
    if not text:
        return None

    if text.lower().startswith("file:"):
        parsed = urlparse(text)
        if parsed.scheme != "file":
            return None
        raw_path = unquote(parsed.path)
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        return Path(raw_path)

    if len(text) >= 3 and text[1] == ":" and text[2] in ("\\", "/"):
        return Path(text)

    if text.startswith("\\\\") or text.startswith("/"):
        return Path(text)

    return None


def _normalize_root_uri_for_mcp(uri: str) -> str:
    """Convert bare filesystem paths to ``file://`` URIs for MCP validation."""
    text = uri.strip()
    if not text:
        return text
    if text.lower().startswith("file:"):
        return text
    path = _root_uri_to_path(text)
    if path is None:
        return text
    try:
        return path.resolve().as_uri()
    except OSError:
        return text


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    """Return de-duplicated resolved paths preserving order."""
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def _paths_from_pydantic_validation_error(exc: BaseException) -> list[Path]:
    """Extract workspace paths from a Pydantic ``ValidationError``."""
    if not isinstance(exc, ValidationError):
        return []

    paths: list[Path] = []
    for err in exc.errors():
        val = err.get("input")
        if isinstance(val, str):
            candidate = _root_uri_to_path(val)
            if candidate is not None:
                paths.append(candidate)
    return _dedupe_paths(paths)


def _paths_from_list_roots_validation_error(exc: Exception) -> list[Path]:
    """Recover workspace paths when the MCP SDK rejects malformed root URIs."""
    paths = _paths_from_pydantic_validation_error(exc)
    if paths:
        return paths

    message = str(exc)
    if "ListRootsResult" not in message:
        return []

    recovered: list[Path] = []
    for match in re.finditer(r"input_value='((?:\\.|[^'])*)'", message):
        candidate = _root_uri_to_path(match.group(1))
        if candidate is not None:
            recovered.append(candidate)
    return _dedupe_paths(recovered)


def _paths_from_raw_list_roots(raw: dict[str, Any]) -> list[Path]:
    """Parse workspace paths from an unvalidated ``roots/list`` response."""
    roots = raw.get("roots")
    if not isinstance(roots, list):
        return []

    paths: list[Path] = []
    for root in roots:
        uri = ""
        if isinstance(root, dict):
            uri = str(root.get("uri", ""))
        else:
            uri = str(getattr(root, "uri", ""))
        candidate = _root_uri_to_path(uri)
        if candidate is not None:
            paths.append(candidate)
    return _dedupe_paths(paths)


async def _fetch_list_roots_raw(ctx: Context) -> dict[str, Any] | None:
    """Send ``roots/list`` and return the raw JSON result without validation."""
    import anyio
    from mcp import types
    from mcp.shared.exceptions import McpError
    from mcp.shared.message import SessionMessage
    from mcp.types import JSONRPCMessage, JSONRPCRequest

    session = ctx.session
    request_id = session._request_id
    session._request_id = request_id + 1

    response_stream, response_stream_reader = anyio.create_memory_object_stream(1)
    session._response_streams[request_id] = response_stream

    try:
        request_data = types.ServerRequest(
            types.ListRootsRequest(),
        ).model_dump(by_alias=True, mode="json", exclude_none=True)
        jsonrpc_request = JSONRPCRequest(jsonrpc="2.0", id=request_id, **request_data)
        await session._write_stream.send(
            SessionMessage(message=JSONRPCMessage(jsonrpc_request)),
        )

        timeout = None
        if session._session_read_timeout_seconds is not None:
            timeout = session._session_read_timeout_seconds.total_seconds()

        try:
            with anyio.fail_after(timeout):
                response_or_error = await response_stream_reader.receive()
        except TimeoutError:
            print("Timed out waiting for roots/list response", file=sys.stderr)
            return None

        from mcp.types import JSONRPCError

        if isinstance(response_or_error, JSONRPCError):
            raise McpError(response_or_error.error)

        result = response_or_error.result
        if isinstance(result, dict):
            return result
        return None
    except Exception as exc:
        print(f"Could not fetch raw workspace roots: {exc}", file=sys.stderr)
        return None
    finally:
        session._response_streams.pop(request_id, None)
        await response_stream.aclose()
        await response_stream_reader.aclose()


async def _list_workspace_root_paths(ctx: Context) -> list[Path]:
    """Resolve workspace root paths, normalizing bare client paths when needed."""
    raw = await _fetch_list_roots_raw(ctx)
    if raw is not None:
        normalized_roots: list[dict[str, Any]] = []
        for root in raw.get("roots", []):
            if not isinstance(root, dict):
                continue
            uri = str(root.get("uri", ""))
            normalized = dict(root)
            normalized["uri"] = _normalize_root_uri_for_mcp(uri)
            normalized_roots.append(normalized)

        if normalized_roots:
            from mcp.types import ListRootsResult

            try:
                result = ListRootsResult.model_validate({"roots": normalized_roots})
                paths = _dedupe_paths(
                    [
                        path
                        for root in result.roots
                        if (path := _root_uri_to_path(str(root.uri))) is not None
                    ],
                )
                if paths:
                    print(
                        f"Workspace roots from client (normalized): {len(paths)} root(s)",
                        file=sys.stderr,
                    )
                    for path in paths:
                        print(
                            f"  - {path} (.env exists: {(path / '.env').exists()})",
                            file=sys.stderr,
                        )
                    return paths
            except ValidationError as exc:
                print(
                    "Normalized roots/list response still failed validation; "
                    "falling back to path extraction",
                    file=sys.stderr,
                )
                recovered = _paths_from_pydantic_validation_error(exc)
                if recovered:
                    return recovered

        direct_paths = _paths_from_raw_list_roots(raw)
        if direct_paths:
            print(
                f"Workspace roots from raw response: {len(direct_paths)} root(s)",
                file=sys.stderr,
            )
            for path in direct_paths:
                print(
                    f"  - {path} (.env exists: {(path / '.env').exists()})",
                    file=sys.stderr,
                )
            return direct_paths

    try:
        roots_result = await ctx.session.list_roots()
        paths = _dedupe_paths(
            [
                path
                for root in roots_result.roots
                if (path := _root_uri_to_path(str(root.uri))) is not None
            ],
        )
        if paths:
            print(
                f"Workspace roots from client: {len(paths)} root(s)",
                file=sys.stderr,
            )
            for path in paths:
                print(
                    f"  - {path} (.env exists: {(path / '.env').exists()})",
                    file=sys.stderr,
                )
        return paths
    except Exception as exc:
        print(f"Could not request workspace roots from client: {exc}", file=sys.stderr)
        recovered = _paths_from_list_roots_validation_error(exc)
        if recovered:
            print(
                f"Recovered {len(recovered)} workspace path(s) from client error: "
                f"{', '.join(str(p) for p in recovered)}",
                file=sys.stderr,
            )
            for path in recovered:
                print(
                    f"  - {path} (.env exists: {(path / '.env').exists()})",
                    file=sys.stderr,
                )
            return recovered
        return []


def _get_server_project_root() -> Path:
    """Return the installed/editable project root for this MCP server package."""
    return Path(__file__).resolve().parents[2]


def _is_own_source_dir(path: Path) -> bool:
    """Return True if path is this server's own source repository."""
    try:
        resolved = path.resolve()
        server_root = _get_server_project_root()
        if resolved == server_root:
            return True
        return server_root.is_relative_to(resolved)
    except (OSError, ValueError):
        return False


def _deprioritize_own_source_dirs(candidates: list[Path]) -> list[Path]:
    """Move this server's own source repo to the end of the probe list."""
    other: list[Path] = []
    self_dirs: list[Path] = []
    for path in candidates:
        if _is_own_source_dir(path):
            self_dirs.append(path)
        else:
            other.append(path)
    if self_dirs and other:
        print(
            "Deprioritizing server source workspace(s): "
            f"{', '.join(str(p) for p in self_dirs)}",
            file=sys.stderr,
        )
    return other + self_dirs


def _fallback_workspace_candidates() -> list[Path]:
    """Workspace discovery when MCP roots/list is unavailable."""
    from .config import _find_project_root

    seen: set[str] = set()
    candidates: list[Path] = []

    def add(path: Path | str | None) -> None:
        if path is None:
            return
        try:
            resolved = Path(path).resolve()
        except OSError:
            return
        key = str(resolved).lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(resolved)

    explicit = os.getenv("DB_MCP_PROJECT_DIR")
    if explicit:
        add(explicit)
    add(_find_project_root())
    add(Path.cwd())
    return candidates


async def collect_workspace_candidates(ctx: Context) -> list[Path]:
    """Build an ordered, de-duplicated list of workspace directories to probe."""
    seen: set[str] = set()
    candidates: list[Path] = []

    def add(path: Path | None) -> None:
        if path is None:
            return
        try:
            resolved = path.resolve()
        except OSError:
            return
        key = str(resolved).lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(resolved)

    root_paths = await _list_workspace_root_paths(ctx)
    for path in root_paths:
        add(path)

    if not candidates:
        print(
            "Using fallback workspace discovery "
            "(DB_MCP_PROJECT_DIR, project root, CWD).",
            file=sys.stderr,
        )
        for path in _fallback_workspace_candidates():
            add(path)
            print(
                f"  fallback candidate: {path} (.env exists: {(path / '.env').exists()})",
                file=sys.stderr,
            )

    ordered = _deprioritize_own_source_dirs(candidates)
    if ordered:
        print(
            "Workspace probe order: "
            + ", ".join(str(path) for path in ordered),
            file=sys.stderr,
        )
    return ordered


@dataclass
class _Entry:
    registry: BackendRegistry
    env_map: dict[str, str]
    mtimes: dict[str, float]


class WorkspaceManager:
    """Session- and workspace-keyed backend registry cache."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session_roots: dict[int, Path] = {}
        self._registries: dict[str, _Entry] = {}

    def _root_key(self, root: Path) -> str:
        return str(root.resolve())

    def _build_entry(self, root: Path) -> _Entry:
        root = root.resolve()
        env_map = parse_workspace_env(root)
        registry = build_registry_from_env(env_map, root)
        config = config_from_env(env_map)
        verify_readonly_for_registry(config, registry, exit_on_write_failure=False)
        mtimes = record_env_mtimes(root)
        backends = registry.list_backends()
        default_name = registry.get_default_name()
        print(
            f"Initialized {len(backends)} backend(s) from workspace root {root}: "
            f"{', '.join(backends)}",
            file=sys.stderr,
        )
        if default_name:
            print(f"Default backend: {default_name}", file=sys.stderr)
        return _Entry(registry=registry, env_map=env_map, mtimes=mtimes)

    def _get_or_build(self, root: Path) -> _Entry:
        key = self._root_key(root)
        with self._lock:
            existing = self._registries.get(key)
            if existing is not None and not env_files_changed(root, existing.mtimes):
                return existing
            if existing is not None:
                existing.registry.clear()
            entry = self._build_entry(root)
            self._registries[key] = entry
            return entry

    async def _resolve_workspace_root(self, ctx: Context) -> Path:
        session_id = id(ctx.session)
        candidates = await collect_workspace_candidates(ctx)
        candidate_keys = {str(path.resolve()).lower() for path in candidates}

        with self._lock:
            cached = self._session_roots.get(session_id)
        if cached is not None:
            cached_key = str(cached.resolve()).lower()
            if cached_key in candidate_keys:
                try:
                    entry = self._get_or_build(cached)
                    if entry.registry.list_backends():
                        print(
                            f"Using cached workspace root: {cached.resolve()}",
                            file=sys.stderr,
                        )
                        return cached.resolve()
                except Exception as exc:
                    print(
                        f"Cached workspace root {cached} failed to initialize: {exc}",
                        file=sys.stderr,
                    )
            else:
                print(
                    f"Cached workspace root {cached} is not in current client roots; "
                    "re-probing",
                    file=sys.stderr,
                )
            with self._lock:
                self._session_roots.pop(session_id, None)

        found_env = False
        last_error: Exception | None = None

        for workspace in candidates:
            env_path = workspace / ".env"
            if not env_path.exists():
                print(
                    f"Skipping {workspace}: no .env file",
                    file=sys.stderr,
                )
                continue
            found_env = True
            print(f"Probing workspace root: {workspace}", file=sys.stderr)
            try:
                entry = self._get_or_build(workspace)
                if entry.registry.list_backends():
                    with self._lock:
                        self._session_roots[session_id] = workspace
                    print(
                        f"Selected workspace root: {workspace} "
                        f"({len(entry.registry.list_backends())} backend(s))",
                        file=sys.stderr,
                    )
                    return workspace
                print(
                    f"Workspace {workspace} has .env but no backends initialized",
                    file=sys.stderr,
                )
            except Exception as exc:
                last_error = exc
                print(f"Failed to initialize from {workspace}: {exc}", file=sys.stderr)

        if found_env and last_error is not None:
            raise last_error
        if found_env:
            raise ValueError(
                "Found .env file(s) in workspace roots but no backends could be configured."
            )
        raise ValueError(
            "No .env file found in any workspace root provided by the client."
        )

    async def get_registry_for(
        self, ctx: Context,
    ) -> tuple[BackendRegistry, dict[str, str], Path]:
        """Resolve session to workspace and return registry, env_map, root."""
        root = await self._resolve_workspace_root(ctx)
        entry = self._get_or_build(root)
        return entry.registry, entry.env_map, root

    def seed(self, root: Path, registry: BackendRegistry, env_map: dict[str, str]) -> None:
        """Register a pre-built entry (eager startup path)."""
        key = self._root_key(root)
        with self._lock:
            self._registries[key] = _Entry(
                registry=registry,
                env_map=env_map,
                mtimes=record_env_mtimes(root),
            )

    def close_all(self) -> None:
        """Close all cached backend registries."""
        with self._lock:
            entries = list(self._registries.values())
            self._registries.clear()
            self._session_roots.clear()
        for entry in entries:
            try:
                entry.registry.clear()
            except Exception:
                pass

_manager = WorkspaceManager()


def get_workspace_manager() -> WorkspaceManager:
    """Return the process-global workspace manager."""
    return _manager
