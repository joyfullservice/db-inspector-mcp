"""
Usage logging for db-inspector-mcp.

Provides structured JSON logging for tool usage analytics and debugging.
Logs are stored with automatic rotation.

**Log Location Strategy:**
- Development mode: Logs to {project_root}/logs/ within the source directory
- Package mode: Logs to ~/.db-inspector-mcp/logs/

Development mode is detected when the source files are in a directory with
pyproject.toml and src/ structure, indicating an editable install.

This allows developers to:
1. Collect usage logs from any client project using the tool
2. Open the MCP source project in an editor
3. Have agents analyze logs alongside source code to suggest improvements

Configuration via environment variables:
- DB_MCP_ENABLE_LOGGING: Set to "true" to enable logging (default: false)
- DB_MCP_LOG_DIR: Custom log directory (overrides auto-detection)
- DB_MCP_LOG_MAX_SIZE_MB: Max size before rotation (default: 10)
- DB_MCP_LOG_BACKUP_COUNT: Number of rotated files to keep (default: 5)
"""

import functools
import json
import os
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

# Module-level state
_logging_enabled: bool | None = None
_log_handler: RotatingFileHandler | None = None
_log_file: Path | None = None


def _is_development_install() -> bool:
    """
    Check if we're running from a development (editable) install.
    
    Returns True if the source files are in a directory structure that
    indicates a development environment (has pyproject.toml, src/, etc.).
    """
    try:
        # Get the directory containing this file
        this_file = Path(__file__).resolve()
        # Go up: usage_logging.py -> db_inspector_mcp -> src -> project_root
        package_dir = this_file.parent  # db_inspector_mcp/
        src_dir = package_dir.parent     # src/
        project_root = src_dir.parent    # project root
        
        # Check for development markers
        has_pyproject = (project_root / "pyproject.toml").exists()
        has_src_structure = src_dir.name == "src" and package_dir.name == "db_inspector_mcp"
        has_tests = (project_root / "tests").exists()
        
        return has_pyproject and has_src_structure and has_tests
    except Exception:
        return False


def _get_project_root() -> Path | None:
    """
    Get the project root directory if running from development install.
    
    Returns None if not in development mode.
    """
    try:
        this_file = Path(__file__).resolve()
        package_dir = this_file.parent
        src_dir = package_dir.parent
        project_root = src_dir.parent
        
        if _is_development_install():
            return project_root
        return None
    except Exception:
        return None


def _get_default_log_dir() -> Path:
    """
    Get the default log directory.
    
    - Development mode: {project_root}/logs/
    - Package mode: ~/.db-inspector-mcp/logs/
    """
    project_root = _get_project_root()
    
    if project_root is not None:
        # Development mode: log to project directory
        return project_root / "logs"
    else:
        # Package mode: log to user's home directory
        return Path.home() / ".db-inspector-mcp" / "logs"


def _get_logging_config() -> dict[str, Any]:
    """Load logging configuration from environment variables."""
    return {
        "enabled": os.getenv("DB_MCP_ENABLE_LOGGING", "false").lower() == "true",
        "log_dir": os.getenv("DB_MCP_LOG_DIR", ""),
        "max_size_mb": int(os.getenv("DB_MCP_LOG_MAX_SIZE_MB", "10")),
        "backup_count": int(os.getenv("DB_MCP_LOG_BACKUP_COUNT", "5")),
    }


