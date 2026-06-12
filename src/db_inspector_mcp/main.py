"""Main entry point for db-inspector-mcp MCP server."""

import atexit
import signal
import sys

from .backends.registry import get_registry
from .config import (
    config_from_env,
    initialize_backends,
    parse_workspace_env,
    _find_project_root,
)
from .readonly import verify_readonly_for_registry
from .workspace import get_workspace_manager


def _handle_subcommand() -> bool:
    """Dispatch CLI subcommands (e.g. ``init``) before starting the MCP server.

    Returns True if a subcommand was handled (caller should exit), False
    otherwise (proceed with MCP server startup).
    """
    if len(sys.argv) < 2:
        return False

    command = sys.argv[1].lower()
    if command == "init":
        from .init import run_init
        run_init(sys.argv[2:])
        return True
    if command in ("--version", "-V"):
        from . import __version__
        print(f"db-inspector-mcp {__version__}")
        return True
    if command in ("--help", "-h"):
        print("usage: db-inspector-mcp [init | --version | --help]")
        print()
        print("commands:")
        print("  init       Initialize db-inspector-mcp in a project (creates .env,")
        print("             registers in ~/.cursor/mcp.json)")
        print("  --version  Show version number")
        print()
        print("When run without arguments, starts the MCP server (stdio transport).")
        return True

    return False


def _cleanup() -> None:
    """Release all backend resources (ODBC connections, COM references, timers)."""
    try:
        get_workspace_manager().close_all()
    except Exception:
        pass
    try:
        get_registry().clear()
    except Exception:
        pass


def main() -> None:
    """Main entry point for the MCP server."""
    if _handle_subcommand():
        return

    from . import __version__
    print(f"db-inspector-mcp v{__version__}", file=sys.stderr)

    atexit.register(_cleanup)

    def _signal_handler(signum, frame):
        _cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    manager = get_workspace_manager()

    try:
        root = _find_project_root()
        env_map = parse_workspace_env(root)
        registry = initialize_backends()
        config = config_from_env(env_map)
        backends = registry.list_backends()
        default_name = registry.get_default_name()

        print(
            f"Initialized {len(backends)} database backend(s): {', '.join(backends)}",
            file=sys.stderr,
        )
        if default_name:
            print(f"Default backend: {default_name}", file=sys.stderr)

        verify_readonly_for_registry(config, registry, exit_on_write_failure=True)
        manager.seed(root, registry, env_map)
    except ValueError:
        print(
            "No database configuration found at startup — "
            "will initialize per workspace on first tool call.",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"Failed to initialize database backends: {e}", file=sys.stderr)
        sys.exit(1)

    from .tools import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
