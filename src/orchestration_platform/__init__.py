# =============================================================================
# 后端主包（orchestration_platform）
#
# 分层架构：
#   api/            接入层：FastAPI 路由、鉴权中间件、SSE 事件推送
#   application/    应用服务层：用例编排与事务边界
#   domain/         领域层：领域模型、枚举、校验器、错误码
#   engine/         编排引擎层：图编译、调度、节点执行器（含 adapter 抽象隔离层）
#   workflow/       YAML 解析与序列化
#   persistence/    持久化仓储（本地文件 / PostgreSQL 双实现）
#   integrations/   外部集成（MCP、LLM、数据源、本地工具）
#   observability/  可观测性（结构化日志、脱敏、指标）
#   configuration/  配置项类型化定义
# =============================================================================

__version__ = "0.1.0"
