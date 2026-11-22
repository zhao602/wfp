# =============================================================================
# 墓碑与被遗忘权模块（governance/audit/tombstone/）
#
# 职责：实现审计日志墓碑标记与 GDPR Article 17 被遗忘权。
# 设计意图（对应偏差修正 spec 5.3 / design 模块C）：
#   - TombstoneService：单条墓碑标记
#   - RightToBeForgottenService：被遗忘权批量标记
#   - TombstoneQueryService：墓碑查询（仅元数据）
# =============================================================================
