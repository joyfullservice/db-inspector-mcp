"""Main entry point for db-inspector-mcp MCP server."""

import sys

from .backends.registry import get_registry
from .config import get_config, initialize_backends


def main() -> None:
    """Main entry point for the MCP server."""
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
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Failed to initialize database backends: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Verify read-only if enabled
    verify_readonly = config.get("DB_MCP_VERIFY_READONLY", "true").lower() == "true"
    if verify_readonly:
        fail_on_write = config.get("DB_MCP_READONLY_FAIL_ON_WRITE", "false").lower() == "true"
        
        for backend_name in backends:
            try:
                backend = registry.get(backend_name)
                result = backend.verify_readonly()
                readonly_status = "✓ Read-only" if result["readonly"] else "⚠ Write permissions detected"
                print(f"[{backend_name}] {readonly_status}: {result['details']}", file=sys.stderr)
                
                # Check if we should fail on write permissions
                if not result["readonly"] and fail_on_write:
                    print(f"Failing startup due to write permissions on '{backend_name}' (DB_MCP_READONLY_FAIL_ON_WRITE=true)", file=sys.stderr)
                    sys.exit(1)
            except Exception as e:
                print(f"Warning: Could not verify read-only status for '{backend_name}': {e}", file=sys.stderr)
    
    # Import mcp here to avoid circular imports
    from .tools import mcp
    
    # Run the MCP server
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

