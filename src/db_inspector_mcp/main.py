"""Main entry point for db-inspector-mcp MCP server."""

import sys

from .config import get_backend, get_config
from .tools import mcp, set_backend


def main() -> None:
    """Main entry point for the MCP server."""
    # Load configuration
    config = get_config()
    
    # Initialize backend
    try:
        backend = get_backend()
        set_backend(backend)
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Failed to initialize database backend: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Verify read-only if enabled
    verify_readonly = config.get("DB_VERIFY_READONLY", "true").lower() == "true"
    if verify_readonly:
        try:
            result = backend.verify_readonly()
            readonly_status = "✓ Read-only" if result["readonly"] else "⚠ Write permissions detected"
            print(f"{readonly_status}: {result['details']}", file=sys.stderr)
            
            # Check if we should fail on write permissions
            fail_on_write = config.get("DB_READONLY_FAIL_ON_WRITE", "false").lower() == "true"
            if not result["readonly"] and fail_on_write:
                print("Failing startup due to write permissions (DB_READONLY_FAIL_ON_WRITE=true)", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"Warning: Could not verify read-only status: {e}", file=sys.stderr)
    
    # Run the MCP server
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

