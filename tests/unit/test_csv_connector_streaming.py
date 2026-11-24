# =============================================================================
# CSV 连接器流式读测试（tests/unit/test_csv_connector_streaming.py）
#
# 覆盖验收：roadmap_b12_data_chunking T4（B12-D1.3）
#   1. islice 流式分页语义（limit+1 探测 has_more）；
#   2. offset 跳过正确性；
#   3. total_count 置 None（不再全量计数）；
#   4. 空文件 / 越界 offset 语义；
#   5. utf-8 中文编码。
# =============================================================================

import csv

import pytest

from orchestration_platform.domain.models.enums import DataSourceType
from orchestration_platform.integrations.data_source_connectors.csv_connector import (
    CsvDataSourceConnector,
)
from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
    DataSourceReadRequest,
    PaginationParams,
)


ROWS = [{"id": i, "name": f"行{i}"} for i in range(10)]


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "name"])
        writer.writeheader()
        writer.writerows(rows)


def _make_request(path, offset=0, limit=100) -> DataSourceReadRequest:
    config = DataSourceConnectionConfig(
        source_type=DataSourceType.CSV,
        connection_reference="csv-1",
        file_path=path,
        encoding="utf-8",
    )
    return DataSourceReadRequest(
        connection_config=config,
        pagination=PaginationParams(offset=offset, limit=limit),
    )


@pytest.fixture
def csv_path(tmp_path):
    path = str(tmp_path / "data.csv")
    _write_csv(path, ROWS)
    return path


class TestStreamingPagination:
    async def test_first_page_has_more(self, csv_path):
        """首页不足全量 → has_more=True（limit+1 探测）。"""
        result = await CsvDataSourceConnector().read(_make_request(csv_path, limit=4))

        assert result.data == [{"id": "0", "name": "行0"},
                               {"id": "1", "name": "行1"},
                               {"id": "2", "name": "行2"},
                               {"id": "3", "name": "行3"}]
        assert result.has_more is True
        assert result.total_count is None  # 流式不再全量计数

    async def test_offset_skip(self, csv_path):
        """offset 跳过正确（第 3 行起取 2 行）。"""
        result = await CsvDataSourceConnector().read(
            _make_request(csv_path, offset=3, limit=2)
        )

        assert result.data == [{"id": "3", "name": "行3"},
                               {"id": "4", "name": "行4"}]
        assert result.has_more is True

    async def test_last_short_page_no_more(self, csv_path):
        """末页不足一页 → has_more=False。"""
        result = await CsvDataSourceConnector().read(
            _make_request(csv_path, offset=8, limit=4)
        )

        assert len(result.data) == 2
        assert result.has_more is False

    async def test_exact_full_page_boundary(self, csv_path):
        """恰满一页（10 行 limit=10）→ 探测第 11 行不存在 → has_more=False。"""
        result = await CsvDataSourceConnector().read(
            _make_request(csv_path, offset=0, limit=10)
        )

        assert len(result.data) == 10
        assert result.has_more is False

    async def test_offset_beyond_eof_returns_empty(self, csv_path):
        """offset 越界 → 空数据 + has_more=False（C5 语义保持）。"""
        result = await CsvDataSourceConnector().read(
            _make_request(csv_path, offset=100, limit=10)
        )

        assert result.data == []
        assert result.has_more is False


class TestEdgeCases:
    async def test_header_only_file(self, tmp_path):
        """空文件（仅表头）→ 空数据 + has_more=False。"""
        path = str(tmp_path / "empty.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=["id", "name"]).writeheader()

        result = await CsvDataSourceConnector().read(_make_request(path))

        assert result.data == []
        assert result.has_more is False
        assert result.total_count is None

    async def test_utf8_chinese_content(self, csv_path):
        """utf-8 中文内容经流式读往返无损。"""
        result = await CsvDataSourceConnector().read(_make_request(csv_path, limit=10))

        assert result.data[0]["name"] == "行0"
        assert result.data[9]["name"] == "行9"

    async def test_multi_page_walk_covers_all(self, csv_path):
        """多页遍历（executor 循环语义模拟）：4+4+2 覆盖全量 10 行无重漏。"""
        connector = CsvDataSourceConnector()
        collected = []
        offset = 0
        for _ in range(10):
            result = await connector.read(
                _make_request(csv_path, offset=offset, limit=4)
            )
            collected.extend(result.data)
            if not result.has_more:
                break
            offset += 4

        assert len(collected) == 10
        assert [r["id"] for r in collected] == [str(i) for i in range(10)]
