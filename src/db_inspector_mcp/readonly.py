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



    Args:

        config: Configuration dict with DB_MCP_VERIFY_READONLY and

            DB_MCP_READONLY_FAIL_ON_WRITE keys.

        registry: BackendRegistry to verify.

        exit_on_write_failure: When True (startup path), call sys.exit(1) if

            write permissions are detected and DB_MCP_READONLY_FAIL_ON_WRITE

            is set. When False (per-workspace lazy path), raise ValueError

            instead so other workspaces are unaffected.

    """

    verify_readonly = config.get("DB_MCP_VERIFY_READONLY", "true").lower() == "true"

    if not verify_readonly:

        return



    env_fail_on_write = (

        config.get("DB_MCP_READONLY_FAIL_ON_WRITE", "false").lower() == "true"

    )

    write_failures: list[str] = []



    for backend_name in registry.list_backends():

        try:

            backend = registry.get(backend_name)

            result = _verify_readonly_bounded(backend, _VERIFY_READONLY_TIMEOUT_SECONDS)



            if result.get("readonly") is None:

                print(

                    f"[{backend_name}] ⚠ Could not verify read-only status: "

                    f"{result.get('details', 'unknown')}",

                    file=sys.stderr,

                )

                continue



            readonly_status = "✓ Read-only" if result["readonly"] else "⚠ Write permissions detected"

            print(f"[{backend_name}] {readonly_status}: {result['details']}", file=sys.stderr)



            if not result["readonly"] and env_fail_on_write:

                write_failures.append(backend_name)

        except Exception as e:

            print(

                f"Warning: Could not verify read-only status for '{backend_name}': {e}",

                file=sys.stderr,

            )



    if write_failures and env_fail_on_write:

        names = ", ".join(write_failures)

        message = (

            f"Write permissions detected on: {names}. "

            "Set DB_MCP_READONLY_FAIL_ON_WRITE=false to allow, or fix connection permissions."

        )

        if exit_on_write_failure:

            print(f"Failing startup: {message}", file=sys.stderr)

            sys.exit(1)

        raise ValueError(message)

