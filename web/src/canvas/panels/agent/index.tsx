import type { WorkflowCanvasState } from "../../canvasTypes";
import type { AgentNodeConfiguration } from "../../../workflow-definition/definitionTypes";
import type { ApplyTemplateResult } from "../../applyAgentTemplate";
import type { AgentTemplate } from "../../../market/marketTypes";
import AgentTemplateSelectionList from "../../AgentTemplateSelectionList";
import ModelSelectionList from "../../ModelSelectionList";
import ToolSelectionList from "../../ToolSelectionList";
import AgentInputListEditor from "../../AgentInputListEditor";
import KeyValueMapEditor from "../../KeyValueMapEditor";
import { PREDEFINED_TYPE_NAMES } from "../../mapEditorShared";
import { Field } from "../shared/Field";

export function AgentConfigForm({
  config,
  onChange,
  canvasState,
  targetNodeId,
  onCanvasStateChange,
  onApplyTemplate,
  appliedTemplateName,
}: {
  config: AgentNodeConfiguration;
  onChange: (config: AgentNodeConfiguration) => void;
  canvasState: WorkflowCanvasState;
  targetNodeId?: string;
  onCanvasStateChange?: (update: React.SetStateAction<WorkflowCanvasState>) => void;
  onApplyTemplate?: (template: AgentTemplate) => ApplyTemplateResult | null;
  appliedTemplateName?: string;
}) {
  return (
    <div className="wfp-ncp-agent-form">
      <div className="wfp-ncp-agent-section">
        <div className="wfp-ncp-section-label">模板预设</div>
        {onCanvasStateChange && onApplyTemplate && (
          <Field nodeType="agent" field="template" label="Agent 模板">
            <AgentTemplateSelectionList
              targetNodeId={targetNodeId ?? ""}
              onApplyTemplate={onApplyTemplate}
              appliedTemplateName={appliedTemplateName}
            />
          </Field>
        )}
      </div>
      <div className="wfp-ncp-agent-section">
        <div className="wfp-ncp-section-label">模型与提示词</div>
        <Field nodeType="agent" field="model_identifier" label="模型标识">
          <ModelSelectionList
            value={config.model_identifier}
            onChange={(v) => onChange({ ...config, model_identifier: v })}
          />
        </Field>
        <Field nodeType="agent" field="system_prompt" label="系统提示词">
          <textarea
            className="wfp-ncp-input"
            value={config.system_prompt}
            onChange={(e) => onChange({ ...config, system_prompt: e.target.value })}
            rows={4}
            style={{ width: "100%" }}
          />
        </Field>
      </div>
      <div className="wfp-ncp-agent-section">
        <div className="wfp-ncp-section-label">工具与输入</div>
        <Field nodeType="agent" field="available_tools" label="可用工具">
          <ToolSelectionList
            value={config.available_tools}
            onChange={(v) => onChange({ ...config, available_tools: v })}
          />
        </Field>
        <Field nodeType="agent" field="inputs" label="输入引用">
          <AgentInputListEditor
            canvasState={canvasState}
            targetNodeId={targetNodeId}
            inputs={config.inputs}
            onChange={(inputs) => onChange({ ...config, inputs })}
          />
        </Field>
      </div>
      <KeyValueMapEditor
        value={config.output_schema ?? {}}
        onChange={(v) => onChange({ ...config, output_schema: Object.keys(v).length === 0 ? undefined : v })}
        keyLabel="字段名"
        valueLabel="类型"
        valueColumnMode={{ kind: "typeNameSelect", options: PREDEFINED_TYPE_NAMES }}
        sectionTitle="输出约束"
      />
    </div>
  );
}
