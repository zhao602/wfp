import type { WorkflowCanvasState } from "../../canvasTypes";
import type { SubWorkflowNodeConfiguration } from "../../../workflow-definition/definitionTypes";
import KeyValueMapEditor from "../../KeyValueMapEditor";
import { Field } from "../shared/Field";

export function SubWorkflowConfigForm({
  config,
  onChange,
  canvasState,
  targetNodeId,
}: {
  config: SubWorkflowNodeConfiguration;
  onChange: (config: SubWorkflowNodeConfiguration) => void;
  canvasState: WorkflowCanvasState;
  targetNodeId?: string;
}) {
  return (
    <div style={{ fontSize: "12px" }}>
      <Field nodeType="subworkflow" field="subworkflow_definition_id" label="子工作流定义 ID">
        <input
          type="text"
          className="wfp-ncp-input"
          value={config.subworkflow_definition_id}
          onChange={(e) => onChange({ ...config, subworkflow_definition_id: e.target.value })}
        />
      </Field>
      <Field nodeType="subworkflow" field="context_strategy" label="上下文策略">
        <select
          className="wfp-ncp-select"
          value={config.context_strategy}
          onChange={(e) =>
            onChange({ ...config, context_strategy: e.target.value as SubWorkflowNodeConfiguration["context_strategy"] })
          }
        >
          <option value="inherit">继承</option>
          <option value="isolate">隔离</option>
        </select>
      </Field>
      <Field nodeType="subworkflow" field="max_nesting_depth" label="最大嵌套深度">
        <input
          type="number"
          className="wfp-ncp-input"
          value={config.max_nesting_depth}
          onChange={(e) => onChange({ ...config, max_nesting_depth: Number(e.target.value) })}
        />
      </Field>
      <KeyValueMapEditor
        value={config.input_mapping}
        onChange={(v) => onChange({ ...config, input_mapping: v })}
        keyLabel="子工作流参数名"
        valueLabel="父状态字段引用"
        valueColumnMode={{ kind: "variableReference", canvasState, targetNodeId }}
        sectionTitle="输入映射"
        directionHint="父工作流 → 子工作流"
      />
      <KeyValueMapEditor
        value={config.output_mapping}
        onChange={(v) => onChange({ ...config, output_mapping: v })}
        keyLabel="父状态字段名"
        valueLabel="子工作流输出参数名"
        valueColumnMode={{ kind: "freeText" }}
        sectionTitle="输出映射"
        directionHint="子工作流 → 父工作流"
      />
    </div>
  );
}
