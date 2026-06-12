"""Per-workspace backend registry management."""



from __future__ import annotations



import os

import re

import sys

import threading

from dataclasses import dataclass

from pathlib import Path

from typing import TYPE_CHECKING

from urllib.parse import unquote, urlparse



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





def _paths_from_list_roots_validation_error(exc: Exception) -> list[Path]:

    """Recover workspace paths when the MCP SDK rejects malformed root URIs."""

    message = str(exc)

    if "ListRootsResult" not in message:

        return []



    seen: set[str] = set()

    paths: list[Path] = []

    for match in re.finditer(r"input_value='((?:\\.|[^'])*)'", message):

        candidate = _root_uri_to_path(match.group(1))

        if candidate is None:

            continue

        try:

            resolved = candidate.resolve()

        except OSError:

            continue

        key = str(resolved).lower()

        if key in seen:

            continue

        seen.add(key)

        paths.append(resolved)

    return paths





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



    try:

        roots_result = await ctx.session.list_roots()

        for root in roots_result.roots:

            add(_root_uri_to_path(str(root.uri)))

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

                add(path)



    if not candidates:

        print(

            "Using fallback workspace discovery "

            "(DB_MCP_PROJECT_DIR, project root, CWD).",

            file=sys.stderr,

        )

        for path in _fallback_workspace_candidates():

            add(path)



    return _deprioritize_own_source_dirs(candidates)





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

        with self._lock:

            cached = self._session_roots.get(session_id)

        if cached is not None:

            return cached



        candidates = await collect_workspace_candidates(ctx)

        found_env = False

        last_error: Exception | None = None



        for workspace in candidates:

            if not (workspace / ".env").exists():

                continue

            found_env = True

            try:

                entry = self._get_or_build(workspace)

                if entry.registry.list_backends():

                    with self._lock:

                        self._session_roots[session_id] = workspace

                    return workspace

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



    def invalidate_root(self, root: Path) -> None:

        """Drop cached entry for a workspace root (e.g. after hot-reload)."""

        key = self._root_key(root)

        with self._lock:

            entry = self._registries.pop(key, None)

        if entry is not None:

            entry.registry.clear()



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

