-- =============================================================================
-- 检查点同轮唯一约束迁移脚本（v1_3_checkpoint_unique.sql）
--
-- 职责：为检查点表建立 (instance_id, round_index) 同轮唯一约束。
-- 设计意图（对应 arch_review_v2 任务 3.1、CHG-15/09/10、DC-09）：
--   1. 迁移幂等（沿用 v1_2 范式，E10）：版本登记 ON CONFLICT DO NOTHING，
--      重复执行零副作用；
--   2. 存量同轮重复行不物理删除（检查点追加写不可变原则）——
--      保留 checkpoint_id 字典序最小一条可用，其余置 is_available=FALSE；
--   3. 唯一索引建立后，重试/恢复场景的同轮重复写由仓储层
--      ON CONFLICT DO NOTHING 幂等吸收（postgres_checkpoint_repository.append），
--      不再产生重复行（DC-09）。
-- =============================================================================

-- 迁移版本记录
INSERT INTO schema_versions (version, description) VALUES ('1.3', '检查点同轮唯一约束')
ON CONFLICT (version) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 1. 存量同轮重复行软去重（不物理删除）
--    同一 (instance_id, round_index) 多行时：checkpoint_id 字典序最小者保留，
--    其余置 is_available=FALSE + 原因标注。
--    幂等性：重复执行时已置 FALSE 的行不再命中更新条件，零变更。
-- -----------------------------------------------------------------------------
UPDATE checkpoint cp
SET is_available = FALSE,
    unavailable_reason = 'v1_3 同轮去重：保留字典序最小检查点（arch_review_v2 3.1）'
WHERE cp.is_available = TRUE
  AND EXISTS (
      SELECT 1
      FROM checkpoint other
      WHERE other.instance_id = cp.instance_id
        AND other.round_index = cp.round_index
        AND other.checkpoint_id < cp.checkpoint_id
  );

-- -----------------------------------------------------------------------------
-- 2. 同轮唯一索引（幂等：IF NOT EXISTS）
-- -----------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_checkpoint_instance_round
    ON checkpoint (instance_id, round_index);
