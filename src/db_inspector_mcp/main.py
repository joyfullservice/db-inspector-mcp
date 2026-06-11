"""Main entry point for db-inspector-mcp MCP server."""

import atexit
import signal
import sys
import threading

from .backends.registry import get_registry
from .config import get_config, initialize_backends

# Wall-clock bound (seconds) for verifying read-only status of a single
# backend at startup.  Verification opens a connection and probes write
# permissions, which can block on a locked/slow database.  A backend that
# exceeds this bound is treated as "could not verify" (non-fatal) rather
# than blocking server startup.
_VERIFY_READONLY_TIMEOUT_SECONDS = 10.0


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


def _verify_readonly_bounded(backend, timeout: float) -> dict:
    """Call ``backend.verify_readonly()`` with a wall-clock timeout.

    The probe opens a connection and runs a write test, which can block on a
    locked or slow database.  We run it on a daemon thread and abandon it if
    it exceeds *timeout*, so one unresponsive backend can never block startup.

    Returns:
        The backend's result dict on success, or a synthetic dict with
        ``readonly=None`` when the call timed out or raised.
    """
    result: dict = {}

    def _worker() -> None:
        try:
            result["value"] = backend.verify_readonly()
        except BaseException as exc:  # noqa: BLE001 — relay to caller thread
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        return {
            "readonly": None,
            "details": f"verification timed out after {timeout}s (backend unresponsive)",
        }
    if "error" in result:
        return {
            "readonly": None,
            "details": f"verification failed: {result['error']}",
        }
    return result["value"]


def _verify_readonly(config: dict, registry) -> None:
    """Verify read-only status for all registered backends.

    Each backend is verified with a wall-clock timeout so a locked/slow
    backend cannot block server startup.  A backend whose verification times
    out or errors is logged as a warning (``readonly`` unknown) and skipped —
    it never aborts startup unless write permissions are positively detected
    and ``DB_MCP_READONLY_FAIL_ON_WRITE`` is set.
    """
    verify_readonly = config.get("DB_MCP_VERIFY_READONLY", "true").lower() == "true"
    if not verify_readonly:
        return

    fail_on_write = config.get("DB_MCP_READONLY_FAIL_ON_WRITE", "false").lower() == "true"
    for backend_name in registry.list_backends():
        try:
            backend = registry.get(backend_name)
            result = _verify_readonly_bounded(backend, _VERIFY_READONLY_TIMEOUT_SECONDS)

            if result.get("readonly") is None:
                # Could not determine (timeout/error) — non-fatal.
                print(
                    f"[{backend_name}] ⚠ Could not verify read-only status: "
                    f"{result.get('details', 'unknown')}",
                    file=sys.stderr,
                )
                continue

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


def _cleanup() -> None:
    """Release all backend resources (ODBC connections, COM references, timers).

    Registered via ``atexit`` and called from signal handlers so that
    the process can exit cleanly when Cursor closes the stdio pipe or
    sends SIGTERM/SIGINT.
    """
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

    # Ensure backends are cleaned up on exit — covers normal exit,
    # unhandled exceptions, and broken-pipe scenarios.
    atexit.register(_cleanup)

    # Handle SIGTERM/SIGINT so the process exits instead of hanging
    # when Cursor kills the stdio pipe.
    def _signal_handler(signum, frame):
        _cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

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

