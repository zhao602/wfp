import { useEffect, useState } from "react";
import { darkTokens, useThemeMode } from "../../../theme/themeMode";
import type {
  DataSourceNodeConfiguration,
  DataSourceType,
} from "../../../workflow-definition/definitionTypes";
import {
  DATA_SOURCE_TYPES,
  DATA_SOURCE_TYPE_META,
  isLocalDataSourceType,
} from "../../dataSourceConfig/dataSourceTypeMeta";
import { checkReadOnlyQuery } from "../../dataSourceConfig/readonlyQueryGuard";
import { DataSourceConnectionFields } from "../../dataSourceConfig/DataSourceConnectionFields";
import { ConnectionTestPanel } from "../../dataSourceConfig/ConnectionTestPanel";
import { SchemaInferencePanel } from "../../dataSourceConfig/SchemaInferencePanel";
import { orchestrationApiClient, getTenantId } from "../../../api/orchestrationApiClient";
import { ApiError } from "../../../api/apiError";
import type { ConnectorResponse } from "../../../api/apiTypes";
import { PanelIcon } from "../shared/PanelIcon";
import { Field } from "../shared/Field";

export function DataSourceConfigForm({
  config,
  onChange,
}: {
  config: DataSourceNodeConfiguration;
  onChange: (config: DataSourceNodeConfiguration) => void;
}) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [connectors, setConnectors] = useState<ConnectorResponse[]>([]);
  const [connectorListError, setConnectorListError] = useState(false);
  const [connectorListDisabled, setConnectorListDisabled] = useState(false);
  const [manualMode, setManualMode] = useState(false);
  const [testReachable, setTestReachable] = useState(false);

  const sourceType = config.source_type;
  // 企业连接器列表仅对企业类型（crm/erp/oa）有意义；本地数据源不依赖它（避免无意义 404）
  const needsEnterpriseList = !!sourceType && !isLocalDataSourceType(sourceType);

  useEffect(() => {
    // 未选择企业类型时不请求企业连接器列表：本地数据源的 connection_reference 手动填写即可
    if (!needsEnterpriseList) {
      setConnectors([]);
      setConnectorListError(false);
      setConnectorListDisabled(false);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    orchestrationApiClient
      .listEnterpriseConnectors(getTenantId())
      .then((list) => {
        if (!cancelled) {
          setConnectors(list);
          setConnectorListDisabled(false);
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        // 404 = 企业连接器功能未启用（后端路由未注册），非加载失败，单独提示
        if (e instanceof ApiError && e.httpStatus === 404) {
          setConnectorListDisabled(true);
        } else {
          setConnectorListError(true);
        }
      })
      .finally(() => {
        if (!cancelled) clearTimeout(timeout);
      });
    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timeout);
    };
  }, [needsEnterpriseList]);

  const queryCheck = checkReadOnlyQuery(config.query);

  const requiredFieldsFilled =
    !!sourceType &&
    DATA_SOURCE_TYPE_META[sourceType].fields.every((f) => {
      if (!f.required) return true;
      const val = config[f.name];
      return val !== undefined && val !== null && String(val).trim() !== "";
    });
  const canTest = !!sourceType && config.connection_reference.trim() !== "" && requiredFieldsFilled;

  return (
    <div style={{ fontSize: "12px" }}>
      {connectorListError && (
        <div style={{ color: isDark ? darkTokens.danger : "#e53935", marginBottom: "4px", fontSize: "11px" }}>
          连接器列表加载失败，已切换手动输入模式
        </div>
      )}
      {connectorListDisabled && (
        <div style={{ color: isDark ? darkTokens.text.hint : "#888", marginBottom: "4px", fontSize: "11px" }}>
          企业连接器功能未启用，已切换手动输入模式
        </div>
      )}
      <Field nodeType="data_source" field="source_type" label="数据源类型">
        <select
          className="wfp-ncp-select"
          value={sourceType ?? ""}
          onChange={(e) => {
            const value = e.target.value as DataSourceType | "";
            onChange({ ...config, source_type: value === "" ? undefined : value });
            setTestReachable(false);
          }}
        >
          <option value="">请选择数据源类型</option>
          {DATA_SOURCE_TYPES.map((t) => (
            <option key={t} value={t}>
              {DATA_SOURCE_TYPE_META[t].friendlyName}
            </option>
          ))}
        </select>
      </Field>
      <Field nodeType="data_source" field="connection_reference" label="连接引用">
        {connectors.length > 0 && !manualMode ? (
          <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
            <select
              className="wfp-ncp-select"
              value={config.connection_reference}
              onChange={(e) => {
                const selected = connectors.find((c) => c.connector_id === e.target.value);
                if (selected) {
                  onChange({
                    ...config,
                    connection_reference: selected.connector_id,
                    source_type: selected.connector_type as DataSourceType,
                  });
                } else {
                  onChange({ ...config, connection_reference: e.target.value });
                }
                setTestReachable(false);
              }}
            >
              <option value="">请选择连接器</option>
              {connectors.map((c) => (
                <option key={c.connector_id} value={c.connector_id}>
                  {c.connector_id}（{c.connector_type}）
                </option>
              ))}
            </select>
            <button
              type="button"
              className="wfp-ncp-btn wfp-ncp-btn--ghost wfp-ncp-btn--icon"
              onClick={() => setManualMode(true)}
              title="改为手动输入连接标识"
              aria-label="手动输入连接标识"
            >
              <PanelIcon name="pencil" size={13} />
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
            <input
              type="text"
              className="wfp-ncp-input"
              value={config.connection_reference}
              onChange={(e) => {
                onChange({ ...config, connection_reference: e.target.value });
                setTestReachable(false);
              }}
            />
            {connectors.length > 0 && (
              <button
                type="button"
                className="wfp-ncp-btn wfp-ncp-btn--ghost wfp-ncp-btn--icon"
                onClick={() => setManualMode(false)}
                title="从连接器列表中选择"
                aria-label="从连接器列表中选择"
              >
                <PanelIcon name="list" size={13} />
              </button>
            )}
          </div>
        )}
      </Field>
      {sourceType && (
        <DataSourceConnectionFields
          sourceType={sourceType}
          config={config}
          onChange={onChange}
          isDark={isDark}
        />
      )}
      <ConnectionTestPanel
        connectorId={config.connection_reference}
        sourceType={sourceType}
        connectionConfig={config}
        canTest={canTest}
        isDark={isDark}
        onTestResult={setTestReachable}
      />
      {testReachable && (
        <SchemaInferencePanel
          connectorId={config.connection_reference}
          testReachable={testReachable}
          isDark={isDark}
        />
      )}
      <Field nodeType="data_source" field="query" label="只读查询">
        <textarea
          className="wfp-ncp-input"
          value={config.query}
          onChange={(e) => onChange({ ...config, query: e.target.value })}
          rows={3}
          style={{ width: "100%" }}
        />
      </Field>
      {queryCheck.violated && (
        <div style={{ color: isDark ? darkTokens.danger : "#e53935", fontSize: "11px", marginTop: "2px" }}>
          检测到禁止的写操作关键词：{queryCheck.keyword}，此查询将被后端拒绝
        </div>
      )}
    </div>
  );
}
