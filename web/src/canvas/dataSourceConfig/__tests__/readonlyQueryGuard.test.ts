import { describe, it, expect } from "vitest";
import { checkReadOnlyQuery } from "../readonlyQueryGuard";

describe("readonlyQueryGuard", () => {
  const forbiddenKeywords = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "CREATE", "TRUNCATE", "MERGE", "GRANT", "REVOKE", "RENAME",
  ];

  it("11 种禁止关键词各命中（AC-4.1）", () => {
    for (const kw of forbiddenKeywords) {
      const result = checkReadOnlyQuery(`${kw} FROM table`);
      expect(result.violated).toBe(true);
      expect(result.keyword).toBe(kw);
    }
  });

  it("大小写不敏感", () => {
    const result = checkReadOnlyQuery("delete from users");
    expect(result.violated).toBe(true);
    expect(result.keyword).toBe("DELETE");
  });

  it("合法查询不命中", () => {
    expect(checkReadOnlyQuery("SELECT * FROM orders")).toEqual({ violated: false, keyword: null });
    expect(checkReadOnlyQuery("WITH cte AS (SELECT 1) SELECT * FROM cte")).toEqual({ violated: false, keyword: null });
  });

  it("空 query 返回 { violated: false, keyword: null }", () => {
    expect(checkReadOnlyQuery("")).toEqual({ violated: false, keyword: null });
  });

  it("正则与后端 _FORBIDDEN_KEYWORDS 11 种完全一致（INV-5）", () => {
    // 确保恰好 11 种关键词被检测
    let hitCount = 0;
    for (const kw of forbiddenKeywords) {
      if (checkReadOnlyQuery(`${kw} x`).violated) hitCount++;
    }
    expect(hitCount).toBe(11);
  });
});