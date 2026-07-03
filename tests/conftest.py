"""Shared test helpers."""

from __future__ import annotations

from pathlib import Path

from db_inspector_mcp.resolution_logging import ResolutionInfo


def make_resolution_info(
    root: Path | str = "/fake/workspace",
    *,
    resolved_via: str = "test",
    session_id: int = 1,
) -> ResolutionInfo:
    """Build a minimal ResolutionInfo for mocked get_registry_for returns."""
    return ResolutionInfo(
        workspace_root=str(Path(root)),
        resolved_via=resolved_via,
        session_id=session_id,
    )


def fake_registry_tuple(
    registry,
    root: Path | str = "/fake/workspace",
    *,
    env_map: dict | None = None,
    resolved_via: str = "test",
):
    """Return the 4-tuple shape of WorkspaceManager.get_registry_for."""
    root_path = Path(root)
    return (
        registry,
        env_map if env_map is not None else {},
        root_path,
        make_resolution_info(root_path, resolved_via=resolved_via),
    )
