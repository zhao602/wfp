// =============================================================================
// Agent 输入选择集编辑器守卫测试（canvas/__tests__/AgentInputListEditor.guard.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/AgentInputListEditor.guard.test.tsx
//
// 覆盖（fix_agent_null_length 隐患 L-11 / DC-05 / INV-01，五要素映射）：
//   L-11-a inputs=null → 隐式空态提示安全渲染（原 === undefined 判空对 null 崩溃）
//   L-11-b inputs=undefined → 隐式空态（既有语义对照）
//   L-11-c inputs=[] → 显式空清单提示（既有语义对照）
//   L-11-d 合法对照：非空清单渲染条目行
//   L-11-e 元素级空态：entry.reference=null → 不崩溃、「未绑定引用」警告呈现
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import AgentInputListEditor from "../AgentInputListEditor";
import type { AgentInputEntry } from "../../workflow-definition/definitionTypes";
import type { WorkflowCanvasState } from "../canvasTypes";

/** 空画布（引用选择器候选为空，不影响渲染断言） */
function makeCanvasState(): WorkflowCanvasState {
  return { nodes: [], edges: [] };
}

describe("L-11 inputs 运行时宽容归一（fix_agent_null_length）", () => {
  it("L-11-a inputs=null：不崩溃，隐式空态提示「注入全部上游命名输出」", () => {
    render(
      <AgentInputListEditor
        canvasState={makeCanvasState()}
        inputs={null as unknown as AgentInputEntry[]}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByText("未配置输入引用，当前将注入全部上游命名输出"),
    ).toBeTruthy();
    expect(screen.getByText("＋ 添加输入引用")).toBeTruthy();
  });

  it("L-11-b inputs=undefined：隐式空态（既有语义对照）", () => {
    render(
      <AgentInputListEditor
        canvasState={makeCanvasState()}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByText("未配置输入引用，当前将注入全部上游命名输出"),
    ).toBeTruthy();
  });

  it("L-11-c inputs=[]：显式空清单提示（既有语义对照）", () => {
    render(
      <AgentInputListEditor
        canvasState={makeCanvasState()}
        inputs={[]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText("已显式声明无输入")).toBeTruthy();
    expect(screen.getByText("恢复隐式注入")).toBeTruthy();
  });

  it("L-11-d 合法对照：非空清单渲染条目行、无空引用警告", () => {
    render(
      <AgentInputListEditor
        canvasState={makeCanvasState()}
        inputs={[{ reference: "n1.out" }]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByText("未配置输入引用，当前将注入全部上游命名输出")).toBeNull();
    expect(screen.queryByText("未绑定引用")).toBeNull();
  });

  it("L-11-e 元素级空态：entry.reference=null → 不崩溃、「未绑定引用」警告呈现", () => {
    const entries = [
      { reference: null as unknown as string },
    ] as AgentInputEntry[];
    render(
      <AgentInputListEditor
        canvasState={makeCanvasState()}
        inputs={entries}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText("未绑定引用")).toBeTruthy();
  });
});
