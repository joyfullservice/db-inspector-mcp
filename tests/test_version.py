"""Version consistency checks."""

import re
from pathlib import Path

from db_inspector_mcp import __version__


def test_init_version_matches_pyproject() -> None:
    """Keep package runtime version and project metadata in sync."""
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    content = pyproject_path.read_text(encoding="utf-8")

    project_match = re.search(r"\[project\](.*?)(?:\n\[|\Z)", content, re.DOTALL)
    assert project_match is not None, "Could not find [project] section in pyproject.toml"

    version_match = re.search(
        r'^\s*version\s*=\s*"([^"]+)"\s*$',
        project_match.group(1),
        re.MULTILINE,
    )
    assert version_match is not None, "Could not find project version in pyproject.toml"

    assert __version__ == version_match.group(1)
