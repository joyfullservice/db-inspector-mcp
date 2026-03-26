"""Subprocess worker for Access ODBC query execution.

This module runs as a child process to isolate Jet/ACE engine state from the
main MCP server process.  On timeout the parent calls ``process.kill()``,
which guarantees release of all file locks — something that is impossible
with in-process threads because the Access ODBC driver does not support
``SQLCancel`` or ``SQL_ATTR_QUERY_TIMEOUT``.

Protocol (JSON over stdin/stdout):

    Request (stdin, single JSON object):
        {
            "connection_string": "Driver=...;DBQ=...;",
            "operation": "count",           # count | columns | sum | measure | preview
            "sql": "SELECT ...",
            "params": { ... }               # operation-specific (optional)
        }

    Response (stdout, single JSON object):
        {"ok": <result>}                    # success
        {"error": "<message>", "type": "<exception class>"}  # failure

Usage:
    python -m db_inspector_mcp.backends._odbc_worker
"""

import json
import sys
import time
from datetime import date, datetime
from datetime import time as dt_time
from decimal import Decimal
from uuid import UUID


def _sanitize_value(value):
    """Convert a single database value to a JSON-serializable type."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return f"0x{value.hex()}"
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dt_time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            value.encode("utf-8")
            return value
        except UnicodeEncodeError:
            return value.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(value, (int, float, bool)):
        return value
    return str(value)


def _sanitize_rows(column_names, rows):
    """Convert cursor rows into JSON-safe list of dicts."""
    return [
        {col: _sanitize_value(val) for col, val in zip(column_names, row)}
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Operation handlers — each receives (cursor, sql, params) and returns a
# JSON-serializable result.
# ---------------------------------------------------------------------------

def _op_count(cursor, sql, _params):
    cursor.execute(sql)
    row = cursor.fetchone()
    return _sanitize_value(row[0]) if row else 0


def _op_columns(cursor, sql, _params):
    cursor.execute(sql)
    columns = []
    for col in cursor.description or []:
        if col:
            columns.append({
                "name": col[0],
                "type": str(col[1]),
                "nullable": col[6] if len(col) > 6 else None,
                "precision": col[4] if len(col) > 4 and col[4] else None,
                "scale": col[5] if len(col) > 5 and col[5] else None,
            })
    return columns


def _op_sum(cursor, sql, _params):
    cursor.execute(sql)
    row = cursor.fetchone()
    return _sanitize_value(row[0]) if row and row[0] is not None else None


def _op_measure(cursor, sql, _params):
    start = time.time()
    cursor.execute(sql)
    rows = cursor.fetchall()
    elapsed_ms = (time.time() - start) * 1000

    col_names = [c[0] for c in cursor.description] if cursor.description else []
    result_rows = _sanitize_rows(col_names, rows)

    return {
        "execution_time_ms": round(elapsed_ms, 2),
        "row_count": len(result_rows),
        "hit_limit": len(result_rows) >= (_params or {}).get("max_rows", len(result_rows) + 1),
    }


def _op_preview(cursor, sql, _params):
    cursor.execute(sql)
    rows = cursor.fetchall()
    col_names = [c[0] for c in cursor.description] if cursor.description else []
    return _sanitize_rows(col_names, rows)


_OPERATIONS = {
    "count": _op_count,
    "columns": _op_columns,
    "sum": _op_sum,
    "measure": _op_measure,
    "preview": _op_preview,
}


def main():
    import pyodbc

    raw = sys.stdin.buffer.read()
    request = json.loads(raw)

    conn_str = request["connection_string"]
    operation = request["operation"]
    sql = request["sql"]
    params = request.get("params")

    handler = _OPERATIONS.get(operation)
    if handler is None:
        json.dump({"error": f"Unknown operation: {operation}", "type": "ValueError"}, sys.stdout)
        sys.exit(1)

    try:
        conn = pyodbc.connect(conn_str, timeout=30)
        try:
            cursor = conn.cursor()
            try:
                result = handler(cursor, sql, params)
                json.dump({"ok": result}, sys.stdout)
            finally:
                cursor.close()
        finally:
            conn.close()
    except Exception as exc:
        json.dump({"error": str(exc), "type": type(exc).__name__}, sys.stdout)
        sys.exit(1)


if __name__ == "__main__":
    main()
