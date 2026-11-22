// =============================================================================
// 代码节点配置面板（src/canvas/panels/code/index.tsx）
//
// 职责：code 节点类型专属配置表单（脚本/入口函数/依赖清单/params 注入输入
// 引用/声明性 output_schema/沙箱策略），由 NodeConfigurationPanel 按
// isCodeConfig 分发进入（T1.2 独立分支拆分）。
// 来源：自 NodeConfigurationPanel.tsx 纯搬运，代码零语义变化，仅补 export。
// =============================================================================

import type { CodeNodeConfiguration } from "../../../workflow-definition/definitionTypes";
import type { WorkflowCanvasState } from "../../canvasTypes";
import { PREDEFINED_TYPE_NAMES } from "../../mapEditorShared";
import KeyValueMapEditor from "../../KeyValueMapEditor";
import AgentInputListEditor from "../../AgentInputListEditor";
import { Field } from "../shared/Field";

/** 代码节点配置表单：脚本 + 入口/依赖登记 + params 注入输入集 + 声明性 output_schema + 沙箱策略 */
export function CodeConfigForm({
  config,
  onChange,
  canvasState,
  targetNodeId,
}: {
  config: CodeNodeConfiguration;
  onChange: (config: CodeNodeConfiguration) => void;
  canvasState: WorkflowCanvasState;
  targetNodeId?: string;
}) {
  return (
    <div style={{ fontSize: "12px" }}>
      <Field nodeType="code" field="language" label="脚本语言">
        <select
          className="wfp-ncp-select"
          value={config.language}
          onChange={(e) => onChange({ ...config, language: e.target.value })}
        >
          {config.language !== "python" && (
            <option value={config.language}>
              {config.language}（本期不支持，请改为 python）
            </option>
          )}
          <option value="python">python</option>
        </select>
      </Field>
      <Field nodeType="code" field="code" label="脚本">
        <textarea
          className="wfp-ncp-input"
          value={config.code ?? ""}
          onChange={(e) => onChange({ ...config, code: e.target.value })}
          rows={8}
          style={{ width: "100%", fontFamily: "monospace" }}
        />
      </Field>
      <Field nodeType="code" field="entry_function" label="入口函数">
        <input
          type="text"
          className="wfp-ncp-input"
          value={config.entry_function ?? "main"}
          onChange={(e) => onChange({ ...config, entry_function: e.target.value })}
        />
      </Field>
      <Field nodeType="code" field="dependencies" label="依赖清单">
        <input
          type="text"
          className="wfp-ncp-input"
          value={(config.dependencies ?? []).join(", ")}
          onChange={(e) => {
            const next = e.target.value
              .split(",")
              .map((s) => s.trim())
              .filter((s) => s.length > 0);
            onChange({ ...config, dependencies: next.length > 0 ? next : undefined });
          }}
        />
      </Field>
      <Field nodeType="code" field="inputs" label="输入引用（params 注入）">
        <AgentInputListEditor
          canvasState={canvasState}
          targetNodeId={targetNodeId}
          inputs={config.inputs}
          onChange={(inputs) => onChange({ ...config, inputs })}
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
        sectionTitle="输出结构声明（声明语义，执行层不校验）"
      />
      <Field nodeType="code" field="sandbox_policy_id" label="沙箱策略">
        <input
          type="text"
          className="wfp-ncp-input"
          value={config.sandbox_policy_id ?? ""}
          onChange={(e) =>
            onChange({
              ...config,
              sandbox_policy_id: e.target.value.trim() === "" ? undefined : e.target.value,
            })
          }
        />
      </Field>
    </div>
  );
}
