"""Read-only verification for database backends."""

import sys
import threading

_VERIFY_READONLY_TIMEOUT_SECONDS = 10.0


def _verify_readonly_bounded(backend, timeout: float) -> dict:
    """Call ``backend.verify_readonly()`` with a wall-clock timeout."""
    result: dict = {}

    def _worker() -> None:
        try:
            result["value"] = backend.verify_readonly()
        except BaseException as exc:  # noqa: BLE001
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


def verify_readonly_for_registry(
    config: dict,
    registry,
    *,
    exit_on_write_failure: bool = False,
) -> None:
    """Verify read-only status for all registered backends.

    When ``DB_MCP_VERIFY_READONLY`` is true (default), every verifiable backend
    must be confirmed read-only. Write permissions detected or an inconclusive
    check (timeout/error) fail closed. Set ``DB_MCP_VERIFY_READONLY=false`` to
    skip verification entirely.

    Args:
        config: Configuration dict with DB_MCP_VERIFY_READONLY key.
        registry: BackendRegistry to verify.
        exit_on_write_failure: When True (startup path), call sys.exit(1) on
            failure. When False (per-workspace lazy path), raise ValueError
            instead so other workspaces are unaffected.
    """
    verify_readonly = config.get("DB_MCP_VERIFY_READONLY", "true").lower() == "true"
    if not verify_readonly:
        return

    write_detected: list[str] = []
    inconclusive: list[str] = []

    for backend_name in registry.list_backends():
        try:
            backend = registry.get(backend_name)
            if getattr(backend, "sql_dialect", None) == "access":
                continue
            result = _verify_readonly_bounded(backend, _VERIFY_READONLY_TIMEOUT_SECONDS)

            if result.get("readonly") is None:
                detail = result.get("details", "unknown")
                print(
                    f"[{backend_name}] ⚠ Could not verify read-only status: {detail}",
                    file=sys.stderr,
                )
                inconclusive.append(f"{backend_name} ({detail})")
                continue

            readonly_status = (
                "✓ Read-only" if result["readonly"] else "⚠ Write permissions detected"
            )
            print(f"[{backend_name}] {readonly_status}: {result['details']}", file=sys.stderr)

            if not result["readonly"]:
                write_detected.append(backend_name)
        except Exception as e:
            print(
                f"Warning: Could not verify read-only status for '{backend_name}': {e}",
                file=sys.stderr,
            )
            inconclusive.append(f"{backend_name} ({e})")

    if not write_detected and not inconclusive:
        return

    parts: list[str] = []
    if write_detected:
        parts.append(
            f"Write permissions detected on: {', '.join(write_detected)}"
        )
    if inconclusive:
        parts.append(
            "Could not verify read-only status for: "
            + "; ".join(inconclusive)
        )
    message = (
        ". ".join(parts)
        + ". Set DB_MCP_VERIFY_READONLY=false to skip verification, "
        "or fix connection permissions."
    )
    if exit_on_write_failure:
        print(f"Failing startup: {message}", file=sys.stderr)
        sys.exit(1)
    raise ValueError(message)
