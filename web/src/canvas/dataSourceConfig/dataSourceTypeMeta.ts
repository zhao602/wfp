// =============================================================================
// 数据源类型元数据表（canvas/dataSourceConfig/dataSourceTypeMeta.ts）
//
// 职责：以元数据表驱动数据源类型 → 连接参数字段的映射（DC-8）。
// 设计意图：
//   1. INV-1 单一出处：类型-字段一致性由本表唯一决定；
//   2. 与后端 enums.py:295-321 DataSourceType 顺序一致；
//   3. 与后端 models.py:47-80 DataSourceConnectionConfig 字段对齐。
// =============================================================================

import type { DataSourceNodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 数据源类型（re-export，避免循环依赖） */
export type { DataSourceType } from "../../workflow-definition/definitionTypes";
import type { DataSourceType } from "../../workflow-definition/definitionTypes";

/** 字段规格 */
export interface FieldSpec {
  /** 字段名（对应 DataSourceNodeConfiguration 的键） */
  name: keyof Pick<
    DataSourceNodeConfiguration,
    | "file_path"
    | "encoding"
    | "url"
    | "method"
    | "headers"
    | "connection_string"
    | "hologres_endpoint"
    | "hologres_port"
    | "hologres_database"
    | "hologres_access_key"
    | "kafka_brokers"
    | "kafka_topic"
    | "kafka_group_id"
    | "kafka_offset"
    | "flink_jobmanager_url"
    | "flink_jar_id"
    | "flink_program_args"
    | "spark_master_url"
    | "spark_submit_args"
    | "spark_app_name"
    | "target_endpoint"
  >;
  /** 中文标签 */
  label: string;
  /** 是否必填 */
  required: boolean;
  /** 是否掩码显示（凭据字段） */
  mask: boolean;
  /** 默认值 */
  defaultValue?: string;
}

/** 类型元数据 */
export interface TypeMeta {
  /** 中文友好名称 */
  friendlyName: string;
  /** 字段规格列表 */
  fields: FieldSpec[];
}

/** 数据源类型有序数组（与后端 enums.py:311-321 一致） */
export const DATA_SOURCE_TYPES: readonly DataSourceType[] = [
  "csv",
  "json",
  "rest_api",
  "database",
  "hologres",
  "kafka",
  "flink",
  "spark",
  "crm",
  "erp",
  "oa",
] as const;

/** 本地数据源类型集合（测试连接走 POST /data-source/test，INV-2） */
export const LOCAL_SOURCE_TYPES: ReadonlySet<DataSourceType> = new Set<DataSourceType>([
  "csv",
  "json",
  "rest_api",
  "database",
  "hologres",
  "kafka",
  "flink",
  "spark",
] as const);

/** 测试连接路由分流判定（INV-2 单一出处）：本地 8 类走新端点，企业 3 类走既有端点 */
export function isLocalDataSourceType(sourceType: DataSourceType | undefined): boolean {
  return sourceType !== undefined && LOCAL_SOURCE_TYPES.has(sourceType);
}

/** 数据源类型 → 元数据表（INV-1 单一出处） */
export const DATA_SOURCE_TYPE_META: Readonly<Record<DataSourceType, TypeMeta>> = {
  csv: {
    friendlyName: "CSV 文件",
    fields: [
      { name: "file_path", label: "文件路径", required: true, mask: false }, // models.py:53
      { name: "encoding", label: "文件编码", required: false, mask: false, defaultValue: "utf-8" }, // models.py:62
    ],
  },
  json: {
    friendlyName: "JSON 文件",
    fields: [
      { name: "file_path", label: "文件路径", required: true, mask: false }, // models.py:53
      { name: "encoding", label: "文件编码", required: false, mask: false, defaultValue: "utf-8" }, // models.py:62
    ],
  },
  rest_api: {
    friendlyName: "REST API",
    fields: [
      { name: "url", label: "端点 URL", required: true, mask: false }, // models.py:55
      { name: "method", label: "HTTP 方法", required: false, mask: false, defaultValue: "GET" }, // models.py:56
      { name: "headers", label: "请求头", required: false, mask: false }, // models.py:57
    ],
  },
  database: {
    friendlyName: "数据库",
    fields: [
      { name: "connection_string", label: "连接串", required: true, mask: false }, // models.py:59
    ],
  },
  hologres: {
    friendlyName: "Hologres 实时数仓",
    fields: [
      { name: "hologres_endpoint", label: "端点地址", required: true, mask: false }, // models.py:64
      { name: "hologres_port", label: "端口", required: false, mask: false, defaultValue: "80" }, // models.py:65
      { name: "hologres_database", label: "数据库名", required: true, mask: false }, // models.py:66
      { name: "hologres_access_key", label: "AccessKey", required: true, mask: true }, // models.py:67
    ],
  },
  kafka: {
    friendlyName: "Kafka 消息流",
    fields: [
      { name: "kafka_brokers", label: "Broker 列表", required: true, mask: false }, // models.py:69
      { name: "kafka_topic", label: "Topic 名称", required: true, mask: false }, // models.py:70
      { name: "kafka_group_id", label: "消费组 ID", required: false, mask: false }, // models.py:71
      { name: "kafka_offset", label: "消费起始位置", required: false, mask: false, defaultValue: "earliest" }, // models.py:72
    ],
  },
  flink: {
    friendlyName: "Flink 流计算",
    fields: [
      { name: "flink_jobmanager_url", label: "JobManager URL", required: true, mask: false }, // models.py:74
      { name: "flink_jar_id", label: "JAR ID", required: false, mask: false }, // models.py:75
      { name: "flink_program_args", label: "作业参数", required: false, mask: false }, // models.py:76
    ],
  },
  spark: {
    friendlyName: "Spark 批计算",
    fields: [
      { name: "spark_master_url", label: "Master URL", required: true, mask: false }, // models.py:78
      { name: "spark_submit_args", label: "submit 参数", required: false, mask: false }, // models.py:79
      { name: "spark_app_name", label: "应用名", required: false, mask: false }, // models.py:80
    ],
  },
  crm: {
    friendlyName: "CRM 系统",
    fields: [
      { name: "target_endpoint", label: "目标端点", required: true, mask: false },
    ],
  },
  erp: {
    friendlyName: "ERP 系统",
    fields: [
      { name: "target_endpoint", label: "目标端点", required: true, mask: false },
    ],
  },
  oa: {
    friendlyName: "OA 系统",
    fields: [
      { name: "target_endpoint", label: "目标端点", required: true, mask: false },
    ],
  },
};