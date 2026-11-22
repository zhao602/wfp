# =============================================================================
# 输出格式投影层（engine/nodes/data_transform_ops/output_format.py）
#
# 职责：链尾数据集 → 声明输出形态的值投影（expand_data_transform_ops
#       design DC-04 / CHG-B10 / T6.1，AC-17/18）。
# 四形态（§2.5 输出格式与下游影响矩阵）：
#   None / dataset      → 原样返回 List[Dict]（默认零破坏，
#                         与 assemble_single_reference(expected_kind="dataset")
#                         完全兼容，INV-09 逐字等价）；
#   single_object       → dict（取 record_index 行，缺省 0；越界/负数/空数据集 → None）；
#   text                → str（text_field 逐行取值 + text_separator 缺省 "\n" 拼接；
#                         text_field 未声明 → 全文 JSON 序列化；None 值行取空串）；
#   kv                  → dict（每行 kv_key_field → kv_value_field；
#                         字段缺省取记录前两列（首见列序）；键重复后者覆盖；
#                         不可哈希键经 JSON 序列化规范化）。
# 设计口径（DC-04）：
#   - 纯计算、**不抛异常**；空数据集确定结果（single_object → None；
#     text → ""；kv → {}）；
#   - 非 dataset 形态与下游 expected_kind="dataset" 装配 →
#     NODE_INPUT_TYPE_MISMATCH（用户显式选择的确定行为，须 UI 提示）；
#   - 命名输出键仍为节点 output_name（INV-10，仅值形态改变）。
# =============================================================================

import json
from typing import Any, Dict, List, Optional

from orchestration_platform.domain.models.enums import DataTransformOutputFormat
from orchestration_platform.domain.models.node_configurations import (
    DataTransformOutputFormatParams,
)


def _dataset_columns(dataset: List[Dict[str, Any]]) -> List[str]:
    """汇总数据集列名（首见顺序，跨行并集；kv 缺省字段推导用）。"""
    columns: List[str] = []
    seen = set()
    for row in dataset:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns


def _hashable_key(key: Any) -> Any:
    """不可哈希键（dict/list）经 JSON 序列化规范化（纯计算不抛异常）。"""
    try:
        hash(key)
        return key
    except TypeError:
        return json.dumps(key, ensure_ascii=False, sort_keys=True)


class OutputFormatProjector:
    """输出格式投影层（DC-04）：链尾数据集 → 声明形态值。

    纯计算、不抛异常；参数越界按定义兜底（record_index 越界 → None 等）。
    """

    @staticmethod
    def project(
        dataset: List[Dict[str, Any]],
        output_format: Optional[DataTransformOutputFormat],
        params: Optional[DataTransformOutputFormatParams],
    ) -> Any:
        """按声明形态投影链尾数据集（缺省/None ≡ dataset 原样返回）。"""
        if output_format is None or output_format == DataTransformOutputFormat.DATASET:
            return dataset
        if output_format == DataTransformOutputFormat.SINGLE_OBJECT:
            return OutputFormatProjector._project_single_object(dataset, params)
        if output_format == DataTransformOutputFormat.TEXT:
            return OutputFormatProjector._project_text(dataset, params)
        # DataTransformOutputFormat.KV
        return OutputFormatProjector._project_kv(dataset, params)

    @staticmethod
    def _project_single_object(
        dataset: List[Dict[str, Any]],
        params: Optional[DataTransformOutputFormatParams],
    ) -> Optional[Dict[str, Any]]:
        """单条记录：取 record_index 行（缺省 0）；越界/负数/空数据集 → None。"""
        if not dataset:
            return None
        record_index = params.record_index if params is not None else None
        index = record_index if record_index is not None else 0
        if not isinstance(index, int) or index < 0 or index >= len(dataset):
            return None
        return dataset[index]

    @staticmethod
    def _project_text(
        dataset: List[Dict[str, Any]],
        params: Optional[DataTransformOutputFormatParams],
    ) -> str:
        """文本：text_field 逐行取值 + text_separator（缺省 "\\n"）拼接。

        text_field 未声明 → 全文 JSON 序列化；None 值行取空串；
        非字符串值以 str() 规范化；空数据集 → ""。
        """
        if not dataset:
            return ""
        text_field = params.text_field if params is not None else None
        if not text_field:
            return json.dumps(dataset, ensure_ascii=False)
        separator = params.text_separator if params is not None else None
        if separator is None:
            separator = "\n"
        parts: List[str] = []
        for row in dataset:
            value = row.get(text_field)
            parts.append("" if value is None else str(value))
        return separator.join(parts)

    @staticmethod
    def _project_kv(
        dataset: List[Dict[str, Any]],
        params: Optional[DataTransformOutputFormatParams],
    ) -> Dict[Any, Any]:
        """键值对：每行 kv_key_field → kv_value_field 组字典。

        字段缺省取记录前两列（首见列序，不足两列时值为 None）；
        键重复后者覆盖；不可哈希键经 JSON 序列化规范化；空数据集 → {}。
        """
        if not dataset:
            return {}
        columns = _dataset_columns(dataset)
        key_field = params.kv_key_field if params is not None else None
        value_field = params.kv_value_field if params is not None else None
        if not key_field:
            key_field = columns[0] if columns else None
        if not value_field:
            value_field = columns[1] if len(columns) > 1 else None
        if key_field is None:
            return {}

        kv: Dict[Any, Any] = {}
        for row in dataset:
            key = _hashable_key(row.get(key_field))
            kv[key] = row.get(value_field) if value_field is not None else None
        return kv
