"""Project initialization for db-inspector-mcp.

Provides the ``db-inspector-mcp init`` CLI command and the template loader
used by both the CLI and the MCP prompt.
"""

import argparse
import json
import sys
from pathlib import Path

# Minimal global mcp.json entry — no env overrides so project-level .env
# settings (DB_MCP_ALLOW_DATA_ACCESS, etc.) are never shadowed.
MCP_JSON_SERVER_ENTRY = {
    "command": "db-inspector-mcp",
}

MCP_JSON_TEMPLATE = json.dumps(
    {"mcpServers": {"db-inspector-mcp": MCP_JSON_SERVER_ENTRY}},
    indent=2,
)


def load_env_example() -> str:
    """Read the .env.example template shipped with the package.

    Resolution order:
    1. Repo root relative to this file (editable / source installs)
    2. ``importlib.resources`` (wheel / regular installs where the file is
       bundled via ``package-data``)
    """
    # Editable install: __file__ is src/db_inspector_mcp/init.py
    repo_root = Path(__file__).resolve().parent.parent.parent
    candidate = repo_root / ".env.example"
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")

    # Regular install with package-data
    try:
        import importlib.resources
        ref = importlib.resources.files("db_inspector_mcp").joinpath(".env.example")
        return ref.read_text(encoding="utf-8")
    except (FileNotFoundError, TypeError, AttributeError):
        pass

    raise FileNotFoundError(
        "Could not find .env.example template. "
        "Ensure the package was installed correctly or run from the repository root."
    )


def _write_env_file(target_dir: Path, *, force: bool) -> Path:
    """Copy the .env.example template into *target_dir* as ``.env``."""
    env_path = target_dir / ".env"
    if env_path.exists() and not force:
        print(
            f"Error: {env_path} already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)

    content = load_env_example()
    env_path.write_text(content, encoding="utf-8")
    return env_path


def _get_global_mcp_json_path() -> Path:
    """Return the path to the user-level Cursor mcp.json."""
    home = Path.home()
    return home / ".cursor" / "mcp.json"


def _register_global_mcp(*, quiet: bool = False) -> Path:
    """Add db-inspector-mcp to the global ``~/.cursor/mcp.json``.

    Creates the file (and parent directories) if it doesn't exist.
    Skips if the server entry is already present.

    Returns the path to the mcp.json file.
    """
    mcp_json_path = _get_global_mcp_json_path()
    mcp_json_path.parent.mkdir(parents=True, exist_ok=True)

    if mcp_json_path.exists():
        try:
            data = json.loads(mcp_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            data = {}
    else:
        data = {}

    servers = data.setdefault("mcpServers", {})
    if "db-inspector-mcp" in servers:
        if not quiet:
            print(f"  Already registered in {mcp_json_path}")
        return mcp_json_path

    servers["db-inspector-mcp"] = MCP_JSON_SERVER_ENTRY
    mcp_json_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if not quiet:
        print(f"  Registered in {mcp_json_path}")
    return mcp_json_path


def run_init(argv: list[str] | None = None) -> None:
    """Entry point for ``db-inspector-mcp init``."""
    parser = argparse.ArgumentParser(
        prog="db-inspector-mcp init",
        description="Initialize db-inspector-mcp in a project directory.",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path.cwd(),
        help="Target directory for .env (default: current directory)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing .env file",
    )
    args = parser.parse_args(argv)
    target_dir: Path = args.dir.resolve()

    if not target_dir.is_dir():
        print(f"Error: {target_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    env_path = target_dir / ".env"
    if env_path.exists() and not args.force:
        print(
            f"Error: {env_path} already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Initializing db-inspector-mcp...")

    # 1. Copy .env template
    env_path = _write_env_file(target_dir, force=args.force)
    print(f"  Created {env_path}")

    # 2. Register in global ~/.cursor/mcp.json
    _register_global_mcp()

    # 3. Next steps
    print()
    print("Next steps:")
    print(f"  1. Edit {env_path} with your database connection details")
    print("  2. Restart Cursor to load the MCP server")
    print()
    print("For configuration help, see: https://github.com/joyfullservice/db-inspector-mcp#configuration")
