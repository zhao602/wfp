import { darkTokens, useThemeMode } from "../../../theme/themeMode";
import type {
  DynamicInjectorNodeConfiguration,
  NodeTemplate,
  NodeType,
} from "../../../workflow-definition/definitionTypes";
import StaticArgumentsEditor from "../../StaticArgumentsEditor";
import { PanelIcon } from "../shared/PanelIcon";
import { Field } from "../shared/Field";

export function DynamicInjectorConfigForm({
  config,
  onChange,
}: {
  config: DynamicInjectorNodeConfiguration;
  onChange: (config: DynamicInjectorNodeConfiguration) => void;
}) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";

  // L-09（fix_agent_null_length DC-05）：node_templates 经导入/恢复链可为
  // null/undefined/非数组（:1776 .map 渲染崩溃），入口单点归一回落空清单；
  // 合法数组同引用零回归，交互回写即自愈配置。
  const templates: NodeTemplate[] = Array.isArray(config.node_templates)
    ? config.node_templates
    : [];

  const updateTemplate = (idx: number, patch: Partial<NodeTemplate>) => {
    const next = [...templates];
    next[idx] = { ...next[idx], ...patch };
    onChange({ ...config, node_templates: next });
  };

  const addTemplate = () => {
    onChange({
      ...config,
      node_templates: [
        ...templates,
        { template_id: "", node_type: "agent", name: "", configuration: {} },
      ],
    });
  };

  const removeTemplate = (idx: number) => {
    onChange({
      ...config,
      node_templates: templates.filter((_, i) => i !== idx),
    });
  };

  return (
    <div style={{ fontSize: "12px" }}>
      <Field nodeType="dynamic_injector" field="injection_condition" label="注入条件">
        <input
          type="text"
          className="wfp-ncp-input"
          value={config.injection_condition}
          onChange={(e) => onChange({ ...config, injection_condition: e.target.value })}
        />
      </Field>
      <Field nodeType="dynamic_injector" field="max_injection_count" label="最大注入数量">
        <input
          type="number"
          className="wfp-ncp-input"
          value={config.max_injection_count}
          onChange={(e) => onChange({ ...config, max_injection_count: Number(e.target.value) })}
        />
      </Field>
      <Field nodeType="dynamic_injector" field="target_position" label="目标位置（可选）">
        <input
          type="text"
          className="wfp-ncp-input"
          value={config.target_position ?? ""}
          onChange={(e) => onChange({ ...config, target_position: e.target.value })}
        />
      </Field>
      <div className="wfp-ncp-subblock">
        <div className="wfp-ncp-section-label">模板节点列表</div>
        {templates.map((tpl, idx) => (
          <div
            key={idx}
            className="wfp-ncp-subcard"
            style={{ borderColor: isDark ? darkTokens.surface.divider : "#e8e8e8" }}
          >
            <div className="wfp-ncp-subcard-head">
              <span className="wfp-ncp-subcard-title">模板 {idx + 1}</span>
              <button
                type="button"
                className="wfp-ncp-btn wfp-ncp-btn--danger wfp-ncp-btn--icon"
                title="移除该模板"
                aria-label="移除该模板"
                onClick={() => removeTemplate(idx)}
              >
                <PanelIcon name="close" size={12} />
              </button>
            </div>
            <Field nodeType="dynamic_injector" field="node_templates" label="模板 ID">
              <input
                type="text"
                className="wfp-ncp-input"
                value={tpl.template_id}
                onChange={(e) => updateTemplate(idx, { template_id: e.target.value })}
              />
            </Field>
            <Field nodeType="dynamic_injector" field="node_templates" label="节点类型">
              <select
                className="wfp-ncp-select"
                value={tpl.node_type}
                onChange={(e) => updateTemplate(idx, { node_type: e.target.value as NodeType })}
              >
                <option value="trigger">触发器</option>
                <option value="agent">Agent</option>
                <option value="data_source">数据源</option>
                <option value="control_flow">控制流</option>
                <option value="subworkflow">子工作流</option>
                <option value="dynamic_injector">动态注入</option>
                <option value="data_transform">数据转换</option>
              </select>
            </Field>
            <Field nodeType="dynamic_injector" field="node_templates" label="模板名称">
              <input
                type="text"
                className="wfp-ncp-input"
                value={tpl.name}
                onChange={(e) => updateTemplate(idx, { name: e.target.value })}
              />
            </Field>
            <StaticArgumentsEditor
              value={tpl.configuration}
              onChange={(v) => updateTemplate(idx, { configuration: v })}
              keyLabel="配置键"
              sectionTitle="模板配置"
            />
          </div>
        ))}
        <button
          type="button"
          className="wfp-ncp-btn wfp-ncp-btn--ghost wfp-ncp-subcard-add"
          onClick={addTemplate}
        >
          <PanelIcon name="plus" size={12} />
          + 新增模板
        </button>
      </div>
    </div>
  );
}
