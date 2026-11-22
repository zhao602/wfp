import type { CanvasNode } from "../../canvasTypes";
import type { ControlFlowNodeConfiguration } from "../../../workflow-definition/definitionTypes";
import BranchConditionEditor from "../../BranchConditionEditor";
import { REVIEW_INSTRUCTIONS_MAX_LENGTH } from "../../mapEditorShared";
import { Field } from "../shared/Field";

export function ControlFlowConfigForm({
  config,
  onChange,
  allNodes,
}: {
  config: ControlFlowNodeConfiguration;
  onChange: (config: ControlFlowNodeConfiguration) => void;
  allNodes: CanvasNode[];
}) {
  return (
    <div style={{ fontSize: "12px" }}>
      <Field nodeType="control_flow" field="control_flow_kind" label="控制流子类型">
        <select
          className="wfp-ncp-select"
          value={config.control_flow_kind}
          onChange={(e) =>
            onChange({ ...config, control_flow_kind: e.target.value as ControlFlowNodeConfiguration["control_flow_kind"] })
          }
        >
          <option value="conditional_branch">条件分支</option>
          <option value="loop">循环</option>
          <option value="parallel_join">并行汇聚</option>
          <option value="human_review_gate">人工审核</option>
        </select>
      </Field>
      {config.control_flow_kind === "conditional_branch" && (
        <>
          <BranchConditionEditor
            value={config.conditions}
            onChange={(v) => onChange({ ...config, conditions: v })}
            allNodes={allNodes}
          />
          <Field nodeType="control_flow" field="default_branch_target" label="默认分支目标">
            <select
              className="wfp-ncp-select"
              value={config.default_branch_target ?? ""}
              onChange={(e) => onChange({ ...config, default_branch_target: e.target.value.length === 0 ? undefined : e.target.value })}
            >
              <option value="">— 无默认分支 —</option>
              {allNodes.map((n) => (
                <option key={n.data.node_id} value={n.data.node_id}>{n.data.name}</option>
              ))}
            </select>
          </Field>
        </>
      )}
      {config.control_flow_kind === "loop" && (
        <>
          <Field nodeType="control_flow" field="loop_body_entry" label="循环体入口">
            <input
              type="text"
              className="wfp-ncp-input"
              value={config.loop_body_entry ?? ""}
              onChange={(e) => onChange({ ...config, loop_body_entry: e.target.value })}
            />
          </Field>
          <Field nodeType="control_flow" field="loop_termination_condition" label="终止条件">
            <input
              type="text"
              className="wfp-ncp-input"
              value={config.loop_termination_condition ?? ""}
              onChange={(e) => onChange({ ...config, loop_termination_condition: e.target.value })}
            />
          </Field>
          <Field nodeType="control_flow" field="max_iterations" label="最大迭代次数">
            <input
              type="number"
              className="wfp-ncp-input"
              value={config.max_iterations ?? ""}
              onChange={(e) => onChange({ ...config, max_iterations: Number(e.target.value) })}
            />
          </Field>
        </>
      )}
      {config.control_flow_kind === "parallel_join" && (
        <Field nodeType="control_flow" field="parallel_failure_strategy" label="并行失败策略">
          <select
            className="wfp-ncp-select"
            value={config.parallel_failure_strategy}
            onChange={(e) =>
              onChange({ ...config, parallel_failure_strategy: e.target.value as ControlFlowNodeConfiguration["parallel_failure_strategy"] })
            }
          >
            <option value="fast_fail">快速失败</option>
            <option value="wait_all">等待全部</option>
          </select>
        </Field>
      )}
      {config.control_flow_kind === "human_review_gate" && (
        <>
          <Field nodeType="control_flow" field="review_instructions" label="审核说明">
            <textarea
              className="wfp-ncp-input"
              value={config.review_instructions ?? ""}
              maxLength={REVIEW_INSTRUCTIONS_MAX_LENGTH}
              onChange={(e) => onChange({ ...config, review_instructions: e.target.value })}
              rows={3}
              style={{ width: "100%" }}
            />
          </Field>
          <Field nodeType="control_flow" field="review_timeout_action" label="审核超时动作">
            <select
              className="wfp-ncp-select"
              value={config.review_timeout_action}
              onChange={(e) =>
                onChange({ ...config, review_timeout_action: e.target.value as "reject" | "wait" })
              }
            >
              <option value="reject">拒绝</option>
              <option value="wait">等待</option>
            </select>
          </Field>
        </>
      )}
    </div>
  );
}
