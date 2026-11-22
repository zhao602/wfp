# =============================================================================
# MCP 工具声明（integrations/mcp_server/tool_declarations.py）
#
# 职责：声明九个平台能力工具，供外部 Agent 通过 MCP 协议调用。
# 设计意图（对应 spec 5.3.1.1、任务 T32.1）：
#   1. 每个工具含标识、名称、输入输出 schema、权限要求；
#   2. 工具覆盖工作流管理、实例执行、状态查询三类能力。
# =============================================================================

from typing import Any, Dict, List

from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.mcp_tool_market.models import McpToolDescriptor


def get_platform_tool_declarations() -> List[McpToolDescriptor]:
    """返回九个平台能力工具的声明。"""
    return [
        McpToolDescriptor(
            tool_identifier="validate_workflow",
            name="校验工作流定义",
            description="校验工作流 YAML 定义的结构正确性与语义约束",
            permission=ToolPermission.READONLY,
            input_schema={"definition": {"type": "object", "description": "工作流定义"}},
            output_schema={"valid": {"type": "boolean"}, "errors": {"type": "array"}},
            tags=["workflow", "validation"],
        ),
        McpToolDescriptor(
            tool_identifier="save_workflow",
            name="保存工作流定义",
            description="保存工作流定义至平台仓储",
            permission=ToolPermission.READWRITE,
            input_schema={"definition": {"type": "object"}, "overwrite": {"type": "boolean"}},
            output_schema={"definition_id": {"type": "string"}, "version": {"type": "integer"}},
            tags=["workflow", "management"],
        ),
        McpToolDescriptor(
            tool_identifier="get_workflow",
            name="查询工作流定义",
            description="按 ID 与版本查询工作流定义",
            permission=ToolPermission.READONLY,
            input_schema={"definition_id": {"type": "string"}, "version": {"type": "integer"}},
            output_schema={"definition": {"type": "object"}},
            tags=["workflow", "query"],
        ),
        McpToolDescriptor(
            tool_identifier="start_workflow",
            name="启动工作流实例",
            description="按定义 ID 与输入参数启动工作流实例",
            permission=ToolPermission.READWRITE,
            input_schema={"definition_id": {"type": "string"}, "inputs": {"type": "object"}},
            output_schema={"instance_id": {"type": "string"}, "status": {"type": "string"}},
            tags=["instance", "execution"],
        ),
        McpToolDescriptor(
            tool_identifier="get_instance",
            name="查询实例状态",
            description="按实例 ID 查询执行状态",
            permission=ToolPermission.READONLY,
            input_schema={"instance_id": {"type": "string"}},
            output_schema={"instance_id": {"type": "string"}, "status": {"type": "string"}},
            tags=["instance", "query"],
        ),
        McpToolDescriptor(
            tool_identifier="get_instance_events",
            name="查询实例事件",
            description="按实例 ID 查询事件序列",
            permission=ToolPermission.READONLY,
            input_schema={"instance_id": {"type": "string"}, "include_voided": {"type": "boolean"}},
            output_schema={"events": {"type": "array"}},
            tags=["instance", "query", "events"],
        ),
        McpToolDescriptor(
            tool_identifier="get_checkpoints",
            name="查询检查点列表",
            description="按实例 ID 查询检查点列表",
            permission=ToolPermission.READONLY,
            input_schema={"instance_id": {"type": "string"}},
            output_schema={"checkpoints": {"type": "array"}},
            tags=["instance", "query", "checkpoint"],
        ),
        McpToolDescriptor(
            tool_identifier="rollback_instance",
            name="回滚实例",
            description="将实例回滚到指定检查点",
            permission=ToolPermission.READWRITE,
            input_schema={"instance_id": {"type": "string"}, "checkpoint_id": {"type": "string"}},
            output_schema={"success": {"type": "boolean"}, "rolled_back_to": {"type": "string"}},
            tags=["instance", "rollback"],
        ),
        McpToolDescriptor(
            tool_identifier="replay_instance",
            name="重放实例",
            description="从指定检查点重放实例执行",
            permission=ToolPermission.READWRITE,
            input_schema={"instance_id": {"type": "string"}, "checkpoint_id": {"type": "string"}},
            output_schema={"success": {"type": "boolean"}, "new_instance_id": {"type": "string"}},
            tags=["instance", "replay"],
        ),
    ]


def get_tool_by_identifier(tool_identifier: str) -> McpToolDescriptor | None:
    """按标识查找工具声明。"""
    for tool in get_platform_tool_declarations():
        if tool.tool_identifier == tool_identifier:
            return tool
    return None
