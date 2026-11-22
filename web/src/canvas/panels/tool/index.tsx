import type { WorkflowCanvasState } from "../../canvasTypes";
import type { ToolNodeConfiguration } from "../../../workflow-definition/definitionTypes";
import KeyValueMapEditor from "../../KeyValueMapEditor";
import StaticArgumentsEditor from "../../StaticArgumentsEditor";
import { PanelIcon } from "../shared/PanelIcon";
import { Field } from "../shared/Field";

export function ToolConfigForm({
  config,
  onChange,
  canvasState,
  targetNodeId,
}: {
  config: ToolNodeConfiguration;
  onChange: (config: ToolNodeConfiguration) => void;
  canvasState: WorkflowCanvasState;
  targetNodeId?: string;
}) {
  return (
    <div style={{ fontSize: "12px" }}>
      {/* 迁移提示条（merge_tool_into_agent_node FR-3 规则 2 / DC-C / CHG-15）：
          tool 独立节点已并入 Agent 节点工具选项，存量配置字段仍可编辑查看 */}
      <div className="wfp-ncp-notice">
        <span className="wfp-ncp-notice-glyph">
          <PanelIcon name="info" size={12} />
        </span>
        <span>该节点类型已并入 Agent 节点，建议迁移为 Agent 节点的工具选项</span>
      </div>
      <Field nodeType="tool" field="tool_identifier" label="工具标识">
        <input
          type="text"
          className="wfp-ncp-input"
          value={config.tool_identifier}
          onChange={(e) => onChange({ ...config, tool_identifier: e.target.value })}
        />
      </Field>
      <Field nodeType="tool" field="tool_source" label="工具来源">
        <select
          className="wfp-ncp-select"
          value={config.tool_source}
          onChange={(e) =>
            onChange({ ...config, tool_source: e.target.value as ToolNodeConfiguration["tool_source"] })
          }
        >
          <option value="local">本地</option>
          <option value="mcp">MCP</option>
        </select>
      </Field>
      <KeyValueMapEditor
        value={config.argument_mapping}
        onChange={(v) => onChange({ ...config, argument_mapping: v })}
        keyLabel="工具参数名"
        valueLabel="状态字段引用"
        valueColumnMode={{ kind: "variableReference", canvasState, targetNodeId }}
        sectionTitle="参数映射"
        excludedKeys={new Set(Object.keys(config.static_arguments))}
      />
      <StaticArgumentsEditor
        value={config.static_arguments}
        onChange={(v) => onChange({ ...config, static_arguments: v })}
        keyLabel="工具参数名"
        sectionTitle="静态参数"
        excludedKeys={new Set(Object.keys(config.argument_mapping))}
      />
    </div>
  );
}
