// =============================================================================
// 静态参数编辑器守卫测试（canvas/__tests__/StaticArgumentsEditor.guard.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/StaticArgumentsEditor.guard.test.tsx
//
// 覆盖（fix_agent_null_length 隐患 L-15 / DC-05，五要素映射——
//       触发链：tool static_arguments 直连）：
//   L-15-d value=null → Object.entries(null) 原崩溃，现空映射提示安全渲染
//   L-15-e value=undefined → 同口径
//   L-15-f 合法对照：非空映射渲染行（既有功能零回归）
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import StaticArgumentsEditor from "../StaticArgumentsEditor";

describe("L-15 StaticArgumentsEditor value 运行时宽容归一（fix_agent_null_length）", () => {
  it("L-15-d value=null：不崩溃，空映射提示「暂无静态参数」", () => {
    const { container } = render(
      <StaticArgumentsEditor
        value={null as unknown as Record<string, unknown>}
        onChange={vi.fn()}
        keyLabel="参数名"
      />,
    );
    expect(screen.getByText("暂无静态参数")).toBeTruthy();
    expect(container.querySelectorAll(".wfp-sae-row").length).toBe(0);
  });

  it("L-15-e value=undefined：同口径安全渲染", () => {
    const { container } = render(
      <StaticArgumentsEditor
        value={undefined as unknown as Record<string, unknown>}
        onChange={vi.fn()}
        keyLabel="参数名"
      />,
    );
    expect(screen.getByText("暂无静态参数")).toBeTruthy();
    expect(container.querySelectorAll(".wfp-sae-row").length).toBe(0);
  });

  it("L-15-f 合法对照：非空映射渲染行", () => {
    const { container } = render(
      <StaticArgumentsEditor
        value={{ top_n: 5 }}
        onChange={vi.fn()}
        keyLabel="参数名"
      />,
    );
    expect(container.querySelectorAll(".wfp-sae-row").length).toBe(1);
    expect(screen.queryByText("暂无静态参数")).toBeNull();
  });
});
