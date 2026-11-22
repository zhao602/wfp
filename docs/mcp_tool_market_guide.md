# MCP 工具市场使用说明

## 概述

MCP 工具市场是平台工具注册中心，维护已注册工具的元数据声明、版本管理与权限标注。

## 核心能力

### 工具注册

```python
from orchestration_platform.mcp_tool_market.tool_market import McpToolMarket
from orchestration_platform.mcp_tool_market.models import McpToolDescriptor
from orchestration_platform.mcp_tool_market.persistence.local_file_tool_market_repository import LocalFileToolMarketRepository

repo = LocalFileToolMarketRepository()
market = McpToolMarket(repo)

descriptor = McpToolDescriptor(
    tool_identifier="my_tool",
    name="自定义工具",
    description="示例工具",
    version="1.0.0",
    input_schema={"param": {"type": "string"}},
    output_schema={"result": {"type": "object"}},
    tags=["custom"],
)

result = await market.register(descriptor, registered_by="user_001")
```

### 版本管理

同一工具标识支持多版本共存。注册新版本时自动记录前一版本号。

### 权限校验

工具权限分三级：`read_only` / `read_write` / `admin`。调用方权限不足时返回 `MCP_TOOL_PERMISSION_DENIED`。

### 工具发现

支持按名称、类别（标签）、权限等条件查询工具清单。

## 平台内置工具

平台通过 MCP 服务端暴露九个能力工具：

| 工具标识 | 功能 | 权限 |
| --- | --- | --- |
| validate_workflow | 校验工作流定义 | read_only |
| save_workflow | 保存工作流定义 | read_write |
| get_workflow | 查询工作流定义 | read_only |
| start_workflow | 启动工作流实例 | read_write |
| get_instance | 查询实例状态 | read_only |
| get_instance_events | 查询实例事件 | read_only |
| get_checkpoints | 查询检查点列表 | read_only |
| rollback_instance | 回滚实例 | read_write |
| replay_instance | 重放实例 | read_write |

## 持久化

- 本地文件：`LocalFileToolMarketRepository`，按 `tool_id + version` 唯一存储
- PostgreSQL：`PostgresToolMarketRepository`，驱动延迟导入

通过配置项切换持久化实现。
