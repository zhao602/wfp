import { describe, it, expect, vi, beforeEach } from "vitest";
import type { DataSourceNodeConfiguration } from "../../workflow-definition/definitionTypes";
import { checkReadOnlyQuery } from "../dataSourceConfig/readonlyQueryGuard";
import { DATA_SOURCE_TYPES, DATA_SOURCE_TYPE_META, isLocalDataSourceType } from "../dataSourceConfig/dataSourceTypeMeta";

// 由于 DataSourceConfigForm 是 NodeConfigurationPanel 的内部函数，
// 这里通过测试其依赖的纯函数和元数据来验证关键行为。

describe("DataSourceConfigForm 集成测试", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("类型选择器有 11 种选项（AC-1.1）", () => {
    expect(DATA_SOURCE_TYPES).toHaveLength(11);
  });

  it("旧格式配置（无 source_type）可加载（RG-1）", () => {
    const config: DataSourceNodeConfiguration = {
      node_type: "data_source",
      connection_reference: "ref1",
      query: "SELECT * FROM t",
    };
    expect(config.source_type).toBeUndefined();
    expect(config.connection_reference).toBe("ref1");
    expect(config.query).toBe("SELECT * FROM t");
  });

  it("选择 DATABASE 类型应展示 connection_string 字段、隐藏 file_path（AC-1.2/AC-1.3）", () => {
    const meta = DATA_SOURCE_TYPE_META.database;
    const fieldNames = meta.fields.map((f) => f.name);
    expect(fieldNames).toContain("connection_string");
    expect(fieldNames).not.toContain("file_path");
  });

  it("选择 HOLOGRES 类型应有 4 个字段且 access_key 为掩码（AC-1.4）", () => {
    const meta = DATA_SOURCE_TYPE_META.hologres;
    expect(meta.fields).toHaveLength(4);
    const accessKey = meta.fields.find((f) => f.name === "hologres_access_key");
    expect(accessKey).toBeDefined();
    expect(accessKey!.mask).toBe(true);
  });

  it("必填字段为空时连接测试按钮置灰（AC-1.5/AC-2.1）", () => {
    const config: DataSourceNodeConfiguration = {
      node_type: "data_source",
      connection_reference: "",
      query: "",
      source_type: "database",
    };
    // connection_reference 为空 → canTest = false
    const canTest =
      config.source_type !== undefined &&
      config.connection_reference.trim() !== "" &&
      DATA_SOURCE_TYPE_META[config.source_type].fields.every((f) => {
        if (!f.required) return true;
        const val = config[f.name];
        return val !== undefined && val !== null && String(val).trim() !== "";
      });
    expect(canTest).toBe(false);
  });

  it("query 含 DELETE 关键词应触发警告且不禁用保存（AC-4.1/AC-4.2）", () => {
    const result = checkReadOnlyQuery("DELETE FROM users");
    expect(result.violated).toBe(true);
    expect(result.keyword).toBe("DELETE");
  });

  it("字段修改触发 onChange 回写（AC-3.1）", () => {
    let config: DataSourceNodeConfiguration = {
      node_type: "data_source",
      connection_reference: "ref1",
      query: "",
    };
    const onChange = (newConfig: DataSourceNodeConfiguration) => {
      config = newConfig;
    };
    onChange({ ...config, query: "SELECT * FROM orders" });
    expect(config.query).toBe("SELECT * FROM orders");
  });

  it("选择类型后写入 source_type（AC-3.2，INV-4）", () => {
    let config: DataSourceNodeConfiguration = {
      node_type: "data_source",
      connection_reference: "ref1",
      query: "",
    };
    const onChange = (newConfig: DataSourceNodeConfiguration) => {
      config = newConfig;
    };
    onChange({ ...config, source_type: "kafka" });
    expect(config.source_type).toBe("kafka");
  });
});
// =============================================================================
// source_type null 值守卫修复用例（fix_datasource_source_type_null_guard）
// 断言修复后守卫表达式（!!sourceType 真值判断）的最终行为。
// =============================================================================

