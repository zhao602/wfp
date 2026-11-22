# =============================================================================
# 自然语言辅助编排模块（governance/nl_orchestration/）
#
# 职责：将用户自然语言指令解析为结构化工作流定义，经拓扑校验后交付可视化画布。
# 设计意图（对应偏差修正 spec 5.1 / design 模块A）：
#   - NLIntentParser：调用 ILlmClient 解析意图，返回结构化节点+边列表
#   - YamlGenerator：将结构化意图映射为 WorkflowDefinition 并序列化为 YAML
#   - WorkflowModifier：在既有定义基础上应用节点插入/删除/边修改
#   - InjectionGuard：检测 YAML 注入与提示注入，阻止恶意输入
#   - NLOrchestrationService：编排上述组件，对外暴露 create_from_nl / modify_from_nl
# =============================================================================
