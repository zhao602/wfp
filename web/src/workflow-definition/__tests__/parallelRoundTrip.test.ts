// =============================================================================
// 并行声明 round-trip 贯通测试（workflow-definition/__tests__/parallelRoundTrip.test.ts）
//
// 执行：cd web && npx vitest run src/workflow-definition/__tests__/parallelRoundTrip.test.ts
//
// 覆盖用例（B13 T1 / D1）：
//   PRT-01 parallel_fanout 范例：导入 → 导出，parallel_branches / wait_policy /
//         parallel_failure_strategy 保真（修复前三字段被导出白名单静默剥离）
//   PRT-02 wait_n_of_m + wait_count 显式声明：导出保留 → 导入还原 →
//         再导出文本一致（往返稳定）
//   PRT-03 无并行声明的 control_flow（loop）：导出不含并行键（零脏字段）
// =============================================================================

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { canvasToYaml } from "../canvasToYamlExporter";
import { yamlToCanvas } from "../yamlToCanvasImporter";
import type {
  WorkflowCanvasState,
  CanvasNode,
} from "../../canvas/canvasTypes";
import type { ControlFlowNodeConfiguration } from "../definitionTypes";
import { isControlFlowConfig } from "../definitionTypes";

function readExample(relativePath: string): string {
  return readFileSync(
    resolve(process.cwd(), "..", "examples", relativePath),
    "utf-8",
  );
}

function makeStateWithControlFlow(
  config: ControlFlowNodeConfiguration,
): WorkflowCanvasState {
  const trigger: CanvasNode = {
    id: "n1",
    type: "trigger",
    position: { x: 0, y: 0 },
    data: {
      node_id: "n1",
      name: "手动触发",
      node_type: "trigger",
      configuration: {
        node_type: "trigger",
        trigger_type: "manual",
        input_template: {},
      },
    },
  };
  const controlFlow: CanvasNode = {
    id: "join",
    type: "control_flow",
    position: { x: 250, y: 0 },
    data: {
      node_id: "join",
      name: "汇聚节点",
      node_type: "control_flow",
      configuration: config,
    },
  };
  return {
    nodes: [trigger, controlFlow],
    edges: [{ id: "e1", source: "n1", target: "join" }],
  };
}

describe("并行声明 round-trip 贯通（B13 T1）", () => {
  it("PRT-01 parallel_fanout 范例：导入 → 导出，并行声明字段保真", () => {
    const imported = yamlToCanvas(
      readExample("parallel_execution/parallel_fanout.yaml"),
    );
    expect(imported.ok).toBe(true);
    if (!imported.ok) {
      throw new Error(imported.error);
    }

    const joinNode = imported.canvasState.nodes.find(
      (n) => n.data.node_id === "parallel_fanout",
    );
    expect(joinNode).toBeDefined();
    if (joinNode === undefined) {
      throw new Error("parallel_fanout 节点缺失");
    }
    const joinConfig = joinNode.data.configuration;
    expect(isControlFlowConfig(joinConfig)).toBe(true);
    if (!isControlFlowConfig(joinConfig)) {
      throw new Error("expected control_flow configuration");
    }
    expect(joinConfig.parallel_branches).toEqual(["branch_a", "branch_b"]);
    expect(joinConfig.wait_policy).toBe("wait_all");
    expect(joinConfig.parallel_failure_strategy).toBe("fast_fail");

    const exported = canvasToYaml(imported.canvasState);
    expect(exported).toContain("parallel_branches:");
    expect(exported).toContain("- branch_a");
    expect(exported).toContain("- branch_b");
    expect(exported).toContain("wait_policy: wait_all");
    expect(exported).toContain("parallel_failure_strategy: fast_fail");
  });

  it("PRT-02 wait_n_of_m + wait_count 显式声明：往返稳定（导出→导入→再导出文本一致）", () => {
    const joinConfig: ControlFlowNodeConfiguration = {
      node_type: "control_flow",
      control_flow_kind: "parallel_join",
      conditions: [],
      parallel_failure_strategy: "wait_all",
      parallel_branches: ["branch_a", "branch_b", "branch_c"],
      wait_policy: "wait_n_of_m",
      wait_count: 2,
      review_timeout_action: "wait",
    };
    const yamlText1 = canvasToYaml(makeStateWithControlFlow(joinConfig));
    expect(yamlText1).toContain("parallel_branches:");
    expect(yamlText1).toContain("- branch_c");
    expect(yamlText1).toContain("wait_policy: wait_n_of_m");
    expect(yamlText1).toContain("wait_count: 2");

    const imported = yamlToCanvas(yamlText1);
    expect(imported.ok).toBe(true);
    if (!imported.ok) {
      throw new Error(imported.error);
    }
    const restored = imported.canvasState.nodes.find(
      (n) => n.data.node_id === "join",
    )?.data.configuration;
    if (restored === undefined) {
      throw new Error("join 节点配置缺失");
    }
    expect(isControlFlowConfig(restored)).toBe(true);
    if (!isControlFlowConfig(restored)) {
      throw new Error("expected control_flow configuration");
    }
    expect(restored.parallel_branches).toEqual([
      "branch_a",
      "branch_b",
      "branch_c",
    ]);
    expect(restored.wait_policy).toBe("wait_n_of_m");
    expect(restored.wait_count).toBe(2);

    const yamlText2 = canvasToYaml(imported.canvasState);
    expect(yamlText2).toBe(yamlText1);
  });

  it("PRT-03 无并行声明的 control_flow：导出不含并行键（零脏字段）", () => {
    const loopConfig: ControlFlowNodeConfiguration = {
      node_type: "control_flow",
      control_flow_kind: "loop",
      conditions: [],
      parallel_failure_strategy: "fast_fail",
      loop_body_entry: "n1",
      loop_termination_condition: "attempts < 3",
      max_iterations: 3,
      review_timeout_action: "wait",
    };
    const yamlText = canvasToYaml(makeStateWithControlFlow(loopConfig));
    expect(yamlText).not.toContain("parallel_branches");
    expect(yamlText).not.toContain("wait_policy");
    expect(yamlText).not.toContain("wait_count");
    expect(yamlText).toContain("loop_body_entry: n1");
  });
});
