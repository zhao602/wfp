// =============================================================================
// 连接测试面板（canvas/dataSourceConfig/ConnectionTestPanel.tsx）
//
// 职责：触发连接测试、展示结果（可达/延迟/诊断），三态互斥（INV-3）。
// 设计意图：
//   1. INV-2：仅传 connectorId 不传 query，结果仅写本地 state 不调 onChange；
//   2. INV-3：按钮三态互斥（可点击/置灰/loading）；
//   3. DC-9：深色主题双轨取色。
// =============================================================================

import { useState, useRef } from "react";
import { orchestrationApiClient } from "../../api/orchestrationApiClient";
import { ApiError } from "../../api/apiError";
import type { ConnectionTestResult, DataSourceConnectionConfigPayload } from "../../api/apiTypes";
import type { DataSourceNodeConfiguration, DataSourceType } from "../../workflow-definition/definitionTypes";
import { isLocalDataSourceType } from "./dataSourceTypeMeta";
import { darkTokens } from "../../theme/themeMode";

export type DiagnosisCategory = "param" | "connection" | "unavailable" | "none";

export function classifyDiagnosis(diagnosis: string | undefined | null): DiagnosisCategory {
  if (!diagnosis) return "none";
  if (diagnosis.startsWith("参数错误：")) return "param";
  if (diagnosis.startsWith("连接失败：")) return "connection";
  if (diagnosis.startsWith("无法探测：")) return "unavailable";
  return "none";
}

interface Props {
  connectorId: string;
  /** 数据源类型（路由分流判定，INV-2） */
  sourceType?: DataSourceType;
  /** 完整连接配置（本地数据源测试请求体来源） */
  connectionConfig?: DataSourceNodeConfiguration;
  canTest: boolean;
  isDark: boolean;
  onTestResult?: (reachable: boolean) => void;
}

/** 从节点配置构造本地数据源测试请求体（剔除 node_type/query 等非连接字段） */
function toConnectionPayload(
  config: DataSourceNodeConfiguration,
): DataSourceConnectionConfigPayload {
  return {
    source_type: config.source_type as DataSourceType,
    connection_reference: config.connection_reference,
    file_path: config.file_path,
    encoding: config.encoding,
    url: config.url,
    method: config.method,
    headers: config.headers,
    connection_string: config.connection_string,
    hologres_endpoint: config.hologres_endpoint,
    hologres_port: config.hologres_port,
    hologres_database: config.hologres_database,
    hologres_access_key: config.hologres_access_key,
    kafka_brokers: config.kafka_brokers,
    kafka_topic: config.kafka_topic,
    kafka_group_id: config.kafka_group_id,
    kafka_offset: config.kafka_offset,
    flink_jobmanager_url: config.flink_jobmanager_url,
    flink_jar_id: config.flink_jar_id,
    flink_program_args: config.flink_program_args,
    spark_master_url: config.spark_master_url,
    spark_submit_args: config.spark_submit_args,
    spark_app_name: config.spark_app_name,
  };
}

