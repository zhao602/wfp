import { describe, it, expect } from "vitest";
import { DATA_SOURCE_TYPES, DATA_SOURCE_TYPE_META } from "../dataSourceTypeMeta";

describe("dataSourceTypeMeta", () => {
  it("DATA_SOURCE_TYPES 长度为 11 且顺序与后端 enums.py 一致", () => {
    expect(DATA_SOURCE_TYPES).toHaveLength(11);
    expect(DATA_SOURCE_TYPES).toEqual([
      "csv", "json", "rest_api", "database", "hologres",
      "kafka", "flink", "spark", "crm", "erp", "oa",
    ]);
  });

  it("每类型都有友好名称和字段列表", () => {
    for (const t of DATA_SOURCE_TYPES) {
      const meta = DATA_SOURCE_TYPE_META[t];
      expect(meta.friendlyName).toBeTruthy();
      expect(meta.fields.length).toBeGreaterThan(0);
    }
  });

  it("CSV 字段为 file_path(必填) + encoding", () => {
    const fields = DATA_SOURCE_TYPE_META.csv.fields;
    expect(fields).toHaveLength(2);
    expect(fields[0].name).toBe("file_path");
    expect(fields[0].required).toBe(true);
    expect(fields[1].name).toBe("encoding");
    expect(fields[1].required).toBe(false);
  });

  it("HOLOGRES 含 4 字段且 hologres_access_key.mask=true（AC-1.4）", () => {
    const fields = DATA_SOURCE_TYPE_META.hologres.fields;
    expect(fields).toHaveLength(4);
    const accessKey = fields.find((f) => f.name === "hologres_access_key");
    expect(accessKey).toBeDefined();
    expect(accessKey!.mask).toBe(true);
    expect(accessKey!.required).toBe(true);
  });

  it("DATABASE 字段为 connection_string(必填)", () => {
    const fields = DATA_SOURCE_TYPE_META.database.fields;
    expect(fields).toHaveLength(1);
    expect(fields[0].name).toBe("connection_string");
    expect(fields[0].required).toBe(true);
  });

  it("KAFKA 必填字段为 kafka_brokers + kafka_topic", () => {
    const fields = DATA_SOURCE_TYPE_META.kafka.fields;
    const required = fields.filter((f) => f.required).map((f) => f.name);
    expect(required).toEqual(["kafka_brokers", "kafka_topic"]);
  });

  it("REST_API 必填字段为 url", () => {
    const fields = DATA_SOURCE_TYPE_META.rest_api.fields;
    const required = fields.filter((f) => f.required).map((f) => f.name);
    expect(required).toEqual(["url"]);
  });

  it("CRM/ERP/OA 字段为 target_endpoint(必填)", () => {
    for (const t of ["crm", "erp", "oa"] as const) {
      const fields = DATA_SOURCE_TYPE_META[t].fields;
      expect(fields).toHaveLength(1);
      expect(fields[0].name).toBe("target_endpoint");
      expect(fields[0].required).toBe(true);
    }
  });

  it("各类型 fields 集合互斥（INV-1 单一出处）", () => {
    // 跨类型共用字段白名单（后端 models.py DataSourceConnectionConfig 单一配置类）：
    //   - file_path/encoding：csv/json 文件类共用（models.py:53「CSV / JSON」、62「通用」）
    //   - target_endpoint：CRM/ERP/OA 企业系统共用
    const crossTypeShared = new Set(["file_path", "encoding", "target_endpoint"]);
    const allFieldNames = new Set<string>();
    for (const t of DATA_SOURCE_TYPES) {
      for (const f of DATA_SOURCE_TYPE_META[t].fields) {
        if (!crossTypeShared.has(f.name)) {
          expect(allFieldNames.has(f.name)).toBe(false);
          allFieldNames.add(f.name);
        }
      }
    }
  });
});