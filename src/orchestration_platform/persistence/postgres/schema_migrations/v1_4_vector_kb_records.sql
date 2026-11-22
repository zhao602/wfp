-- =============================================================================
-- 向量知识库 PG 数组存储迁移脚本（v1_4_vector_kb_records.sql）
--
-- 职责：为「本地向量数据库 pg_array 可选存储路径」建立表结构
-- （arch_review_gap_b2 B2-C5 / DC-B2-05 ①；tasks T5.3）。
-- 设计意图：
--   1. 迁移幂等（沿用 v1_2/v1_3 范式，E10）：版本登记 ON CONFLICT DO NOTHING，
--      表/索引 IF NOT EXISTS，重复执行零副作用；
--   2. vector_kb_records：payload JSONB（text+source 载荷）+ embedding
--      DOUBLE PRECISION[]（PostgreSQL 原生数组）+ embedding_kind 代际
--      （real|hash；NULL=存量缺省 hash 代际读取时识别）；
--   3. collection_meta：一集合一行（维度/度量/版本/计数），对齐
--      SqliteVectorStore collection_meta 语义（首写固化维度 AC-20）；
--   4. 距离权威路径 = 应用层 vector_math.metric_score（sqlite/pg_array 同源
--      零漂移，DC-B2-05 ②）；pgvector `<=>` 算子仅为可选增强路径——本迁移
--      不创建/不依赖 vector 扩展（探测由 PgVectorExtensionProbe 运行期执行）。
-- =============================================================================

-- 迁移版本记录
INSERT INTO schema_versions (version, description) VALUES ('1.4', '向量知识库 PG 数组存储（pg_array 可选路径）')
ON CONFLICT (version) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 1. 集合元信息表（一集合一行；首写固化维度，AC-20）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collection_meta (
    collection_id      TEXT PRIMARY KEY,
    declared_dimension INTEGER NOT NULL CHECK (declared_dimension >= 1),
    metric             TEXT    NOT NULL,
    format_version     TEXT    NOT NULL,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL,
    record_count       BIGINT  NOT NULL DEFAULT 0
);

-- -----------------------------------------------------------------------------
-- 2. 向量记录表（幂等 upsert 键 = (collection_id, record_id)，AC-04/AC-05）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vector_kb_records (
    collection_id  TEXT                   NOT NULL,
    record_id      TEXT                   NOT NULL,
    payload        JSONB                  NOT NULL,
    embedding      DOUBLE PRECISION[]     NOT NULL,
    embedding_kind TEXT                   NULL,
    PRIMARY KEY (collection_id, record_id)
);

CREATE INDEX IF NOT EXISTS idx_vector_kb_records_kind
    ON vector_kb_records(collection_id, embedding_kind);
