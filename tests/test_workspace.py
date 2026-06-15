"""Tests for per-workspace backend isolation."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from db_inspector_mcp.backends.registry import BackendRegistry
from db_inspector_mcp.workspace import (
    WorkspaceManager,
    _deprioritize_own_source_dirs,
    _get_server_project_root,
    _is_own_source_dir,
    _list_workspace_root_paths,
    _normalize_root_uri_for_mcp,
    _paths_from_list_roots_validation_error,
    _paths_from_pydantic_validation_error,
    _paths_from_raw_list_roots,
    _root_uri_to_path,
    collect_workspace_candidates,
)


def test_root_uri_to_path_file_uri_windows():
    assert _root_uri_to_path("file:///C:/Repos/my-project") == Path("C:/Repos/my-project")


def test_root_uri_to_path_bare_windows_path():
    assert _root_uri_to_path(r"c:\Repos\db-if-portal-sync") == Path(
        r"c:\Repos\db-if-portal-sync"
    )


def test_normalize_root_uri_for_mcp_bare_windows_path():
    uri = _normalize_root_uri_for_mcp(r"c:\Repos\db-if-portal-sync")
    assert uri.startswith("file:///")
    assert "db-if-portal-sync" in uri


def test_paths_from_pydantic_validation_error():
    from mcp.types import ListRootsResult
    from pydantic import ValidationError

    try:
        ListRootsResult.model_validate(
            {"roots": [{"uri": r"c:\Repos\db-if-portal-sync"}]},
        )
    except ValidationError as exc:
        paths = _paths_from_pydantic_validation_error(exc)
    else:
        pytest.fail("expected ValidationError")

    assert len(paths) == 1
    assert paths[0] == Path(r"c:\Repos\db-if-portal-sync").resolve()


def test_paths_from_raw_list_roots():
    raw = {
        "roots": [
            {"uri": r"c:\Repos\db-if-portal-sync", "name": "client"},
            {"uri": "file:///c:/Repos/db-inspector-mcp", "name": "server"},
        ],
    }
    paths = _paths_from_raw_list_roots(raw)
    assert len(paths) == 2
    assert paths[0] == Path(r"c:\Repos\db-if-portal-sync").resolve()
    assert paths[1] == Path("C:/Repos/db-inspector-mcp").resolve()


@pytest.mark.anyio
async def test_list_workspace_root_paths_normalizes_bare_paths_from_raw():
    ctx = MagicMock()
    ctx.session = MagicMock()

    async def fake_fetch(_ctx):
        return {"roots": [{"uri": r"c:\Repos\db-if-portal-sync"}]}

    with patch(
        "db_inspector_mcp.workspace._fetch_list_roots_raw",
        fake_fetch,
    ):
        paths = await _list_workspace_root_paths(ctx)

    assert len(paths) == 1
    assert paths[0] == Path(r"c:\Repos\db-if-portal-sync").resolve()


def test_paths_from_list_roots_validation_error():
    exc = Exception(
        "2 validation errors for ListRootsResult\n"
        "roots.0.uri\n"
        "  URL scheme should be 'file' [type=url_scheme, "
        "input_value='c:\\\\Repos\\\\db-if-portal-sync', input_type=str]\n"
        "roots.1.uri\n"
        "  URL scheme should be 'file' [type=url_scheme, "
        "input_value='c:\\\\Repos\\\\db-inspector-mcp', input_type=str]"
    )
    paths = _paths_from_list_roots_validation_error(exc)
    assert len(paths) == 2
    assert paths[0] == Path(r"c:\Repos\db-if-portal-sync").resolve()
    assert paths[1] == Path(r"c:\Repos\db-inspector-mcp").resolve()


def test_is_own_source_dir_detects_server_repo():
    server_root = _get_server_project_root()
    assert _is_own_source_dir(server_root) is True
    assert _is_own_source_dir(server_root / "src") is False
    assert _is_own_source_dir(Path(r"C:\Repos\db-if-portal-sync")) is False


def test_deprioritize_own_source_dirs_moves_server_repo_last():
    server_root = _get_server_project_root()
    project_root = Path(r"C:\Repos\db-if-portal-sync")
    ordered = _deprioritize_own_source_dirs([server_root, project_root])
    assert ordered == [project_root, server_root]


@pytest.mark.anyio
async def test_collect_workspace_candidates_deprioritizes_own_source_dir():
    server_root = _get_server_project_root()
    project_root = Path(r"C:\Repos\db-if-portal-sync")

    root_inspector = MagicMock()
    root_inspector.uri = str(server_root)
    root_project = MagicMock()
    root_project.uri = str(project_root)

    ctx = MagicMock()

    async def fake_list_roots():
        return MagicMock(roots=[root_inspector, root_project])

    ctx.session.list_roots = fake_list_roots

    with patch("db_inspector_mcp.workspace._fetch_list_roots_raw", return_value=None):
        candidates = await collect_workspace_candidates(ctx)
    assert candidates[0] == project_root.resolve()
    assert candidates[-1] == server_root.resolve()


@pytest.mark.anyio
async def test_manager_resolves_session_to_workspace(tmp_path):
    (tmp_path / ".env").write_text(
        "DB_MCP_DEFAULT_DATABASE=sqlserver\n"
        "DB_MCP_DEFAULT_CONNECTION_STRING=test\n"
    )

    manager = WorkspaceManager()
    ctx = MagicMock()
    ctx.session = MagicMock()

    async def fake_list_roots():
        root = MagicMock()
        root.uri = str(tmp_path)
        return MagicMock(roots=[root])

    ctx.session.list_roots = fake_list_roots

    with patch("db_inspector_mcp.workspace._fetch_list_roots_raw", return_value=None):
        with patch("db_inspector_mcp.workspace.verify_readonly_for_registry"):
            registry, env_map, root = await manager.get_registry_for(ctx)

    assert root == tmp_path.resolve()
    assert "default" in registry.list_backends()
    assert env_map["DB_MCP_DEFAULT_DATABASE"] == "sqlserver"

    registry2, _, root2 = await manager.get_registry_for(ctx)
    assert root2 == root
    assert registry2 is registry


@pytest.mark.anyio
async def test_manager_prefers_client_project_over_server_repo(tmp_path, monkeypatch):
    server_root = tmp_path / "db-inspector-mcp"
    project_root = tmp_path / "client-project"
    server_root.mkdir()
    project_root.mkdir()
    (server_root / ".env").write_text(
        "DB_MCP_SYNC_DATABASE=sqlserver\nDB_MCP_SYNC_CONNECTION_STRING=srv\n"
    )
    (project_root / ".env").write_text(
        "DB_MCP_SYNC_DATABASE=sqlserver\nDB_MCP_SYNC_CONNECTION_STRING=client\n"
    )
    monkeypatch.setattr(
        "db_inspector_mcp.workspace._get_server_project_root", lambda: server_root,
    )

    manager = WorkspaceManager()
    ctx = MagicMock()

    root_inspector = MagicMock()
    root_inspector.uri = str(server_root)
    root_project = MagicMock()
    root_project.uri = str(project_root)

    async def fake_list_roots():
        return MagicMock(roots=[root_inspector, root_project])

    ctx.session.list_roots = fake_list_roots

    with patch("db_inspector_mcp.workspace._fetch_list_roots_raw", return_value=None):
        with patch("db_inspector_mcp.workspace.verify_readonly_for_registry"):
            _, env_map, root = await manager.get_registry_for(ctx)

    assert root == project_root.resolve()
    assert env_map["DB_MCP_SYNC_CONNECTION_STRING"] == "client"


@pytest.mark.anyio
async def test_manager_recovers_bare_paths_from_validation_error(tmp_path):
    (tmp_path / ".env").write_text(
        "DB_MCP_DEFAULT_DATABASE=sqlserver\n"
        "DB_MCP_DEFAULT_CONNECTION_STRING=test\n"
    )

    bare_path = str(tmp_path)

    async def failing_list_roots():
        raise Exception(
            "1 validation error for ListRootsResult\n"
            "roots.0.uri\n"
            f"  URL scheme should be 'file' [type=url_scheme, "
            f"input_value='{bare_path.replace(chr(92), chr(92) * 2)}', input_type=str]"
        )

    manager = WorkspaceManager()
    ctx = MagicMock()
    ctx.session.list_roots = failing_list_roots

    with patch("db_inspector_mcp.workspace._fetch_list_roots_raw", return_value=None):
        with patch("db_inspector_mcp.workspace.verify_readonly_for_registry"):
            _, _, root = await manager.get_registry_for(ctx)

    assert root == tmp_path.resolve()


@pytest.mark.anyio
async def test_manager_revalidates_cached_root_when_client_roots_change(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / ".env").write_text(
        "DB_MCP_FIRST_DATABASE=sqlserver\nDB_MCP_FIRST_CONNECTION_STRING=one\n",
    )
    (second_root / ".env").write_text(
        "DB_MCP_SECOND_DATABASE=sqlserver\nDB_MCP_SECOND_CONNECTION_STRING=two\n",
    )

    manager = WorkspaceManager()
    ctx = MagicMock()
    ctx.session = MagicMock()
    current_roots = [first_root]

    async def fake_list_roots():
        root = MagicMock()
        root.uri = str(current_roots[0])
        return MagicMock(roots=[root])

    ctx.session.list_roots = fake_list_roots

    with patch("db_inspector_mcp.workspace._fetch_list_roots_raw", return_value=None):
        with patch("db_inspector_mcp.workspace.verify_readonly_for_registry"):
            _, env_map, root = await manager.get_registry_for(ctx)

            assert root == first_root.resolve()
            assert env_map["DB_MCP_FIRST_CONNECTION_STRING"] == "one"

            current_roots[0] = second_root
            _, env_map, root = await manager.get_registry_for(ctx)

            assert root == second_root.resolve()
            assert env_map["DB_MCP_SECOND_CONNECTION_STRING"] == "two"


def test_manager_seed_avoids_rebuild(tmp_path):
    manager = WorkspaceManager()
    registry = BackendRegistry()
    env_map = {"DB_MCP_DATABASE": "sqlserver"}
    manager.seed(tmp_path, registry, env_map)

    with manager._lock:
        entry = manager._registries[str(tmp_path.resolve())]
    assert entry.registry is registry
    assert entry.env_map == env_map


def test_manager_close_all_clears_registries(tmp_path):
    manager = WorkspaceManager()
    registry = BackendRegistry()
    env_map = {}
    manager.seed(tmp_path, registry, env_map)
    manager.close_all()
    assert manager._registries == {}
    assert manager._session_roots == {}
