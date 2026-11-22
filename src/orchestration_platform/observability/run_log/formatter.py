# =============================================================================
# 运行日志格式化（observability/run_log/formatter.py，workflow_run_logging CHG-07）
#
# 职责：JSONL 行格式化（JsonLinesFormatter）。
# 设计意图（design 2.2 接口组 H、INV-03）：
#   1. 载荷已在门面（recorder）完成组装/脱敏/截断并序列化为 JSON 行，
#      格式化层不做任何字段裁剪、不引入原始载荷入口；
#   2. 输出 = record.getMessage() + "\n"（单行 + 换行）；
#      配套 TimeSizeRotatingFileHandler 的 terminator="" 防双换行。
# =============================================================================

import logging


class JsonLinesFormatter(logging.Formatter):
    """JSONL 格式化器：消息即完整 JSON 行。"""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage() + "\n"
