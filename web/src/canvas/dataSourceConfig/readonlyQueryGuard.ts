// =============================================================================
// 只读查询前端预校验（canvas/dataSourceConfig/readonlyQueryGuard.ts）
//
// 职责：在前端对查询语句进行预校验，检测写/删/结构变更关键词（DC-5）。
// 设计意图：
//   1. INV-5：关键词集合与后端 read_only_statement_guard.py:11-14 完全一致；
//   2. 纯函数，零副作用、零依赖、零异常抛出；
//   3. 仅作提示性第一道防线，后端守卫为最终安全防线。
// =============================================================================

/** 禁止关键词正则（与后端 _FORBIDDEN_KEYWORDS 完全一致，11 种，i flag） */
const FORBIDDEN_KEYWORDS = /\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|REVOKE|RENAME)\b/i;

/** 预校验结果 */
export interface ReadOnlyQueryResult {
  /** 是否命中禁止关键词 */
  violated: boolean;
  /** 命中的关键词（大写归一），未命中时为 null */
  keyword: string | null;
}

/**
 * 检查查询语句是否包含禁止的写/删/结构变更关键词。
 *
 * @param query SQL 或类 SQL 查询语句
 * @returns 预校验结果，命中时 violated=true 且 keyword 为首个命中关键词（大写）
 */
export function checkReadOnlyQuery(query: string): ReadOnlyQueryResult {
  if (!query) {
    return { violated: false, keyword: null };
  }
  const match = FORBIDDEN_KEYWORDS.exec(query);
  if (match) {
    return { violated: true, keyword: match[0].toUpperCase() };
  }
  return { violated: false, keyword: null };
}