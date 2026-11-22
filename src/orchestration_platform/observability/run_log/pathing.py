# =============================================================================
# 运行日志路径解析（observability/run_log/pathing.py，workflow_run_logging CHG-06）
#
# 职责：运行日志的目录层级与文件命名（LogPathResolver）。
# 设计意图（design 2.0.2 命名与层级规范、INV-11）：
#   1. 目录层级 {log_dir}/{tenant}/{business_category}/（租户隔离）；
#   2. 文件名 {record_type}_{category}_{yyyyMMdd}[_{seq}].jsonl
#      （seq<=0 无序号段；序号 3 位零填充、_001 起）；
#   3. 消毒规则 [^a-z0-9_-]→_、转小写、去首尾 _、空值→default，
#      杜绝路径穿越；
#   4. 活动文件辨识：指定日期下无序号文件优先（当天活动文件不带序号），
#      无序号文件缺失时回退最大序号文件（轮转切换窗口/中断恢复场景）。
# =============================================================================

import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional


class LogPathResolver:
    """运行日志路径解析器（纯计算，不执行任何 I/O）。"""

    _DEFAULT_SEGMENT = "default"

    def __init__(self, log_dir: str) -> None:
        self._log_dir = Path(log_dir)

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    def sanitize_segment(self, value: Optional[str]) -> str:
        """消毒路径段：转小写、非法字符→_、去首尾 _、空值兜底 default。"""
        if not value:
            return self._DEFAULT_SEGMENT
        lowered = value.strip().lower()
        sanitized = re.sub(r"[^a-z0-9_-]", "_", lowered)
        sanitized = sanitized.strip("_")
        return sanitized or self._DEFAULT_SEGMENT

    def resolve(
        self,
        record_type: str,
        tenant_id: Optional[str],
        business_category: Optional[str],
        when: datetime,
        seq: int = 0,
    ) -> Path:
        """解析记录文件路径（不执行任何 I/O）。"""
        filename = self._file_prefix(record_type, business_category, when)
        if seq > 0:
            filename += f"_{seq:03d}"
        filename += ".jsonl"
        return self._directory_for(tenant_id, business_category) / filename

    def active_path(
        self,
        record_type: str,
        tenant_id: Optional[str],
        business_category: Optional[str],
        when: datetime,
    ) -> Optional[Path]:
        """辨识指定日期的当前写入文件：无序号文件优先（design 2.0.2：当天活动文件不带序号）；
        无序号文件缺失时回退最大序号文件（轮转切换窗口/中断恢复场景）；不存在返回 None。"""
        directory = self._directory_for(tenant_id, business_category)
        if not directory.is_dir():
            return None
        prefix = self._file_prefix(record_type, business_category, when)
        no_seq_path = directory / f"{prefix}.jsonl"
        if no_seq_path.exists():
            return no_seq_path
        best_seq = -1
        best_path: Optional[Path] = None
        pattern = re.compile(re.escape(prefix) + r"_(\d+)\.jsonl$")
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            match = pattern.match(entry.name)
            if match is None:
                continue
            seq_index = int(match.group(1))
            if seq_index > best_seq:
                best_seq = seq_index
                best_path = entry
        return best_path

    def scan_seq_indices(
        self,
        record_type: str,
        tenant_id: Optional[str],
        business_category: Optional[str],
        when: datetime,
    ) -> List[int]:
        """扫描指定日期已存在的序号列表（升序）；目录不存在返回空列表。"""
        directory = self._directory_for(tenant_id, business_category)
        if not directory.is_dir():
            return []
        prefix = self._file_prefix(record_type, business_category, when)
        pattern = re.compile(re.escape(prefix) + r"_(\d+)\.jsonl$")
        indices: List[int] = []
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            match = pattern.match(entry.name)
            if match is not None:
                indices.append(int(match.group(1)))
        indices.sort()
        return indices

    def _directory_for(self, tenant_id: Optional[str], business_category: Optional[str]) -> Path:
        return (
            self._log_dir
            / self.sanitize_segment(tenant_id)
            / self.sanitize_segment(business_category)
        )

    def _file_prefix(
        self, record_type: str, business_category: Optional[str], when: datetime
    ) -> str:
        # record_type 同样过消毒，防路径穿越（正常常量值不受影响）
        record_type_segment = self.sanitize_segment(record_type)
        category = self.sanitize_segment(business_category)
        return f"{record_type_segment}_{category}_{when.strftime('%Y%m%d')}"
