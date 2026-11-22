# RBAC 与多租户使用说明

## 权限项注册表

权限项 = 资源类型 × 操作类型 × 资源范围。平台启动时自动注册默认权限项。

### 资源类型

- `workflow_definition`：工作流定义
- `execution_instance`：执行实例
- `mcp_tool`：MCP 工具
- `data_source_connector`：数据源连接器
- `tenant_config`：租户配置
- `audit_log`：审计日志
- `governance_policy`：治理策略

### 操作类型

- `create` / `read` / `update` / `delete` / `execute` / `export`

### 资源范围

- `tenant`：租户级
- `org`：组织级
- `self`：个人级

## 角色与权限配置

角色是权限项的命名集合，支持权限继承（parent_role_id）。角色绑定将角色关联到用户与组织节点。

## 租户创建与组织树管理

通过 `/api/v1/governance/tenants` 创建租户，通过 `/api/v1/governance/orgs` 管理组织树。组织树支持多层级，自动维护节点路径。

## 降级模式

未启用治理模块时（`RBAC_ENABLED=false` 且 `TENANT_ISOLATION_ENABLED=false` 等），系统以"单租户默认全权限"模式运行，行为与第二阶段一致。
