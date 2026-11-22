# =============================================================================
# 五要素结构校验器（domain/validation/definition_structure_validator.py）
#
# 职责：校验工作流定义的「五要素」是否齐备且类型正确，并控制定义体积上限。
# 设计意图（对应需求规格 5.1.1.1、5.1.1.7、5.1.3.4）：
#   1. 五要素：规范版本号 / 节点列表 / 连线列表 / 输入参数定义 / 输出定义；
#   2. 校验结果以 ValidationIssue 列表一次性返回，不因首错终止；
#   3. 节点数量超过上限时返回 WF_DEFINITION_TOO_LARGE 并附实际节点数。
# =============================================================================

from typing import Any, Dict, List

from orchestration_platform.domain.errors import WF_DEFINITION_TOO_LARGE, WF_VALIDATION_FAILED
from orchestration_platform.domain.models.common import ValidationIssue
from orchestration_platform.domain.models.enums import Severity, NodeType

# 五要素 + 定义元信息的必备顶层字段
REQUIRED_TOP_LEVEL_FIELDS = (
    "spec_version",
    "definition_id",
    "name",
    "nodes",
    "edges",
    "inputs",
    "outputs",
)

# 节点顶层必备字段
REQUIRED_NODE_FIELDS = ("node_id", "node_type")


class DefinitionStructureValidator:
    """工作流定义结构校验器（五要素存在性 + 体积上限）。"""

    def validate(self, document: Dict[str, Any], max_nodes: int) -> List[ValidationIssue]:
        """校验文档结构，返回问题清单（可能为空表示通过）。

        参数：
            document  —— 已通过 YAML 语法解析的原始文档树
            max_nodes —— 节点数量上限
        """
        issues: List[ValidationIssue] = []

        # ---------------- 必备顶层字段存在性 ----------------
        for field_name in REQUIRED_TOP_LEVEL_FIELDS:
            if field_name not in document:
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"缺少必填要素：{field_name}",
                    )
                )

        # 若关键要素缺失，后续类型校验无从进行，直接返回已收集问题
        if any(issue.message.startswith("缺少必填要素") for issue in issues):
            return issues

        # ---------------- 版本号与名称类型 ----------------
        if not str(document.get("spec_version", "")).strip():
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message="规范版本号 spec_version 不能为空",
                )
            )

        name = document.get("name", "")
        if not isinstance(name, str) or not name.strip():
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message="定义名称 name 不能为空",
                )
            )
        elif len(name) > 100:
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message=f"定义名称长度超过 100 字符（当前 {len(name)}）",
                )
            )

        # ---------------- 列表要素类型与体积 ----------------
        issues.extend(self._validate_node_list(document.get("nodes"), max_nodes))
        issues.extend(self._validate_list_field(document.get("edges"), "edges", "连线列表"))
        issues.extend(self._validate_list_field(document.get("inputs"), "inputs", "输入参数定义"))
        issues.extend(self._validate_list_field(document.get("outputs"), "outputs", "输出定义"))

        return issues

    # ------------------------------------------------------------------
    # 内部校验方法
    # ------------------------------------------------------------------
    def _validate_node_list(self, nodes: Any, max_nodes: int) -> List[ValidationIssue]:
        """校验节点列表：类型、非空、体积上限、节点基本字段。"""
        issues: List[ValidationIssue] = []

        if not isinstance(nodes, list):
            return [
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message="节点列表 nodes 必须为列表",
                )
            ]

        if len(nodes) == 0:
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message="节点列表 nodes 不能为空（至少需包含一个触发器节点）",
                )
            )

        # 体积上限：超过第一阶段上限时拒绝并给出实际节点数
        if len(nodes) > max_nodes:
            issues.append(
                ValidationIssue(
                    code=WF_DEFINITION_TOO_LARGE,
                    severity=Severity.ERROR,
                    message=f"定义体积超限：节点数 {len(nodes)} 超过上限 {max_nodes}",
                )
            )

        # 节点基本字段校验（node_id / node_type）
        valid_node_types = {item.value for item in NodeType}
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"第 {index + 1} 个节点必须为映射结构",
                    )
                )
                continue
            for required in REQUIRED_NODE_FIELDS:
                if required not in node or node.get(required) in (None, ""):
                    issues.append(
                        ValidationIssue(
                            code=WF_VALIDATION_FAILED,
                            severity=Severity.ERROR,
                            message=f"第 {index + 1} 个节点缺少必填字段：{required}",
                            node_id=node.get("node_id"),
                        )
                    )
            node_type = node.get("node_type")
            if node_type is not None and node_type not in valid_node_types:
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"节点 {node.get('node_id')} 的节点类型非法：{node_type}",
                        node_id=node.get("node_id"),
                    )
                )

        return issues

    def _validate_list_field(self, value: Any, field_name: str, label: str) -> List[ValidationIssue]:
        """校验列表类要素的类型（可为空列表）。"""
        if not isinstance(value, list):
            return [
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message=f"{label} {field_name} 必须为列表",
                )
            ]
        return []