export function ConnectionTestPanel({
  connectorId,
  sourceType,
  connectionConfig,
  canTest,
  onTestResult,
}: Props) {
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<ConnectionTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleTest = async () => {
    if (isLoading || !canTest) return;
    setIsLoading(true);
    setError(null);
    setResult(null);
    const controller = new AbortController();
    abortRef.current = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    timeoutRef.current = timeout;
    try {
      // 路由分流（INV-2）：本地 8 类走 /data-source/test，企业 3 类走既有企业连接器端点
      const res = isLocalDataSourceType(sourceType)
        ? await orchestrationApiClient.testDataSourceConnection(
            toConnectionPayload(connectionConfig as DataSourceNodeConfiguration),
            controller.signal,
          )
        : await orchestrationApiClient.testEnterpriseConnection(connectorId, controller.signal);
      setResult(res);
      onTestResult?.(res.reachable);
    } catch (e) {
      if (e instanceof ApiError && e.httpStatus === 404) {
        // detail=Not Found 表示后端路由未注册（功能未启用），区别于"连接器未注册"
        const routeMissing = e.message === "Not Found" || e.message.startsWith("HTTP 404");
        if (isLocalDataSourceType(sourceType)) {
          setError(
            routeMissing
              ? "测试连接接口不可用，请确认后端已重启并加载 /data-source/test 路由"
              : e.message,
          );
        } else {
          setError(
            routeMissing
              ? "企业连接器功能未启用，请联系管理员"
              : e.message || "连接器不存在，请先创建数据源连接",
          );
        }
      } else if (e instanceof Error && e.name === "AbortError") {
        setError("连接测试超时，请检查网络或目标端点是否可达");
      } else if (e instanceof ApiError) {
        setError(`网络错误，请检查网络连接（${e.message}）`);
      } else {
        setError("网络错误，请检查网络连接");
      }
      onTestResult?.(false);
    } finally {
      clearTimeout(timeout);
      setIsLoading(false);
      abortRef.current = null;
      timeoutRef.current = null;
    }
  };

  const disabled = !canTest || isLoading;

  return (
    <div className="wfp-ds-test">
      <style>{styles}</style>
      <button
        type="button"
        className="wfp-ds-test__btn"
        disabled={disabled}
        onClick={handleTest}
        title={!canTest ? "请先填写必填字段" : ""}
      >
        {isLoading ? "测试中…" : "测试连接"}
      </button>
      {result && (
        <div className="wfp-ds-test__result">
          <span className={result.reachable ? "wfp-ds-test__ok" : "wfp-ds-test__fail"}>
            {result.reachable ? "✓ 可达" : "✗ 不可达"}
          </span>
          <span className="wfp-ds-test__latency">{result.response_latency_ms}ms</span>
          {result.diagnosis && (
            <span className={`wfp-ds-test__diagnosis wfp-ds-test__diagnosis--${classifyDiagnosis(result.diagnosis)}`}>
              {result.diagnosis}
            </span>
          )}
        </div>
      )}
      {error && (
        <div className="wfp-ds-test__error">{error}</div>
      )}
    </div>
  );
}

const styles = `
.wfp-ds-test {
  margin-top: 8px;
}
.wfp-ds-test__btn {
  padding: 4px 12px;
  font-size: 12px;
  border: 1px solid #1976d2;
  border-radius: 3px;
  background: #1976d2;
  color: #fff;
  cursor: pointer;
}
.wfp-ds-test__btn:disabled {
  background: #ccc;
  border-color: #ccc;
  cursor: not-allowed;
}
.wfp-ds-test__result {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
}
.wfp-ds-test__ok {
  color: #4caf50;
}
.wfp-ds-test__fail {
  color: #e53935;
}
.wfp-ds-test__latency {
  color: #666;
}
.wfp-ds-test__diagnosis {
  color: #888;
  font-size: 11px;
  word-break: break-all;
}
.wfp-ds-test__diagnosis--param {
  color: #f57c00;
}
.wfp-ds-test__diagnosis--connection {
  color: #e53935;
}
.wfp-ds-test__diagnosis--unavailable {
  color: #757575;
}
.wfp-ds-test__diagnosis--none {
  color: #888;
}
.wfp-ds-test__error {
  margin-top: 4px;
  font-size: 11px;
  color: #e53935;
}
.wfp-theme--dark .wfp-ds-test__btn {
  background: #1565c0;
  border-color: #1565c0;
  color: ${darkTokens.text.primary};
}
.wfp-theme--dark .wfp-ds-test__btn:disabled {
  background: ${darkTokens.surface.border};
  border-color: ${darkTokens.surface.border};
  color: ${darkTokens.text.hint};
}
.wfp-theme--dark .wfp-ds-test__ok {
  color: #81c784;
}
.wfp-theme--dark .wfp-ds-test__fail {
  color: ${darkTokens.danger};
}
.wfp-theme--dark .wfp-ds-test__latency {
  color: ${darkTokens.text.secondary};
}
.wfp-theme--dark .wfp-ds-test__diagnosis {
  color: ${darkTokens.text.hint};
}
.wfp-theme--dark .wfp-ds-test__diagnosis--param {
  color: #ffb74d;
}
.wfp-theme--dark .wfp-ds-test__diagnosis--connection {
  color: ${darkTokens.danger};
}
.wfp-theme--dark .wfp-ds-test__diagnosis--unavailable {
  color: ${darkTokens.text.secondary};
}
.wfp-theme--dark .wfp-ds-test__diagnosis--none {
  color: ${darkTokens.text.hint};
}
.wfp-theme--dark .wfp-ds-test__error {
  color: ${darkTokens.danger};
}
`;