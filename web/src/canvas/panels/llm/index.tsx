// =============================================================================
// 大模型节点配置面板（src/canvas/panels/llm/index.tsx）
//
// 职责：llm 节点类型专属配置表单（模型标识/系统与用户提示词/输入引用/六采样
// 参数 None=未配置/登记性 output_schema），由 NodeConfigurationPanel 按
// isLlmConfig 分发进入（T1.2 独立分支拆分）。
// 来源：自 NodeConfigurationPanel.tsx 纯搬运，代码零语义变化，仅补 export。
// =============================================================================

import type { LlmNodeConfiguration } from "../../../workflow-definition/definitionTypes";
import type { WorkflowCanvasState } from "../../canvasTypes";
import { PREDEFINED_TYPE_NAMES } from "../../mapEditorShared";
import KeyValueMapEditor from "../../KeyValueMapEditor";
import AgentInputListEditor from "../../AgentInputListEditor";
import ModelSelectionList from "../../ModelSelectionList";
import { Field } from "../shared/Field";

// ---------------------------------------------------------------------------
// 大模型 / 代码节点配置表单（llm_code_nodes_v2 任务 7.2 / CHG-F03，渲染守卫链分支）
// ---------------------------------------------------------------------------

/** 大模型节点配置表单：提示词 + 输入选择集 + 六采样参数（None=未配置）+ 登记性 output_schema */
export function LlmConfigForm({
  config,
  onChange,
  canvasState,
  targetNodeId,
}: {
  config: LlmNodeConfiguration;
  onChange: (config: LlmNodeConfiguration) => void;
  canvasState: WorkflowCanvasState;
  targetNodeId?: string;
}) {
  return (
    <div style={{ fontSize: "12px" }}>
      <Field nodeType="llm" field="model_identifier" label="模型标识">
        <ModelSelectionList
          value={config.model_identifier}
          onChange={(v) => onChange({ ...config, model_identifier: v })}
        />
      </Field>
      <Field nodeType="llm" field="system_prompt" label="系统提示词">
        <textarea
          className="wfp-ncp-input"
          value={config.system_prompt ?? ""}
          onChange={(e) => onChange({ ...config, system_prompt: e.target.value })}
          rows={3}
          style={{ width: "100%" }}
        />
      </Field>
      <Field nodeType="llm" field="user_prompt" label="用户提示词">
        <textarea
          className="wfp-ncp-input"
          value={config.user_prompt ?? ""}
          onChange={(e) => onChange({ ...config, user_prompt: e.target.value })}
          rows={3}
          style={{ width: "100%" }}
        />
      </Field>
      <Field nodeType="llm" field="inputs" label="输入引用">
        <AgentInputListEditor
          canvasState={canvasState}
          targetNodeId={targetNodeId}
          inputs={config.inputs}
          onChange={(inputs) => onChange({ ...config, inputs })}
        />
      </Field>
      <Field nodeType="llm" field="temperature" label="温度 temperature">
        <input
          type="number"
          className="wfp-ncp-input"
          min={0}
          max={2}
          step={0.1}
          value={config.temperature ?? ""}
          onChange={(e) => {
            const raw = e.target.value.trim();
            const parsed = Number(raw);
            onChange({
              ...config,
              temperature: raw === "" || !Number.isFinite(parsed) ? undefined : parsed,
            });
          }}
        />
      </Field>
      <Field nodeType="llm" field="top_p" label="Top P">
        <input
          type="number"
          className="wfp-ncp-input"
          min={0}
          max={1}
          step={0.01}
          value={config.top_p ?? ""}
          onChange={(e) => {
            const raw = e.target.value.trim();
            const parsed = Number(raw);
            onChange({
              ...config,
              top_p: raw === "" || !Number.isFinite(parsed) ? undefined : parsed,
            });
          }}
        />
      </Field>
      <Field nodeType="llm" field="top_k" label="Top K">
        <input
          type="number"
          className="wfp-ncp-input"
          min={0}
          max={100}
          step={1}
          value={config.top_k ?? ""}
          onChange={(e) => {
            const raw = e.target.value.trim();
            const parsed = Number(raw);
            onChange({
              ...config,
              top_k: raw === "" || !Number.isFinite(parsed) ? undefined : Math.round(parsed),
            });
          }}
        />
      </Field>
      <Field nodeType="llm" field="max_tokens" label="最大生成长度 max_tokens">
        <input
          type="number"
          className="wfp-ncp-input"
          min={1}
          max={32768}
          step={1}
          value={config.max_tokens ?? ""}
          onChange={(e) => {
            const raw = e.target.value.trim();
            const parsed = Number(raw);
            onChange({
              ...config,
              max_tokens:
                raw === "" || !Number.isFinite(parsed) ? undefined : Math.round(parsed),
            });
          }}
        />
      </Field>
      <Field nodeType="llm" field="stop" label="停止序列 stop">
        <input
          type="text"
          className="wfp-ncp-input"
          value={(config.stop ?? []).join(", ")}
          onChange={(e) => {
            const next = e.target.value
              .split(",")
              .map((s) => s.trim())
              .filter((s) => s.length > 0)
              .slice(0, 4);
            onChange({ ...config, stop: next.length > 0 ? next : undefined });
          }}
        />
      </Field>
      <Field nodeType="llm" field="seed" label="随机种子 seed">
        <input
          type="number"
          className="wfp-ncp-input"
          min={0}
          max={2147483647}
          step={1}
          value={config.seed ?? ""}
          onChange={(e) => {
            const raw = e.target.value.trim();
            const parsed = Number(raw);
            onChange({
              ...config,
              seed: raw === "" || !Number.isFinite(parsed) ? undefined : Math.round(parsed),
            });
          }}
        />
      </Field>
      <KeyValueMapEditor
        value={config.output_schema ?? {}}
        onChange={(v) =>
          onChange({
            ...config,
            output_schema: Object.keys(v).length === 0 ? undefined : v,
          })
        }
        keyLabel="字段名"
        valueLabel="类型"
        valueColumnMode={{ kind: "typeNameSelect", options: PREDEFINED_TYPE_NAMES }}
        sectionTitle="输出结构约束（当前版本不生效，声明保留）"
      />
    </div>
  );
}
