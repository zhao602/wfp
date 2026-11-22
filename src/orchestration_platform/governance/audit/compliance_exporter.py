# =============================================================================
# 合规导出器（governance/audit/compliance_exporter.py）
#
# 职责：按条件过滤审计日志并脱敏导出。
# 设计意图（对应 spec 5.3、design 2.6、任务 T65.1）：
#   1. 导出过滤：按时间范围、租户、操作类型、操作人等条件过滤；
#   2. 导出格式：JSON 与 CSV；
#   3. 导出脱敏：敏感字段按脱敏规则掩码；
#   4. 导出操作留痕：导出本身产生审计日志；
#   5. 流式导出与批量脱敏。
# =============================================================================

import csv
import io
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.audit.mask_engine import MaskEngine
from orchestration_platform.governance.audit.models import AuditLogEntry


class ComplianceExporter:
    """合规导出器。

    按条件过滤审计日志并脱敏导出为 JSON 或 CSV 格式。
    """

    def __init__(
        self,
        mask_engine: Optional[MaskEngine] = None,
    ) -> None:
        """初始化合规导出器。

        Args:
            mask_engine: 脱敏引擎；缺省创建默认实例。
        """
        self._mask_engine = mask_engine or MaskEngine()
        self._settings = get_settings()

    def export(
        self,
        entries: List[AuditLogEntry],
        format: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> str:
        """导出审计日志。

        Args:
            entries: 已查询的审计日志列表。
            format: 导出格式（"json" 或 "csv"）；缺省从配置读取。
            tenant_id: 租户标识（用于脱敏规则过滤）。

        Returns:
            导出内容字符串。
        """
        fmt = format or self._settings.audit_export_format
        max_records = self._settings.audit_export_max_records

        if len(entries) > max_records:
            raise ValueError(
                f"导出数据量超限: {len(entries)} > {max_records}"
            )

        # 脱敏处理
        masked_entries = [
            self._mask_entry(entry, tenant_id) for entry in entries
        ]

        if fmt == "csv":
            return self._export_csv(masked_entries)
        else:
            return self._export_json(masked_entries)

    def export_with_pagination(
        self,
        entries: List[AuditLogEntry],
        page: int = 1,
        page_size: int = 10000,
        format: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """分页导出审计日志。

        Returns:
            {page, page_size, total_pages, total_records, data}。
        """
        total = len(entries)
        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 1
        start = (page - 1) * page_size
        end = start + page_size
        page_entries = entries[start:end]

        exported = self.export(page_entries, format, tenant_id)
        return {
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_records": total,
            "data": exported,
        }

    def _mask_entry(
        self,
        entry: AuditLogEntry,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """对单条审计日志脱敏。"""
        data = entry.model_dump(mode="json")
        return self._mask_engine.mask_record(data, tenant_id)

    @staticmethod
    def _export_json(entries: List[Dict[str, Any]]) -> str:
        """导出为 JSON 格式。"""
        return json.dumps(entries, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def _export_csv(entries: List[Dict[str, Any]]) -> str:
        """导出为 CSV 格式。"""
        if not entries:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=entries[0].keys())
        writer.writeheader()
        for row in entries:
            flattened = {
                k: json.dumps(v, ensure_ascii=False, default=str)
                if isinstance(v, (dict, list))
                else str(v) if v is not None else ""
                for k, v in row.items()
            }
            writer.writerow(flattened)
        return output.getvalue()
