"""Tests for SQL query manipulation utilities (inject_top_clause, split_cte_prefix)."""

import pytest

from db_inspector_mcp.backends.sql_utils import (
    _find_final_select_pos,
    inject_top_clause,
    split_cte_prefix,
)


# ---------------------------------------------------------------------------
# _find_final_select_pos
# ---------------------------------------------------------------------------

class TestFindFinalSelectPos:

    def test_simple_select(self):
        assert _find_final_select_pos("SELECT col FROM t") == 0

    def test_select_with_subquery(self):
        sql = "SELECT * FROM (SELECT 1 AS x) AS sub"
        pos = _find_final_select_pos(sql)
        assert pos == 0  # outer SELECT, not inner

    def test_cte_finds_final_select(self):
        sql = "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"
        pos = _find_final_select_pos(sql)
        assert sql[pos:pos + 6] == "SELECT"
        assert pos == 28  # the final SELECT after the CTE

    def test_multiple_ctes(self):
        sql = "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a, b"
        pos = _find_final_select_pos(sql)
        assert sql[pos:].startswith("SELECT * FROM a, b")

    def test_select_inside_string_literal_skipped(self):
        sql = "SELECT * FROM t WHERE name = 'SELECT'"
        pos = _find_final_select_pos(sql)
        assert pos == 0  # only the real SELECT, not the one in the string

    def test_no_select(self):
        assert _find_final_select_pos("INSERT INTO t VALUES (1)") is None

    def test_keyword_boundary_not_crossselect(self):
        sql = "CROSSSELECT x"
        assert _find_final_select_pos(sql) is None

    def test_keyword_boundary_not_selectall_word(self):
        sql = "SELECTALL x"
        assert _find_final_select_pos(sql) is None

    def test_nested_parens(self):
        sql = "WITH a AS (SELECT (SELECT 1) AS x) SELECT x FROM a"
        pos = _find_final_select_pos(sql)
        assert sql[pos:].startswith("SELECT x FROM a")


# ---------------------------------------------------------------------------
# split_cte_prefix
# ---------------------------------------------------------------------------

class TestSplitCtePrefix:

    def test_non_cte_returns_empty_prefix(self):
        cte, core = split_cte_prefix("SELECT * FROM t")
        assert cte == ""
        assert core == "SELECT * FROM t"

    def test_non_cte_with_whitespace(self):
        cte, core = split_cte_prefix("  \n  SELECT * FROM t  \n  ")
        assert cte == ""
        assert core == "SELECT * FROM t"

    def test_simple_cte(self):
        sql = "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"
        cte, core = split_cte_prefix(sql)
        assert cte == "WITH cte AS (SELECT 1 AS x) "
        assert core == "SELECT x FROM cte"

    def test_multiple_ctes(self):
        sql = "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a, b"
        cte, core = split_cte_prefix(sql)
        assert core.startswith("SELECT * FROM a, b")
        assert "WITH" in cte

    def test_cte_with_leading_whitespace(self):
        sql = "  \n  WITH cte AS (SELECT 1 AS x) SELECT x FROM cte  "
        cte, core = split_cte_prefix(sql)
        assert core == "SELECT x FROM cte"
        assert "WITH" in cte

    def test_withdraw_not_detected_as_cte(self):
        cte, core = split_cte_prefix("WITHDRAW funds")
        assert cte == ""
        assert core == "WITHDRAW funds"

    def test_with_underscore_not_detected_as_cte(self):
        cte, core = split_cte_prefix("WITH_OPTION x")
        assert cte == ""
        assert core == "WITH_OPTION x"


# ---------------------------------------------------------------------------
# inject_top_clause — Bug 1: leading whitespace
# ---------------------------------------------------------------------------

