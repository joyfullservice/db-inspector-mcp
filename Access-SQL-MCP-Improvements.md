# Access SQL Syntax Guide for MCP Tool Enhancement

## Overview

This document captures real-world Access SQL syntax issues encountered while using the db-inspector-mcp tool with a Microsoft Access database. These observations are intended to guide improvements to the MCP tool's documentation, error handling, and user guidance.

---

## Issue 1: Multiple JOINs Require Parentheses (Most Common)

### Problem

Access SQL uses a non-standard syntax for queries with multiple JOINs. Standard SQL JOIN syntax fails with cryptic errors like:

```
Syntax error (missing operator) in query expression 'table1.col = table2.col
INNER JOIN table3 ON table2.col = table3.col'
```

### What Failed

```sql
-- Standard SQL syntax (works in SQL Server, PostgreSQL, MySQL, etc.)
SELECT *
FROM tblPfCoPfRevFund prf
INNER JOIN tblFundGroup fg ON prf.fldFundGroupID = fg.fldFundGroupID
INNER JOIN tblFunds f ON fg.fldMainFund = f.fldFundID
WHERE f.fldFundLocationID = 3
```

### What Works

```sql
-- Access requires parentheses wrapping JOINs from left to right
SELECT *
FROM (tblPfCoPfRevFund prf
INNER JOIN tblFundGroup fg ON prf.fldFundGroupID = fg.fldFundGroupID)
INNER JOIN tblFunds f ON fg.fldMainFund = f.fldFundID
WHERE f.fldFundLocationID = 3
```

### Pattern for N Tables

```sql
-- 2 tables: no parentheses needed
FROM A INNER JOIN B ON A.id = B.a_id

-- 3 tables: one set of parentheses
FROM (A INNER JOIN B ON A.id = B.a_id)
INNER JOIN C ON B.id = C.b_id

-- 4 tables: nested parentheses
FROM ((A INNER JOIN B ON A.id = B.a_id)
INNER JOIN C ON B.id = C.b_id)
INNER JOIN D ON C.id = D.c_id
```

### Suggested MCP Enhancement

When an error message contains "missing operator" near a JOIN keyword, return a hint like:

> **Access SQL Tip:** Multiple JOINs require parentheses. Wrap each additional JOIN:
> `FROM ((A INNER JOIN B ON ...) INNER JOIN C ON ...) INNER JOIN D ON ...`

---

## Issue 2: DISTINCT Keyword Placement

### Problem

Some forms of `SELECT DISTINCT` can fail with "missing operator" errors.

### What Failed

```sql
SELECT DISTINCT pr.fldFundID FROM tblPfCoPfRev pr WHERE pr.fldFundID IS NOT NULL
```

### What Works

```sql
-- Use GROUP BY instead for reliability
SELECT pr.fldFundID FROM tblPfCoPfRev pr 
WHERE pr.fldFundID IS NOT NULL 
GROUP BY pr.fldFundID
```

### Suggested MCP Enhancement

Include a note that `GROUP BY` is often more reliable than `DISTINCT` in Access, especially with complex expressions.

---

## Issue 3: IIF() Instead of CASE WHEN

### Problem

Access uses `IIF()` function instead of standard SQL `CASE WHEN` expressions.

### Standard SQL (Fails in Access)

```sql
SELECT 
  CASE WHEN status = 'active' THEN 1 ELSE 0 END AS is_active
FROM users
```

### Access SQL (Works)

```sql
SELECT 
  IIF(status = 'active', 1, 0) AS is_active
FROM users
```

### Nested Conditionals

```sql
-- Instead of CASE WHEN...WHEN...ELSE, nest IIF calls:
IIF(condition1, result1, IIF(condition2, result2, default_result))
```

### Suggested MCP Enhancement

When the query contains `CASE WHEN`, suggest using `IIF(condition, true_value, false_value)` instead.

---

## Issue 4: Boolean Literals and Comparisons

### Problem

Access uses `True`/`False` keywords for boolean values, not `1`/`0` in all contexts.

### Examples

```sql
-- Comparing to boolean field
WHERE fldIsActive = True

-- In IIF expressions, both work but True/False is idiomatic
IIF(fldFundLocationID = 3, True, False)
```

---

## Issue 5: Aggregate Functions with IIF for Conditional Counting

### Pattern That Works Well

This pattern was used successfully for identifying IDG-only records:

```sql
SELECT fldCoID
FROM tblCoLpFundsInvest lp
INNER JOIN tblFunds f ON lp.fldFundID = f.fldFundID
GROUP BY lp.fldCoID
HAVING Sum(IIF(f.fldFundLocationID=3 And f.fldFundLocationMgmtID=3, 1, 0)) > 0
  And Sum(IIF(f.fldFundLocationID<>3 Or f.fldFundLocationMgmtID<>3, 1, 0)) = 0
```

### Key Points

- `Sum(IIF(..., 1, 0))` is the Access equivalent of `COUNT(*) FILTER (WHERE ...)` or conditional aggregation
- Use `And`/`Or` keywords (not `&&`/`||`)
- Comparison operators: `=`, `<>`, `<`, `>`, `<=`, `>=`

---

## Issue 6: String and Date Literals

### Strings

```sql
-- Use single quotes for strings
WHERE fldFundID = 'IDGMain'
```

### Dates

```sql
-- Use # delimiters for date literals
WHERE fldDate = #2024-01-15#
WHERE fldDate >= #2024-01-01# AND fldDate < #2025-01-01#
```

---

## Issue 7: TOP Instead of LIMIT

### Standard SQL (Fails)

```sql
SELECT * FROM users LIMIT 10
```

### Access SQL (Works)

```sql
SELECT TOP 10 * FROM users
```

---

## Issue 8: Wildcard Characters in LIKE

### Standard SQL

```sql
WHERE name LIKE '%Smith%'
WHERE code LIKE 'A_1'
```

### Access SQL

```sql
WHERE name LIKE '*Smith*'
WHERE code LIKE 'A?1'
```

| Standard | Access | Meaning |
|----------|--------|---------|
| `%` | `*` | Zero or more characters |
| `_` | `?` | Single character |

---

## Recommended MCP Tool Enhancements

### 1. Add Access SQL Hints to Tool Descriptions

Include a condensed version of these tips in the MCP server's instruction block:

```
## Access SQL Syntax Notes

- **Multiple JOINs**: Wrap in parentheses: `FROM ((A JOIN B ON ...) JOIN C ON ...)`
- **Conditionals**: Use `IIF(cond, true, false)` not CASE WHEN
- **Boolean**: Use `True`/`False` keywords
- **Wildcards**: Use `*` and `?` in LIKE, not `%` and `_`
- **Dates**: Use `#2024-01-15#` format
- **Limit rows**: Use `SELECT TOP N` not `LIMIT N`
- **Logical ops**: Use `And`/`Or` not `&&`/`||`
```

### 2. Enhance Error Messages with Contextual Hints

When returning errors, detect common patterns and append helpful suggestions:

| Error Pattern | Suggested Hint |
|--------------|----------------|
| "missing operator" + JOIN in query | Suggest parentheses around JOINs |
| "missing operator" + CASE | Suggest using IIF() instead |
| Syntax error near LIMIT | Suggest using TOP instead |
| Wildcard-related errors | Suggest * and ? instead of % and _ |

### 3. Add a Syntax Help Tool (Optional)

Consider adding a `db_access_sql_help` tool that returns syntax examples for common operations:

```python
def db_access_sql_help(topic: str = None) -> dict:
    """
    Returns Access SQL syntax help.
    Topics: joins, conditionals, dates, wildcards, aggregates, limits
    """
```

### 4. Query Validation/Transformation (Advanced)

For a more advanced enhancement, consider pre-processing queries to:
- Detect standard SQL patterns that will fail
- Suggest or auto-transform to Access syntax
- Validate parentheses around JOINs before execution

---

## Summary Table

| Feature | Standard SQL | Access SQL |
|---------|-------------|------------|
| Multiple JOINs | No parentheses | Parentheses required |
| Conditionals | CASE WHEN | IIF() |
| Boolean | 1/0 or true/false | True/False |
| Row limit | LIMIT N | TOP N |
| Wildcard (any) | % | * |
| Wildcard (one) | _ | ? |
| Date literal | '2024-01-15' | #2024-01-15# |
| Logical AND | AND or && | And |
| Logical OR | OR or \|\| | Or |
| Not equal | != or <> | <> |

---

## Document History

- **Created**: 2026-02-05
- **Context**: Real-world experience using db-inspector-mcp with SecTbl.accdb Access database
- **Purpose**: Guide MCP tool improvements for better Access SQL support
