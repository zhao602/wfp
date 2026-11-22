// =============================================================================
// Schema 推断面板（canvas/dataSourceConfig/SchemaInferencePanel.tsx）
//
// 职责：连接测试成功后推断数据集 Schema 并以表格展示。
// 设计意图：
//   1. INV-9：仅本地 state，不接收/调用 onChange；
//   2. DC-7：依赖后端 POST /connectors/enterprise/{id}/schema 端点；
//   3. DC-9：深色主题双轨取色。
// =============================================================================

import { useState, useRef } from "react";
import { orchestrationApiClient } from "../../api/orchestrationApiClient";
import { ApiError } from "../../api/apiError";
import type { DataSchema } from "../../api/apiTypes";
import { darkTokens } from "../../theme/themeMode";

interface Props {
  connectorId: string;
  testReachable: boolean;
  isDark: boolean;
}

export function SchemaInferencePanel({ connectorId, testReachable }: Props) {
  const [isLoading, setIsLoading] = useState(false);
  const [schema, setSchema] = useState<DataSchema | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleInfer = async () => {
    if (isLoading || !testReachable) return;
    setIsLoading(true);
    setError(null);
    setSchema(null);
    const controller = new AbortController();
    abortRef.current = controller;
    const timeout = setTimeout(() => controller.abort(), 15000);
    timeoutRef.current = timeout;
    try {
      const res = await orchestrationApiClient.inferEnterpriseConnectorSchema(connectorId, controller.signal);
      setSchema(res);
    } catch (e) {
      if (e instanceof ApiError && e.httpStatus === 404) {
        setError("Schema 推断端点暂未开放");
      } else if (e instanceof Error && e.name === "AbortError") {
        setError("Schema 推断超时，可重试");
      } else if (e instanceof ApiError) {
        setError(`Schema 推断失败：${e.message}`);
      } else {
        setError("Schema 推断失败，请重试");
      }
    } finally {
      clearTimeout(timeout);
      setIsLoading(false);
      abortRef.current = null;
      timeoutRef.current = null;
    }
  };

  return (
    <div className="wfp-ds-schema">
      <style>{styles}</style>
      <button
        type="button"
        className="wfp-ds-schema__btn"
        disabled={!testReachable || isLoading}
        onClick={handleInfer}
      >
        {isLoading ? "推断中…" : "推断 Schema"}
      </button>
      {schema && (
        <div className="wfp-ds-schema__table-wrap">
          {schema.fields.length === 0 ? (
            <span className="wfp-ds-schema__empty">未推断到字段，请检查连接配置或查询语句</span>
          ) : (
            <table className="wfp-ds-schema__table">
              <thead>
                <tr>
                  <th>字段名</th>
                  <th>类型</th>
                  <th>可空</th>
                </tr>
              </thead>
              <tbody>
                {schema.fields.map((f) => (
                  <tr key={f.name}>
                    <td>{f.name}</td>
                    <td>{f.type}</td>
                    <td>{f.nullable ? "是" : "否"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {error && (
        <div className="wfp-ds-schema__error">{error}</div>
      )}
    </div>
  );
}

const styles = `
.wfp-ds-schema {
  margin-top: 8px;
}
.wfp-ds-schema__btn {
  padding: 4px 12px;
  font-size: 12px;
  border: 1px solid #7b1fa2;
  border-radius: 3px;
  background: #7b1fa2;
  color: #fff;
  cursor: pointer;
}
.wfp-ds-schema__btn:disabled {
  background: #ccc;
  border-color: #ccc;
  cursor: not-allowed;
}
.wfp-ds-schema__table-wrap {
  margin-top: 4px;
  overflow-x: auto;
}
.wfp-ds-schema__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}
.wfp-ds-schema__table th,
.wfp-ds-schema__table td {
  border: 1px solid #ddd;
  padding: 2px 6px;
  text-align: left;
}
.wfp-ds-schema__table th {
  background: #f5f5f5;
  font-weight: 600;
}
.wfp-ds-schema__empty {
  font-size: 11px;
  color: #888;
}
.wfp-ds-schema__error {
  margin-top: 4px;
  font-size: 11px;
  color: #e53935;
}
.wfp-theme--dark .wfp-ds-schema__btn {
  background: #6a1b9a;
  border-color: #6a1b9a;
  color: ${darkTokens.text.primary};
}
.wfp-theme--dark .wfp-ds-schema__btn:disabled {
  background: ${darkTokens.surface.border};
  border-color: ${darkTokens.surface.border};
  color: ${darkTokens.text.hint};
}
.wfp-theme--dark .wfp-ds-schema__table th,
.wfp-theme--dark .wfp-ds-schema__table td {
  border-color: ${darkTokens.surface.divider};
}
.wfp-theme--dark .wfp-ds-schema__table th {
  background: ${darkTokens.surface.bgHover};
  color: ${darkTokens.text.primary};
}
.wfp-theme--dark .wfp-ds-schema__table td {
  color: ${darkTokens.text.secondary};
}
.wfp-theme--dark .wfp-ds-schema__empty {
  color: ${darkTokens.text.hint};
}
.wfp-theme--dark .wfp-ds-schema__error {
  color: ${darkTokens.danger};
}
`;