class TestInjectTopWhitespace:
    """Bug 1: Leading whitespace breaks TOP N injection."""

    def test_no_whitespace(self):
        result = inject_top_clause("SELECT col FROM t", 10)
        assert result == "SELECT TOP 10 col FROM t"

    def test_leading_newline(self):
        result = inject_top_clause("\nSELECT col FROM t", 10)
        assert result == "SELECT TOP 10 col FROM t"

    def test_leading_spaces(self):
        result = inject_top_clause("   SELECT col FROM t", 10)
        assert result == "SELECT TOP 10 col FROM t"

    def test_leading_and_trailing_whitespace(self):
        result = inject_top_clause("  \n  SELECT col FROM t  \n  ", 10)
        assert result == "SELECT TOP 10 col FROM t"

    def test_multiline_query_with_leading_newline(self):
        sql = "\nSELECT fldCoID, fldCoName FROM dbo.tblCo WHERE fldRecStatID <> 4"
        result = inject_top_clause(sql, 10)
        assert result.startswith("SELECT TOP 10 ")
        assert "fldCoID" in result
        assert "T'" not in result  # no fragmented 'T' column

    def test_trailing_whitespace_stripped(self):
        result = inject_top_clause("SELECT col FROM t   ", 5)
        assert result == "SELECT TOP 5 col FROM t"


# ---------------------------------------------------------------------------
# inject_top_clause — Bug 2: DISTINCT / ALL
# ---------------------------------------------------------------------------

class TestInjectTopDistinct:
    """Bug 2: DISTINCT not handled by TOP N injection."""

    def test_select_distinct(self):
        result = inject_top_clause("SELECT DISTINCT col FROM t", 10)
        assert result == "SELECT DISTINCT TOP 10 col FROM t"

    def test_select_distinct_case_insensitive(self):
        result = inject_top_clause("select distinct col FROM t", 10)
        assert result == "SELECT DISTINCT TOP 10 col FROM t"

    def test_select_all(self):
        result = inject_top_clause("SELECT ALL col FROM t", 10)
        assert result == "SELECT ALL TOP 10 col FROM t"

    def test_select_distinct_with_leading_whitespace(self):
        result = inject_top_clause("\nSELECT DISTINCT col FROM t\n", 10)
        assert result == "SELECT DISTINCT TOP 10 col FROM t"

    def test_select_distinct_multiline(self):
        sql = "SELECT DISTINCT\n    co.fldCoID, co.fldCoName\nFROM dbo.tblCo co"
        result = inject_top_clause(sql, 10)
        assert result.startswith("SELECT DISTINCT TOP 10")
        assert "co.fldCoID" in result

    def test_already_has_top(self):
        sql = "SELECT TOP 5 col FROM t"
        result = inject_top_clause(sql, 10)
        assert result == sql  # unchanged

    def test_already_has_distinct_top(self):
        sql = "SELECT DISTINCT TOP 5 col FROM t"
        result = inject_top_clause(sql, 10)
        assert result == sql  # unchanged


# ---------------------------------------------------------------------------
# inject_top_clause — Bug 3: CTEs
# ---------------------------------------------------------------------------

class TestInjectTopCte:
    """Bug 3: CTE queries break TOP N injection."""

    def test_simple_cte(self):
        sql = "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"
        result = inject_top_clause(sql, 10)
        assert result == "WITH cte AS (SELECT 1 AS x) SELECT TOP 10 x FROM cte"

    def test_cte_with_distinct(self):
        sql = "WITH cte AS (SELECT 1 AS x) SELECT DISTINCT x FROM cte"
        result = inject_top_clause(sql, 10)
        assert result == "WITH cte AS (SELECT 1 AS x) SELECT DISTINCT TOP 10 x FROM cte"

    def test_cte_with_leading_whitespace(self):
        sql = "\nWITH cte AS (SELECT 1 AS x) SELECT x FROM cte\n"
        result = inject_top_clause(sql, 10)
        assert "SELECT TOP 10 x FROM cte" in result
        assert result.startswith("WITH")

    def test_multiple_ctes(self):
        sql = (
            "WITH a AS (SELECT 1 AS x), b AS (SELECT 2 AS y) "
            "SELECT a.x, b.y FROM a, b"
        )
        result = inject_top_clause(sql, 5)
        assert "SELECT TOP 5 a.x, b.y FROM a, b" in result
        assert result.startswith("WITH a AS")

    def test_cte_real_world_query(self):
        sql = (
            "WITH RolesFunds AS (\n"
            "    SELECT ccrf.fldCoConRolesID, ccrf.fldFundID\n"
            "    FROM dbo.tblCoConRolesFunds ccrf\n"
            "    WHERE ccrf.fldRecStatID <> 4\n"
            ")\n"
            "SELECT DISTINCT ccr.fldConRoleID, rf.fldFundID\n"
            "FROM dbo.tblCoConRoles ccr\n"
            "LEFT JOIN RolesFunds rf ON ccr.fldCoConRolesID = rf.fldCoConRolesID\n"
            "WHERE ccr.fldRecStatID <> 4"
        )
        result = inject_top_clause(sql, 100)
        assert result.startswith("WITH RolesFunds AS")
        assert "SELECT DISTINCT TOP 100 ccr.fldConRoleID" in result
        # The CTE's internal SELECT should NOT have TOP injected
        assert result.count("TOP") == 1


