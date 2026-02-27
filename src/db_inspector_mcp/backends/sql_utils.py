"""SQL query manipulation utilities for TOP N injection and subquery wrapping.

Handles edge cases that naive string manipulation misses:
- Leading/trailing whitespace before SELECT
- SELECT DISTINCT / SELECT ALL modifiers
- Common Table Expressions (WITH ... AS)
"""

from __future__ import annotations

import re

_SELECT_MODIFIER_RE = re.compile(
    r"^SELECT\s+(DISTINCT|ALL)\s+", re.IGNORECASE
)


def _find_final_select_pos(sql: str) -> int | None:
    """Find position of the final top-level SELECT keyword.

    Tracks parenthesis depth and single-quoted strings to skip SELECTs
    inside subqueries, CTEs, and string literals.  Returns the character
    index of 'S' in the last depth-0 SELECT, or None.
    """
    upper = sql.upper()
    depth = 0
    last_pos: int | None = None
    i = 0
    length = len(upper)

    while i < length:
        ch = upper[i]

        # Skip single-quoted string literals (SQL standard)
        if ch == "'":
            i += 1
            while i < length:
                if upper[i] == "'":
                    if i + 1 < length and upper[i + 1] == "'":
                        i += 2  # escaped quote ('')
                        continue
                    break
                i += 1
            i += 1
            continue

        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and ch == "S" and upper[i : i + 6] == "SELECT":
            before_ok = (
                i == 0
                or not (upper[i - 1].isalnum() or upper[i - 1] == "_")
            )
            end = i + 6
            after_ok = end >= length or not (
                upper[end].isalnum() or upper[end] == "_"
            )
            if before_ok and after_ok:
                last_pos = i

        i += 1

    return last_pos


def split_cte_prefix(query: str) -> tuple[str, str]:
    """Split a CTE query into ``(cte_definitions, final_select)``.

    If *query* begins with ``WITH`` (a Common Table Expression), the CTE
    definitions are returned as the first element and the final ``SELECT``
    (at parenthesis depth 0) as the second.

    If *query* is not a CTE, returns ``("", query)``.

    Leading/trailing whitespace is stripped from the input.
    """
    stripped = query.strip()
    upper = stripped.upper()

    if not upper.startswith("WITH"):
        return ("", stripped)
    # Guard against words like WITHDRAW — WITH must be a full keyword
    if len(upper) > 4 and (upper[4].isalnum() or upper[4] == "_"):
        return ("", stripped)

    pos = _find_final_select_pos(stripped)
    if pos is None or pos == 0:
        return ("", stripped)

    return (stripped[:pos], stripped[pos:])


def inject_top_clause(query: str, n: int) -> str:
    """Inject ``TOP n`` into a SQL query.

    Correctly handles:

    * leading / trailing whitespace
    * ``SELECT DISTINCT`` / ``SELECT ALL`` modifiers
    * CTE queries (``WITH … AS (…) SELECT …``)
    * queries that already contain ``TOP`` (returned after stripping)
    """
    query = query.strip()

    if "TOP " in query.upper():
        return query

    cte_prefix, core = split_cte_prefix(query)
    core = core.lstrip()
    core_upper = core.upper()

    if not core_upper.startswith("SELECT"):
        raise ValueError(
            f"Cannot inject TOP into a non-SELECT statement: "
            f"{core[:40]}..."
        )

    # Check for DISTINCT / ALL modifier
    m = _SELECT_MODIFIER_RE.match(core)
    if m:
        modifier = m.group(1).upper()
        after_modifier = core[m.end() :]
        return f"{cte_prefix}SELECT {modifier} TOP {n} {after_modifier.lstrip()}"

    after_select = core[6:].lstrip()  # skip "SELECT"
    return f"{cte_prefix}SELECT TOP {n} {after_select}"
