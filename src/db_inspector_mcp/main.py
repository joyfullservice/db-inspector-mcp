"""Main entry point for db-inspector-mcp MCP server."""

import sys

from .backends.registry import get_registry
from .config import get_config, initialize_backends


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


def _verify_readonly(config: dict, registry) -> None:
    """Verify read-only status for all registered backends."""
    verify_readonly = config.get("DB_MCP_VERIFY_READONLY", "true").lower() == "true"
    if not verify_readonly:
        return

    fail_on_write = config.get("DB_MCP_READONLY_FAIL_ON_WRITE", "false").lower() == "true"
    for backend_name in registry.list_backends():
        try:
            backend = registry.get(backend_name)
            result = backend.verify_readonly()
            readonly_status = "✓ Read-only" if result["readonly"] else "⚠ Write permissions detected"
            print(f"[{backend_name}] {readonly_status}: {result['details']}", file=sys.stderr)

            if not result["readonly"] and fail_on_write:
                print(
                    f"Failing startup due to write permissions on '{backend_name}' "
                    f"(DB_MCP_READONLY_FAIL_ON_WRITE=true)",
                    file=sys.stderr,
                )
                sys.exit(1)
        except Exception as e:
            print(f"Warning: Could not verify read-only status for '{backend_name}': {e}", file=sys.stderr)


def main() -> None:
    """Main entry point for the MCP server."""
    if _handle_subcommand():
        return

    from . import __version__
    print(f"db-inspector-mcp v{__version__}", file=sys.stderr)

    # Load configuration (automatically loads .env files from project root)
    # Environment variables from MCP server env section take precedence
    config = get_config()
    
    # Initialize backends (supports both single and multi-database configurations)
    try:
        registry = initialize_backends()
        backends = registry.list_backends()
        default_name = registry.get_default_name()
        
        print(f"Initialized {len(backends)} database backend(s): {', '.join(backends)}", file=sys.stderr)
        if default_name:
            print(f"Default backend: {default_name}", file=sys.stderr)

        _verify_readonly(config, registry)
    except ValueError:
        # No database configuration found at startup.  This is expected when
        # the MCP server is configured at the user level and the working
        # directory is not the project root.  Backends will be initialized
        # lazily on the first tool call using MCP workspace roots.
        print(
            "No database configuration found at startup — "
            "will attempt workspace detection on first tool call.",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"Failed to initialize database backends: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Import mcp here to avoid circular imports
    from .tools import mcp
    
    # Run the MCP server
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

