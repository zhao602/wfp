# =============================================================================
# ABAC 权限模型扩展模块（governance/rbac/abac/）
#
# 职责：在既有 RBAC 基础上扩展 ABAC 策略引擎，实现组合决策。
# 设计意图（对应偏差修正 spec 5.2 / design 模块B）：
#   - AbacPolicyEngine：ABAC 策略决策引擎
#   - AttributeEvaluator：动态属性求值器
#   - CombinedDecisionEngine：RBAC先+ABAC后组合决策
#   - AbacInterceptor：ABAC 拦截器，插入拦截器链
# =============================================================================
