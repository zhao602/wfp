# =============================================================================
# 节点市场第三方贡献（mcp_tool_market/contribution/）
#
# 职责：第三方开发者贡献节点包的提交、审核、安全扫描与发布。
# 设计意图（对应偏差修正 spec 5.3 / design 2.1.3.1）：
#   - 贡献状态机七态：DRAFT→SUBMITTED→SECURITY_SCAN→REVIEWING→APPROVED→PUBLISHED/REJECTED
#   - 签名校验作为 SUBMITTED→SECURITY_SCAN 迁移守卫
#   - 静态安全扫描基于 ast 模块
#   - 发布复用既有 McpToolMarket.register / VersionManager / PermissionChecker
# =============================================================================
