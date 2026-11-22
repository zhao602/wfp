# =============================================================================
# 数据库连接器分页查询拼接单元测试（tests/unit/test_database_connector_pagination.py）
#
# 覆盖 fix_datasource_pagination_semicolon 的 18 个边界场景：
#   T-1 ~ T-3：末尾分号去除（单分号/多分号/空白+分号+换行）
#   T-4 ~ T-7：已有 LIMIT/OFFSET 检测（大写/小写/混合/两者）
#   T-8：正常无分页子句（向后兼容）
#   T-9 ~ T-10：纯分号/纯空白+分号（空串返回）
#   T-11 ~ T-12：已有 LIMIT/OFFSET + 末尾分号（先去分号再检测）
#   T-13：混合大小写
#   T-14：词边界防误匹配
#   T-15：不同 limit/offset 值
#   T-16 ~ T-17：复杂查询/CTE + 末尾分号
#   T-18：子查询中含 LIMIT（已知限制）
#
# 沙箱限制：pytest 无法安装，本文件作为交付物编写，待 CI 执行。
# =============================================================================

import pytest

from orchestration_platform.integrations.data_source_connectors.database_connector import (
    DatabaseDataSourceConnector,
)


@pytest.fixture
def connector():
    return DatabaseDataSourceConnector()


class TestBuildPaginatedQuerySemicolonStripping:
    """T-1 ~ T-3：末尾分号去除"""

    def test_t01_single_semicolon(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user";', 100, 0)
        assert result == 'SELECT id FROM "user" LIMIT 100 OFFSET 0'

    def test_t02_multiple_semicolons(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user";;;', 100, 0)
        assert result == 'SELECT id FROM "user" LIMIT 100 OFFSET 0'

    def test_t03_whitespace_semicolon_newline(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user"  ;  \n', 100, 0)
        assert result == 'SELECT id FROM "user" LIMIT 100 OFFSET 0'


class TestBuildPaginatedQueryExistingLimitOffset:
    """T-4 ~ T-7：已有 LIMIT/OFFSET 检测"""

    def test_t04_existing_limit_uppercase(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user" LIMIT 10', 100, 0)
        assert result == 'SELECT id FROM "user" LIMIT 10'

    def test_t05_existing_offset_uppercase(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user" OFFSET 5', 100, 0)
        assert result == 'SELECT id FROM "user" OFFSET 5'

    def test_t06_existing_limit_and_offset(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user" LIMIT 10 OFFSET 5', 100, 0)
        assert result == 'SELECT id FROM "user" LIMIT 10 OFFSET 5'

    def test_t07_existing_limit_offset_lowercase(self, connector):
        result = connector._build_paginated_query('select id from "user" limit 10 offset 5', 100, 0)
        assert result == 'select id from "user" limit 10 offset 5'


class TestBuildPaginatedQueryNormalAppend:
    """T-8、T-15：正常追加"""

    def test_t08_normal_no_pagination_clause(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user"', 100, 0)
        assert result == 'SELECT id FROM "user" LIMIT 100 OFFSET 0'

    def test_t15_different_limit_offset_values(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user"', 50, 200)
        assert result == 'SELECT id FROM "user" LIMIT 50 OFFSET 200'


class TestBuildPaginatedQueryEmptyAfterStrip:
    """T-9 ~ T-10：去除后为空串"""

    def test_t09_pure_semicolons(self, connector):
        result = connector._build_paginated_query(";;;", 100, 0)
        assert result == ""

    def test_t10_whitespace_and_semicolons(self, connector):
        result = connector._build_paginated_query("  ;  \n", 100, 0)
        assert result == ""


class TestBuildPaginatedQuerySemicolonThenDetect:
    """T-11 ~ T-12：先去分号再检测已有 LIMIT/OFFSET"""

    def test_t11_existing_limit_with_trailing_semicolon(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user" LIMIT 10;', 100, 0)
        assert result == 'SELECT id FROM "user" LIMIT 10'

    def test_t12_existing_offset_with_trailing_semicolons(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user" OFFSET 5;;;', 100, 0)
        assert result == 'SELECT id FROM "user" OFFSET 5'


class TestBuildPaginatedQueryCaseInsensitive:
    """T-13：混合大小写"""

    def test_t13_mixed_case_limit(self, connector):
        result = connector._build_paginated_query('SELECT id FROM "user" Limit 10', 100, 0)
        assert result == 'SELECT id FROM "user" Limit 10'


class TestBuildPaginatedQueryWordBoundary:
    """T-14：词边界防误匹配"""

    def test_t14_column_name_contains_limit_substring(self, connector):
        result = connector._build_paginated_query('SELECT limited_count FROM "user"', 100, 0)
        assert result == 'SELECT limited_count FROM "user" LIMIT 100 OFFSET 0'


class TestBuildPaginatedQueryComplexQueries:
    """T-16 ~ T-17：复杂查询 + 末尾分号"""

    def test_t16_complex_query_with_semicolon(self, connector):
        query = 'SELECT id, nickname FROM "user" WHERE id > 100 ORDER BY id;'
        result = connector._build_paginated_query(query, 100, 0)
        assert result == 'SELECT id, nickname FROM "user" WHERE id > 100 ORDER BY id LIMIT 100 OFFSET 0'

    def test_t17_cte_query_with_semicolon(self, connector):
        query = 'WITH t AS (SELECT id FROM "user") SELECT * FROM t;'
        result = connector._build_paginated_query(query, 100, 0)
        assert result == 'WITH t AS (SELECT id FROM "user") SELECT * FROM t LIMIT 100 OFFSET 0'


class TestBuildPaginatedQuerySubqueryLimit:
    """T-18：子查询中含 LIMIT（已知限制，不追加）"""

    def test_t18_limit_in_subquery(self, connector):
        query = 'SELECT * FROM (SELECT id FROM "user" LIMIT 10) t'
        result = connector._build_paginated_query(query, 100, 0)
        assert result == 'SELECT * FROM (SELECT id FROM "user" LIMIT 10) t'