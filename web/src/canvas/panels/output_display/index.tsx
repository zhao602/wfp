// =============================================================================
// 输出展示节点配置面板（src/canvas/panels/output_display/index.tsx）
//
// 职责：output_display 节点类型专属配置表单（输出方式 screen/file 复选、
// 输出内容引用候选（collectUpstreamOutputCandidates 零改动复用，DC-05）、
// 展示标题），由 NodeConfigurationPanel 按 isOutputDisplayConfig 分发进入
//（T1.2 独立分支拆分）。
// 来源：自 NodeConfigurationPanel.tsx 纯搬运，代码零语义变化，仅补 export。
// =============================================================================

import type { OutputDisplayNodeConfiguration } from "../../../workflow-definition/definitionTypes";
import type { WorkflowCanvasState } from "../../canvasTypes";
import { collectUpstreamOutputCandidates } from "../../nodeIoTopology";
import { NODE_OUTPUT_CONTRACTS } from "../../nodeOutputContracts";
import { normalizeOutputMode, normalizeOutputReferences } from "../../../workflow-definition/outputDisplayConfig";
import OutputReferenceCheckboxList from "../../OutputReferenceCheckboxList";
import { Field, FieldCheckboxGroup } from "../shared/Field";

export function OutputDisplayConfigForm({
  config,
  onChange,
  canvasState,
  targetNodeId,
}: {
  config: OutputDisplayNodeConfiguration;
  onChange: (config: OutputDisplayNodeConfiguration) => void;
  canvasState: WorkflowCanvasState;
  targetNodeId: string;
}) {
  /** 上游有效输出候选（DC-05：collectUpstreamOutputCandidates 零改动复用） */
  const candidates = collectUpstreamOutputCandidates(
    canvasState,
    targetNodeId,
    NODE_OUTPUT_CONTRACTS,
  );
  /** 归一化输出方式（缺省 ≡ ["screen"]，兼容存量屏幕显示语义） */
  const modes = normalizeOutputMode(config.output_mode);
  /** 输出方式复选框回写：取消至空时 normalizeOutputMode 回落 ["screen"]（禁止"无任何输出方式"） */
  const toggleMode = (mode: "screen" | "file", checked: boolean) => {
    const next = checked ? [...modes, mode] : modes.filter((m) => m !== mode);
    onChange({ ...config, output_mode: normalizeOutputMode(next) });
  };
  return (
    <div style={{ fontSize: "12px" }}>
      <FieldCheckboxGroup
        nodeType="output_display"
        field="output_mode"
        label="输出方式"
      >
        <label className="wfp-ncp-odc-mode-row">
          <input
            type="checkbox"
            checked={modes.includes("screen")}
            onChange={(e) => toggleMode("screen", e.target.checked)}
          />
          屏幕显示
        </label>
        <label className="wfp-ncp-odc-mode-row">
          <input
            type="checkbox"
            checked={modes.includes("file")}
            onChange={(e) => toggleMode("file", e.target.checked)}
          />
          输出为文件
        </label>
      </FieldCheckboxGroup>
      <FieldCheckboxGroup
        nodeType="output_display"
        field="input_reference"
        label="输出内容（引用）"
      >
        <OutputReferenceCheckboxList
          value={normalizeOutputReferences(config.input_reference)}
          onChange={(refs) => onChange({ ...config, input_reference: refs })}
          candidates={candidates}
          deferredGroups={[
            {
              title: "上游输入参数",
              note: "待 upstream_input_ref 落地后启用",
            },
          ]}
        />
      </FieldCheckboxGroup>
      <Field nodeType="output_display" field="display_title" label="展示标题">
        <input
          type="text"
          className="wfp-ncp-input"
          value={config.display_title ?? ""}
          onChange={(e) => onChange({ ...config, display_title: e.target.value })}
        />
      </Field>
    </div>
  );
}