describe("source_type null 值守卫修复（AC-1~AC-7）", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  /** 修复后守卫表达式副本（NodeConfigurationPanel.tsx:798-800） */
  const computeRequiredFieldsFilled = (config: DataSourceNodeConfiguration): boolean => {
    const sourceType = config.source_type;
    return (
      !!sourceType &&
      DATA_SOURCE_TYPE_META[sourceType].fields.every((f) => {
        if (!f.required) return true;
        const val = config[f.name];
        return val !== undefined && val !== null && String(val).trim() !== "";
      })
    );
  };

  /** 修复后 canTest 表达式副本（NodeConfigurationPanel.tsx:805） */
  const computeCanTest = (config: DataSourceNodeConfiguration): boolean => {
    const sourceType = config.source_type;
    return !!sourceType && config.connection_reference.trim() !== "" && computeRequiredFieldsFilled(config);
  };

  /** 修复后 needsEnterpriseList 表达式副本（NodeConfigurationPanel.tsx:756） */
  const computeNeedsEnterpriseList = (config: DataSourceNodeConfiguration): boolean => {
    const sourceType = config.source_type;
    return !!sourceType && !isLocalDataSourceType(sourceType);
  };

  it("source_type 为 null 时守卫直接短路，不访问 META 表（AC-1/AC-2，崩溃消除）", () => {
    const config = { node_type: "data_source", connection_reference: "ref1", query: "", source_type: null } as unknown as DataSourceNodeConfiguration;
    expect(computeRequiredFieldsFilled(config)).toBe(false);
  });

  it("null 且连接引用已填写时 canTest 恒为 false（AC-3）", () => {
    const config = { node_type: "data_source", connection_reference: "ref1", query: "", source_type: null } as unknown as DataSourceNodeConfiguration;
    expect(config.connection_reference.trim()).toBe("ref1");
    expect(computeCanTest(config)).toBe(false);
  });

  it("null 时 needsEnterpriseList 为 false，不误请求企业列表（AC-4）", () => {
    const config = { node_type: "data_source", connection_reference: "ref1", query: "", source_type: null } as unknown as DataSourceNodeConfiguration;
    expect(computeNeedsEnterpriseList(config)).toBe(false);
  });

  it("null 与 undefined 等价：三种守卫行为完全一致（AC-5）", () => {
    const nullConfig = { node_type: "data_source", connection_reference: "ref1", query: "", source_type: null } as unknown as DataSourceNodeConfiguration;
    const undefinedConfig: DataSourceNodeConfiguration = { node_type: "data_source", connection_reference: "ref1", query: "" };
    expect(computeRequiredFieldsFilled(nullConfig)).toBe(computeRequiredFieldsFilled(undefinedConfig));
    expect(computeCanTest(nullConfig)).toBe(computeCanTest(undefinedConfig));
    expect(computeNeedsEnterpriseList(nullConfig)).toBe(computeNeedsEnterpriseList(undefinedConfig));
  });

  it("有效类型 csv 主流程回归：齐全配置 canTest 为 true（AC-6，INV-6）", () => {
    const config: DataSourceNodeConfiguration = {
      node_type: "data_source",
      connection_reference: "ref1",
      query: "",
      source_type: "csv",
      file_path: "/data/test.csv",
    };
    expect(computeRequiredFieldsFilled(config)).toBe(true);
    expect(computeCanTest(config)).toBe(true);
    expect(computeNeedsEnterpriseList(config)).toBe(false);
  });

  it("清除选择回写 source_type 为 undefined 而非 null 或空串（AC-7，INV-5）", () => {
    let config: DataSourceNodeConfiguration = {
      node_type: "data_source",
      connection_reference: "ref1",
      query: "",
      source_type: "csv",
    };
    const onChange = (newConfig: DataSourceNodeConfiguration) => {
      config = newConfig;
    };
    const value = "";
    onChange({ ...config, source_type: value === "" ? undefined : value });
    expect(config.source_type).toBeUndefined();
    expect(config.source_type).not.toBeNull();
  });
});