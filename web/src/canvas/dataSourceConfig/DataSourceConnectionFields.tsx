// =============================================================================
// 数据源动态连接参数表单（canvas/dataSourceConfig/DataSourceConnectionFields.tsx）
//
// 职责：按数据源类型元数据表驱动渲染类型专属连接参数字段（DC-8）。
// 设计意图：
//   1. INV-1：仅渲染 DATA_SOURCE_TYPE_META[sourceType].fields 定义的字段；
//   2. DC-10：mask=true 的字段使用 password input；
//   3. DC-9：深色主题双轨取色（darkTokens + .wfp-theme--dark 作用域）。
// =============================================================================

import { useState } from "react";
import type { DataSourceNodeConfiguration } from "../../workflow-definition/definitionTypes";
import type { DataSourceType } from "../../workflow-definition/definitionTypes";
import { DATA_SOURCE_TYPE_META, type FieldSpec } from "./dataSourceTypeMeta";
import { darkTokens } from "../../theme/themeMode";

interface Props {
  sourceType: DataSourceType;
  config: DataSourceNodeConfiguration;
  onChange: (config: DataSourceNodeConfiguration) => void;
  isDark: boolean;
}

export function DataSourceConnectionFields({ sourceType, config, onChange }: Props) {
  const meta = DATA_SOURCE_TYPE_META[sourceType];
  const [headersRaw, setHeadersRaw] = useState<string>(
    config.headers ? JSON.stringify(config.headers, null, 2) : "",
  );

  const getFieldValue = (field: FieldSpec): string => {
    const value = config[field.name];
    if (value === undefined || value === null) return "";
    if (typeof value === "object") return headersRaw;
    return String(value);
  };

  const handleFieldChange = (field: FieldSpec, value: string) => {
    if (field.name === "headers") {
      setHeadersRaw(value);
      try {
        const parsed = value.trim() === "" ? {} : JSON.parse(value);
        onChange({ ...config, headers: parsed });
      } catch {
        // JSON 解析失败时不更新 config，保留用户输入
      }
      return;
    }
    if (field.name === "hologres_port") {
      onChange({ ...config, hologres_port: value === "" ? undefined : parseInt(value, 10) });
      return;
    }
    onChange({ ...config, [field.name]: value });
  };

  return (
    <div className="wfp-ds-conn">
      <style>{styles}</style>
      {meta.fields.map((field) => {
        const fieldValue = getFieldValue(field);
        const isEmpty = fieldValue === "";
        const showRequired = field.required && isEmpty;
        return (
          <label key={field.name} className="wfp-ds-conn__field">
            <span className="wfp-ds-conn__label">{field.label}{field.required && <span className="wfp-ds-conn__required">*</span>}</span>
            {field.name === "headers" ? (
              <textarea
                className="wfp-ds-conn__input wfp-ds-conn__input--textarea"
                value={fieldValue}
                onChange={(e) => handleFieldChange(field, e.target.value)}
                rows={3}
                placeholder='{"Content-Type": "application/json"}'
              />
            ) : field.name === "method" ? (
              <select
                className="wfp-ds-conn__input wfp-ds-conn__input--select"
                value={fieldValue || "GET"}
                onChange={(e) => handleFieldChange(field, e.target.value)}
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
                <option value="DELETE">DELETE</option>
                <option value="PATCH">PATCH</option>
              </select>
            ) : field.name === "kafka_offset" ? (
              <select
                className="wfp-ds-conn__input wfp-ds-conn__input--select"
                value={fieldValue || "earliest"}
                onChange={(e) => handleFieldChange(field, e.target.value)}
              >
                <option value="earliest">earliest</option>
                <option value="latest">latest</option>
              </select>
            ) : (
              <input
                type={field.mask ? "password" : "text"}
                className="wfp-ds-conn__input"
                value={fieldValue}
                onChange={(e) => handleFieldChange(field, e.target.value)}
                placeholder={field.defaultValue ?? ""}
              />
            )}
            {showRequired && (
              <span className="wfp-ds-conn__hint">此字段为必填</span>
            )}
          </label>
        );
      })}
    </div>
  );
}

const styles = `
.wfp-ds-conn {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}
.wfp-ds-conn__field {
  display: block;
  margin-bottom: 8px;
}
.wfp-ds-conn__label {
  display: block;
  font-size: 12px;
  color: #555;
  margin-bottom: 2px;
}
.wfp-ds-conn__required {
  color: #e53935;
  margin-left: 2px;
}
.wfp-ds-conn__input {
  width: 100%;
  padding: 4px 8px;
  border: 1px solid #ccc;
  border-radius: 3px;
  font-size: 12px;
  box-sizing: border-box;
  background: #fff;
  color: #333;
}
.wfp-ds-conn__input--textarea {
  resize: vertical;
  font-family: monospace;
}
.wfp-ds-conn__input--select {
  cursor: pointer;
}
.wfp-ds-conn__hint {
  display: block;
  font-size: 11px;
  color: #e53935;
  margin-top: 2px;
}
.wfp-theme--dark .wfp-ds-conn__label {
  color: ${darkTokens.text.secondary};
}
.wfp-theme--dark .wfp-ds-conn__input {
  background: ${darkTokens.surface.bg};
  border-color: ${darkTokens.surface.border};
  color: ${darkTokens.text.primary};
}
.wfp-theme--dark .wfp-ds-conn__hint {
  color: ${darkTokens.danger};
}
`;