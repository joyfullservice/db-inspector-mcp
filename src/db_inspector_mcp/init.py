"""Project initialization for db-inspector-mcp.

Provides the ``db-inspector-mcp init`` CLI command and the template loader
used by both the CLI and the MCP prompt.
"""

import argparse
import json
import sys
from pathlib import Path

# Minimal global mcp.json entry — uses uvx for automatic package management.
# No env overrides so project-level .env settings (DB_MCP_ALLOW_DATA_ACCESS,
# etc.) are never shadowed.
MCP_JSON_SERVER_ENTRY = {
    "command": "uvx",
    "args": ["db-inspector-mcp@latest"],
}

MCP_JSON_TEMPLATE = json.dumps(
    {"mcpServers": {"db-inspector-mcp": MCP_JSON_SERVER_ENTRY}},
    indent=2,
)

_DOCS_URL = "https://github.com/joyfullservice/db-inspector-mcp#configuration"

_ENV_STARTER_BLOCK = f"""
# db-inspector-mcp ({_DOCS_URL})
#DB_MCP_DATABASE=sqlserver
#DB_MCP_CONNECTION_STRING=Driver={{ODBC Driver 17 for SQL Server}};Server=localhost;Database=mydb;UID=user;PWD=password
"""


def _env_has_db_mcp_vars(env_path: Path) -> bool:
    """Check if a .env file already references DB_MCP_ variables (even commented)."""
    content = env_path.read_text(encoding="utf-8")
    return "DB_MCP_" in content


def load_env_example() -> str:
    """Read the .env.example template shipped with the package.

    Resolution order:
    1. ``importlib.resources`` (works for both wheel installs and editable
       installs since the file lives in the package directory)
    2. Repo root relative to this file (fallback for editable installs if
       the package-local copy is missing)
    """
    try:
        import importlib.resources
        ref = importlib.resources.files("db_inspector_mcp").joinpath(".env.example")
        return ref.read_text(encoding="utf-8")
    except (FileNotFoundError, TypeError, AttributeError):
        pass

    # Fallback: editable install with file at repo root only
    repo_root = Path(__file__).resolve().parent.parent.parent
    candidate = repo_root / ".env.example"
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")

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


_MCP_CLIENT_CONFIGS = [
    ("Cursor", Path.home() / ".cursor" / "mcp.json"),
    ("Claude Code", Path.home() / ".claude.json"),
]


def _is_registered_in(mcp_json_path: Path) -> bool:
    """Check whether db-inspector-mcp is registered in a config file."""
    if not mcp_json_path.exists():
        return False
    try:
        data = json.loads(mcp_json_path.read_text(encoding="utf-8"))
        return "db-inspector-mcp" in data.get("mcpServers", {})
    except (json.JSONDecodeError, ValueError, OSError):
        return False


def is_globally_registered() -> bool:
    """Check whether db-inspector-mcp is in any global MCP config."""
    return any(_is_registered_in(path) for _, path in _MCP_CLIENT_CONFIGS)


def _register_in_config(mcp_json_path: Path, *, quiet: bool = False) -> Path:
    """Add db-inspector-mcp to a global MCP config file.

    Creates the file (and parent directories) if it doesn't exist.
    Skips if the server entry is already present.

    Returns the path to the config file.
    """
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


def _register_global_mcp(*, quiet: bool = False) -> None:
    """Register db-inspector-mcp in all known MCP client configs."""
    for _name, path in _MCP_CLIENT_CONFIGS:
        _register_in_config(path, quiet=quiet)


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

    print("Initializing db-inspector-mcp...")

    # 1. Configure .env
    env_path = target_dir / ".env"
    env_appended = False
    if env_path.exists() and not args.force:
        if _env_has_db_mcp_vars(env_path):
            print(f"  {env_path} already contains DB_MCP_ configuration")
        else:
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(_ENV_STARTER_BLOCK)
            env_appended = True
            print(f"  Appended DB_MCP_ starter config to {env_path}")
            print(f"  Details: {_DOCS_URL}")
    else:
        env_path = _write_env_file(target_dir, force=args.force)
        print(f"  Created {env_path}")

    # 2. Register in global MCP client configs
    _register_global_mcp()

    # 3. Next steps
    print()
    print("Next steps:")
    if env_appended:
        print(f"  1. Edit the DB_MCP_ variables in {env_path}")
    else:
        print(f"  1. Edit {env_path} with your database connection details")
    print("  2. Restart your MCP client (Cursor, Claude Code, etc.) to load the server")
    print()
    print(f"For configuration help, see: {_DOCS_URL}")
