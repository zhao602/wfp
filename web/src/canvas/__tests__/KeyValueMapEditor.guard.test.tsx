// =============================================================================
// 键值对映射编辑器守卫测试（canvas/__tests__/KeyValueMapEditor.guard.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/KeyValueMapEditor.guard.test.tsx
//
// 覆盖（fix_agent_null_length 隐患 L-15 / DC-05，五要素映射——
//       触发链：trigger input_template / tool argument_mapping /
//               subworkflow input_mapping、output_mapping 直连）：
//   L-15-a value=null → Object.entries(null) 原崩溃，现空映射提示安全渲染
//   L-15-b value=undefined → 同口径
//   L-15-c 合法对照：非空映射渲染行（既有功能零回归）
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import KeyValueMapEditor from "../KeyValueMapEditor";

describe("L-15 KeyValueMapEditor value 运行时宽容归一（fix_agent_null_length）", () => {
  it("L-15-a value=null：不崩溃，空映射提示「暂无映射条目」", () => {
    const { container } = render(
      <KeyValueMapEditor
        value={null as unknown as Record<string, string>}
        onChange={vi.fn()}
        keyLabel="参数名"
        valueLabel="参数值"
        valueColumnMode={{ kind: "freeText" }}
      />,
    );
    expect(screen.getByText("暂无映射条目")).toBeTruthy();
    expect(container.querySelectorAll(".wfp-kvme-row").length).toBe(0);
  });

  it("L-15-b value=undefined：同口径安全渲染", () => {
    const { container } = render(
      <KeyValueMapEditor
        value={undefined as unknown as Record<string, string>}
        onChange={vi.fn()}
        keyLabel="参数名"
        valueLabel="参数值"
        valueColumnMode={{ kind: "freeText" }}
      />,
    );
    expect(screen.getByText("暂无映射条目")).toBeTruthy();
    expect(container.querySelectorAll(".wfp-kvme-row").length).toBe(0);
  });

  it("L-15-c 合法对照：非空映射渲染行且键列可见", () => {
    const { container } = render(
      <KeyValueMapEditor
        value={{ draft: "n1.out" }}
        onChange={vi.fn()}
        keyLabel="参数名"
        valueLabel="参数值"
        valueColumnMode={{ kind: "freeText" }}
      />,
    );
    expect(container.querySelectorAll(".wfp-kvme-row").length).toBe(1);
    const keyInput = container.querySelector(".wfp-kvme-key") as HTMLInputElement;
    expect(keyInput.value).toBe("draft");
  });
});