def _ensure_log_dir(log_dir: Path) -> bool:
    """
    Ensure the log directory exists.
    
    Returns:
        True if directory exists or was created, False if creation failed.
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        print(f"Warning: Could not create log directory {log_dir}: {e}", file=sys.stderr)
        return False


def _initialize_logging() -> bool:
    """
    Initialize the logging system.
    
    Returns:
        True if logging is enabled and initialized, False otherwise.
    """
    global _logging_enabled, _log_handler, _log_file
    
    # Already successfully initialized -- skip re-init.
    if _logging_enabled is True:
        return True

    config = _get_logging_config()

    if not config["enabled"]:
        # Do NOT cache False: in the lazy-init path the .env file hasn't
        # been loaded yet, so the env var is absent.  Leaving
        # _logging_enabled as None lets us re-check on the next call once
        # load_config() has populated the environment.
        return False
    
    # Determine log directory
    if config["log_dir"]:
        log_dir = Path(config["log_dir"])
    else:
        log_dir = _get_default_log_dir()
    
    # Ensure directory exists
    if not _ensure_log_dir(log_dir):
        _logging_enabled = False
        return False
    
    # Create log file path
    _log_file = log_dir / "usage.jsonl"
    
    # Calculate max bytes from MB
    max_bytes = config["max_size_mb"] * 1024 * 1024
    
    try:
        # Create rotating file handler
        _log_handler = RotatingFileHandler(
            filename=str(_log_file),
            maxBytes=max_bytes,
            backupCount=config["backup_count"],
            encoding="utf-8",
        )
        _logging_enabled = True
        
        # Log initialization
        _write_log_entry({
            "event": "logging_initialized",
            "log_file": str(_log_file),
            "max_size_mb": config["max_size_mb"],
            "backup_count": config["backup_count"],
        })
        
        return True
        
    except Exception as e:
        print(f"Warning: Could not initialize logging: {e}", file=sys.stderr)
        _logging_enabled = False
        return False


def reset_logging() -> None:
    """Clear logging state so it re-initializes from fresh env vars.

    Called by :func:`config.load_config` when a ``.env`` file change is
    detected, ensuring that logging configuration changes (enable/disable,
    log directory, rotation settings) take effect without a server restart.
    """
    global _logging_enabled, _log_handler, _log_file
    if _log_handler is not None:
        try:
            _log_handler.close()
        except Exception:
            pass
    _logging_enabled = None
    _log_handler = None
    _log_file = None


def _write_log_entry(entry: dict[str, Any]) -> None:
    """Write a single log entry to the log file."""
    global _log_handler
    
    if _log_handler is None:
        return
    
    # Add timestamp and version if not present
    if "timestamp" not in entry:
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    if "version" not in entry:
        from . import __version__
        entry["version"] = __version__
    
    try:
        # Format as JSON line
        log_line = json.dumps(entry, default=str) + "\n"
        
        # Write using the handler (handles rotation automatically)
        _log_handler.stream.write(log_line)
        _log_handler.stream.flush()
        
        # Check if rotation is needed by comparing file size directly.
        # We can't use shouldRollover() because it expects a LogRecord and
        # we're writing to the stream directly, bypassing the logging module.
        if _log_handler.maxBytes > 0:
            _log_handler.stream.seek(0, 2)
            if _log_handler.stream.tell() >= _log_handler.maxBytes:
                _log_handler.doRollover()
            
    except Exception as e:
        # Silently fail - logging should never break the tool
        print(f"Warning: Failed to write log entry: {e}", file=sys.stderr)


def log_tool_call(
    tool_name: str,
    parameters: dict[str, Any],
    result: dict[str, Any] | None = None,
    error: str | None = None,
    execution_time_ms: float | None = None,
    database: str | None = None,
    dialect: str | None = None,
) -> None:
    """
    Log a tool call with its parameters and result.
    
    Args:
        tool_name: Name of the tool called
        parameters: Input parameters (will be truncated if too large)
        result: Result dictionary (success case)
        error: Error message (failure case)
        execution_time_ms: Execution time in milliseconds
        database: Database name used
        dialect: SQL dialect (access, mssql, postgres)
    """
    if not _initialize_logging():
        return
    
    # Truncate large parameters (like long SQL queries)
    sanitized_params = _sanitize_parameters(parameters)
    
    entry = {
        "event": "tool_call",
        "tool": tool_name,
        "database": database,
        "dialect": dialect,
        "parameters": sanitized_params,
        "success": error is None,
        "execution_time_ms": execution_time_ms,
    }
    
    if error:
        entry["error"] = _truncate_string(error, max_length=500)
        # Extract error pattern for easier analysis
        entry["error_pattern"] = _extract_error_pattern(error)
    
    if result and "error" in result:
        # Tool returned an error in the result dict
        entry["success"] = False
        entry["error"] = _truncate_string(str(result.get("error", "")), max_length=500)
        entry["error_pattern"] = _extract_error_pattern(str(result.get("error", "")))
    
    _write_log_entry(entry)


def _sanitize_parameters(params: dict[str, Any], max_string_length: int = 500) -> dict[str, Any]:
    """
    Sanitize parameters for logging by truncating large values.
    
    Args:
        params: Original parameters
        max_string_length: Maximum length for string values
        
    Returns:
        Sanitized parameters dictionary
    """
    sanitized = {}
    for key, value in params.items():
        if isinstance(value, str):
            sanitized[key] = _truncate_string(value, max_string_length)
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_parameters(value, max_string_length)
        elif isinstance(value, list):
            sanitized[key] = [
                _truncate_string(v, max_string_length) if isinstance(v, str) else v
                for v in value[:10]  # Limit list length
            ]
            if len(value) > 10:
                sanitized[key].append(f"... ({len(value) - 10} more items)")
        else:
            sanitized[key] = value
    return sanitized


def _truncate_string(s: str, max_length: int = 500) -> str:
    """Truncate a string if it exceeds max_length."""
    if len(s) <= max_length:
        return s
    return s[:max_length] + f"... (truncated, {len(s)} chars total)"


def _extract_error_pattern(error: str) -> str:
    """
    Extract a normalized error pattern for categorization.
    
    This helps identify common error types for analysis.
    """
    error_lower = error.lower()
    
    # Common Access SQL errors
    if "missing operator" in error_lower:
        if "join" in error_lower or "inner join" in error_lower:
            return "missing_operator_join"
        elif "case" in error_lower:
            return "missing_operator_case"
        return "missing_operator_other"
    
    if "syntax error" in error_lower:
        if "limit" in error_lower:
            return "syntax_error_limit"
        return "syntax_error_other"
    
    if "data type mismatch" in error_lower:
        return "data_type_mismatch"
    
    if "no value given" in error_lower:
        return "no_value_given"
    
    if "too few parameters" in error_lower:
        return "too_few_parameters"

    if "prevents it from being opened or locked" in error_lower:
        return "database_exclusive_lock"

    if "file already in use" in error_lower:
        return "file_in_use"

    if "cannot find the input table or query" in error_lower:
        return "table_not_found"

    if "join on memo" in error_lower or "join on ole" in error_lower:
        return "join_unsupported_type"

    if "join expression not supported" in error_lower:
        return "join_not_supported"

    if "permission" in error_lower or "access denied" in error_lower:
        return "permission_denied"
    
    if "timeout" in error_lower:
        return "timeout"
    
    if "connection" in error_lower:
        return "connection_error"
    
    # Encoding / serialization errors
    if "utf-8" in error_lower or "utf8" in error_lower:
        return "encoding_utf8"
    if "unicode" in error_lower or "encode" in error_lower or "decode" in error_lower:
        return "encoding_unicode"
    if "serializ" in error_lower or "not json serializable" in error_lower:
        return "serialization_error"
    
    # Generic categorization
    if "error" in error_lower:
        return "generic_error"
    
    return "unknown"


def with_logging(tool_name: str):
    """
    Decorator to add logging to a tool function.
    
    Supports both sync and async tool functions.
    
    Usage:
        @with_logging("db_count_query_results")
        def db_count_query_results(query: str, database: str | None = None) -> dict:
            ...

        @with_logging("db_list_databases")
        async def db_list_databases(ctx: Context) -> dict:
            ...
    
    Args:
        tool_name: Name of the tool for logging purposes
        
    Returns:
        Decorator function
    """
    def _log_call(func, args, kwargs, result, error_msg, serialization_warning, start_time):
        """Shared logging logic for sync and async wrappers."""
        execution_time_ms = (time.time() - start_time) * 1000
        database = kwargs.get("database")
        dialect = None
        try:
            from .backends.registry import get_registry
            registry = get_registry()
            if registry.list_backends():
                backend = registry.get(database)
                dialect = getattr(backend, "sql_dialect", None)
        except Exception:
            pass

        import inspect
        parameters = dict(kwargs)
        sig = inspect.signature(func)
        param_names = list(sig.parameters.keys())
        for i, arg in enumerate(args):
            if i < len(param_names):
                parameters[param_names[i]] = arg

        logged_error = error_msg or serialization_warning
        log_tool_call(
            tool_name=tool_name,
            parameters=parameters,
            result=result,
            error=logged_error,
            execution_time_ms=round(execution_time_ms, 2),
            database=database,
            dialect=dialect,
        )

    def _check_serialization(result):
        """Validate result is JSON-serializable, return (result, warning)."""
        if result is None:
            return result, None
        try:
            json.dumps(result, default=str)
            return result, None
        except (TypeError, ValueError, UnicodeEncodeError) as ser_err:
            warning = (
                f"Result passed tool execution but failed JSON serialization: "
                f"{type(ser_err).__name__}: {ser_err}"
            )
            error_result = {
                "error": (
                    f"Query succeeded but the result contains values that "
                    f"cannot be serialized to JSON ({type(ser_err).__name__}: {ser_err}). "
                    f"This usually means a column returns binary data (e.g., timestamp/rowversion). "
                    f"Try selecting specific columns instead of SELECT *."
                )
            }
            return error_result, warning

    def decorator(func: Callable) -> Callable:
        import asyncio
        import inspect as _inspect

        if _inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs) -> Any:
                if not _initialize_logging():
                    return await func(*args, **kwargs)

                start_time = time.time()
                error_msg = None
                result = None
                serialization_warning = None
                try:
                    result = await func(*args, **kwargs)
                    result, serialization_warning = _check_serialization(result)
                    return result
                except Exception as e:
                    error_msg = str(e)
                    raise
                finally:
                    _log_call(func, args, kwargs, result, error_msg, serialization_warning, start_time)

            return async_wrapper
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs) -> Any:
                if not _initialize_logging():
                    return func(*args, **kwargs)

                start_time = time.time()
                error_msg = None
                result = None
                serialization_warning = None
                try:
                    result = func(*args, **kwargs)
                    result, serialization_warning = _check_serialization(result)
                    return result
                except Exception as e:
                    error_msg = str(e)
                    raise
                finally:
                    _log_call(func, args, kwargs, result, error_msg, serialization_warning, start_time)

            return wrapper
    return decorator


def get_log_file_path() -> Path | None:
    """
    Get the current log file path.
    
    Returns:
        Path to log file if logging is enabled, None otherwise.
    """
    if _initialize_logging():
        return _log_file
    return None


def is_logging_enabled() -> bool:
    """Check if logging is currently enabled."""
    return _initialize_logging()