# ---------------------------------------------------------------------------
# split_cte_prefix — CTE subquery wrapping (Bug 3)
# ---------------------------------------------------------------------------

class TestCteSubqueryWrapping:
    """Bug 3: CTE queries break subquery wrapping tools."""

    def test_count_wrap_no_cte(self):
        query = "SELECT * FROM t"
        cte, core = split_cte_prefix(query)
        wrapped = f"{cte}SELECT COUNT(*) AS cnt FROM ({core}) AS subquery"
        assert wrapped == "SELECT COUNT(*) AS cnt FROM (SELECT * FROM t) AS subquery"

    def test_count_wrap_with_cte(self):
        query = "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"
        cte, core = split_cte_prefix(query)
        wrapped = f"{cte}SELECT COUNT(*) AS cnt FROM ({core}) AS subquery"
        assert wrapped == (
            "WITH cte AS (SELECT 1 AS x) "
            "SELECT COUNT(*) AS cnt FROM (SELECT x FROM cte) AS subquery"
        )

    def test_columns_wrap_with_cte(self):
        query = "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"
        cte, core = split_cte_prefix(query)
        wrapped = f"{cte}SELECT TOP 0 * FROM ({core}) AS subquery"
        assert wrapped == (
            "WITH cte AS (SELECT 1 AS x) "
            "SELECT TOP 0 * FROM (SELECT x FROM cte) AS subquery"
        )

    def test_sum_wrap_with_cte(self):
        query = "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"
        cte, core = split_cte_prefix(query)
        column = "x"
        wrapped = f"{cte}SELECT SUM([{column}]) AS sum_val FROM ({core}) AS subquery"
        assert wrapped == (
            "WITH cte AS (SELECT 1 AS x) "
            "SELECT SUM([x]) AS sum_val FROM (SELECT x FROM cte) AS subquery"
        )

    def test_wrap_with_leading_whitespace(self):
        query = "\n  WITH cte AS (SELECT 1 AS x) SELECT x FROM cte  \n"
        cte, core = split_cte_prefix(query)
        wrapped = f"{cte}SELECT COUNT(*) AS cnt FROM ({core}) AS subquery"
        assert "WITH cte AS" in wrapped
        assert "SELECT COUNT(*)" in wrapped
        assert "FROM (SELECT x FROM cte)" in wrapped

    def test_wrap_non_cte_with_whitespace(self):
        query = "\n  SELECT * FROM t  \n"
        cte, core = split_cte_prefix(query)
        wrapped = f"{cte}SELECT COUNT(*) AS cnt FROM ({core}) AS subquery"
        assert wrapped == "SELECT COUNT(*) AS cnt FROM (SELECT * FROM t) AS subquery"


# ---------------------------------------------------------------------------
# Edge cases and combined scenarios
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_inject_top_non_select_query_raises(self):
        with pytest.raises(ValueError, match="non-SELECT"):
            inject_top_clause("VALUES (1, 2, 3)", 10)

    def test_inject_top_preserves_complex_query(self):
        sql = "SELECT a.*, b.col FROM a JOIN b ON a.id = b.id WHERE a.x > 5"
        result = inject_top_clause(sql, 100)
        assert result.startswith("SELECT TOP 100 ")
        assert "a.*, b.col" in result

    def test_cte_with_escaped_quotes(self):
        sql = "WITH cte AS (SELECT 'it''s' AS x) SELECT x FROM cte"
        cte, core = split_cte_prefix(sql)
        assert core == "SELECT x FROM cte"

    def test_cte_with_string_containing_select(self):
        sql = "WITH cte AS (SELECT 'SELECT' AS x) SELECT x FROM cte"
        cte, core = split_cte_prefix(sql)
        assert core == "SELECT x FROM cte"

    def test_inject_top_with_string_containing_top(self):
        sql = "SELECT col FROM t WHERE name = 'TOP SECRET'"
        result = inject_top_clause(sql, 10)
        # "TOP " exists in the query, so it should be returned as-is
        assert result == sql
