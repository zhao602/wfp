// =============================================================================
// 分支条件编辑器守卫测试（canvas/__tests__/BranchConditionEditor.guard.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/BranchConditionEditor.guard.test.tsx
//
// 覆盖（fix_agent_null_length 隐患 L-08 / DC-05 / AC-06，五要素映射）：
//   L-08-a value=null（config.conditions 导入链空态）→ 空清单提示安全渲染
//   L-08-b value=undefined → 同口径
//   L-08-c 合法对照：非空 conditions 渲染行 + 无「目标节点不存在」误报
//   L-08-d 合法对照：目标节点不存在 → 警告呈现（既有功能零回归）
//   L-08-e 元素级空态：target_node_id/expression 为 null → 不崩溃、表达式空警告呈现
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import BranchConditionEditor from "../BranchConditionEditor";
import type { BranchCondition } from "../../workflow-definition/definitionTypes";
import type { CanvasNode, WorkflowCanvasState } from "../canvasTypes";

/** 画布节点壳（目标节点候选） */
function makeNode(nodeId: string, name: string): CanvasNode {
  const data = {
    node_id: nodeId,
    name,
    node_type: "agent" as const,
    configuration: {
      node_type: "agent" as const,
      model_identifier: "m",
      system_prompt: "",
      available_tools: [],
    },
  };
  return { id: nodeId, type: "agent", position: { x: 0, y: 0 }, data };
}

/** 双节点画布（n1 / n2） */
function makeCanvasState(): WorkflowCanvasState {
  return {
    nodes: [makeNode("n1", "节点一"), makeNode("n2", "节点二")],
    edges: [],
  };
}

describe("L-08 conditions 运行时宽容归一（fix_agent_null_length）", () => {
  it("L-08-a value=null：不崩溃，空清单提示「至少需要一条分支条件」", () => {
    const { container } = render(
      <BranchConditionEditor
        value={null as unknown as BranchCondition[]}
        onChange={vi.fn()}
        allNodes={makeCanvasState().nodes}
      />,
    );
    expect(screen.getByText("至少需要一条分支条件")).toBeTruthy();
    expect(container.querySelectorAll(".wfp-bce-cond").length).toBe(0);
  });

  it("L-08-b value=undefined：同口径安全渲染", () => {
    const { container } = render(
      <BranchConditionEditor
        value={undefined as unknown as BranchCondition[]}
        onChange={vi.fn()}
        allNodes={makeCanvasState().nodes}
      />,
    );
    expect(screen.getByText("至少需要一条分支条件")).toBeTruthy();
    expect(container.querySelectorAll(".wfp-bce-cond").length).toBe(0);
  });

  it("L-08-c 合法对照：存在目标节点的条件渲染行且无误报警告", () => {
    const conditions: BranchCondition[] = [
      { target_node_id: "n2", expression: "draft.score >= 80" },
    ];
    const { container } = render(
      <BranchConditionEditor
        value={conditions}
        onChange={vi.fn()}
        allNodes={makeCanvasState().nodes}
      />,
    );
    expect(container.querySelectorAll(".wfp-bce-cond").length).toBe(1);
    expect(screen.queryByText("目标节点不存在")).toBeNull();
    expect(screen.queryByText("条件表达式为空，将始终匹配")).toBeNull();
  });

  it("L-08-d 合法对照：目标节点不存在 → 警告呈现（既有功能零回归）", () => {
    const conditions: BranchCondition[] = [
      { target_node_id: "nX", expression: "ok" },
    ];
    render(
      <BranchConditionEditor
        value={conditions}
        onChange={vi.fn()}
        allNodes={makeCanvasState().nodes}
      />,
    );
    expect(screen.getByText("目标节点不存在")).toBeTruthy();
  });

  it("L-08-e 元素级空态：target_node_id/expression 为 null → 不崩溃、表达式空警告呈现、无目标误报", () => {
    const conditions = [
      {
        target_node_id: null as unknown as string,
        expression: null as unknown as string,
      },
    ] as BranchCondition[];
    const { container } = render(
      <BranchConditionEditor
        value={conditions}
        onChange={vi.fn()}
        allNodes={makeCanvasState().nodes}
      />,
    );
    expect(container.querySelectorAll(".wfp-bce-cond").length).toBe(1);
    expect(screen.getByText("条件表达式为空，将始终匹配")).toBeTruthy();
    expect(screen.queryByText("目标节点不存在")).toBeNull();
  });
});
