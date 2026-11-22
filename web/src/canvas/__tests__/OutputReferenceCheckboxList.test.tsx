// =============================================================================
// 输出引用复选框列表组件测试（src/canvas/__tests__/OutputReferenceCheckboxList.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/OutputReferenceCheckboxList.test.tsx
//
// 覆盖 optimize_output_display_node T8.2 用例组（CHG-17 / FR-3 / DC-03）：
//   ① 受控勾选回写：勾选态与 value 一致、onChange 全量数组（追加保序/取消过滤）、
//     不修改入参数组（AC-3.2）
//   ② 候选分组渲染：「上游有效输出」分组、节点名/引用/类别徽标、「已选 N 项」摘要、
//     候选按引用表达式去重、候选为空提示（AC-3.1）
//   ③ 候选外引用「已失效」标记行：保持勾选、不可再勾选、可移除（AC-3.3）
//   ④ 依赖未满足占位分组：灰显标题与说明、复选框禁用、点击不触发 onChange、
//     不阻塞候选勾选回写（INV-D19 确定性降级）
//   ⑤ 双主题取色：浅色/深色分别取预期值（AC-3.7）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within, cleanup } from "@testing-library/react";
import { darkTokens } from "../../theme/themeMode";
import type { NamedOutputCandidate } from "../mapEditorShared";

// 主题 mock：外部变量切换浅色/深色，保留真实 darkTokens 供 <style> 插值
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

import OutputReferenceCheckboxList from "../OutputReferenceCheckboxList";

// ---------------------------------------------------------------------------
// 样例数据
// ---------------------------------------------------------------------------

/** 上游有效输出候选样例（直接上游 agent / 间接上游 tool / 上游 data_transform） */
const CANDIDATES: NamedOutputCandidate[] = [
  {
    nodeId: "agent-1",
    nodeName: "情报分析",
    outputName: "analysis_result",
    category: "结构化对象",
  },
  {
    nodeId: "tool-1",
    nodeName: "天气查询",
    outputName: "weather_result",
    category: "工具调用结果",
  },
  {
    nodeId: "dt-1",
    nodeName: "数据清洗",
    outputName: "clean_dataset",
    category: "数据集（记录集合）",
  },
];

/** 依赖未满足占位分组样例（上游输入参数待 upstream_input_ref 落地） */
const DEFERRED_GROUPS = [
  { title: "上游输入参数", note: "待 upstream_input_ref 落地后启用" },
];

/** 渲染受控组件（默认浅色、空勾选） */
function renderList(overrides?: {
  value?: string[];
  candidates?: NamedOutputCandidate[];
  deferredGroups?: { title: string; note: string }[];
}) {
  return render(
    <OutputReferenceCheckboxList
      value={overrides?.value ?? []}
      onChange={vi.fn()}
      candidates={overrides?.candidates ?? CANDIDATES}
      deferredGroups={overrides?.deferredGroups}
    />,
  );
}

/** 按行 title（nodeName（outputName））定位候选行内勾选框 */
function checkboxOfRow(rowTitle: string): HTMLInputElement {
  const row = screen.getByTitle(rowTitle);
  return within(row).getByRole("checkbox") as HTMLInputElement;
}

// ---------------------------------------------------------------------------
// ① 受控勾选回写
// ---------------------------------------------------------------------------

describe("① 受控勾选回写（AC-3.2）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });
  afterEach(() => {
    cleanup();
  });

  it("勾选候选 → onChange 全量数组；续勾追加保序；取消按引用过滤", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <OutputReferenceCheckboxList value={[]} onChange={onChange} candidates={CANDIDATES} />,
    );

    // 勾选首个候选 → 全量数组
    fireEvent.click(checkboxOfRow("情报分析（analysis_result）"));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith(["analysis_result"]);

    // 受控更新后续勾第二个 → 追加保序
    rerender(
      <OutputReferenceCheckboxList
        value={["analysis_result"]}
        onChange={onChange}
        candidates={CANDIDATES}
      />,
    );
    fireEvent.click(checkboxOfRow("天气查询（weather_result）"));
    expect(onChange).toHaveBeenLastCalledWith(["analysis_result", "weather_result"]);

    // 取消首个 → 过滤
    rerender(
      <OutputReferenceCheckboxList
        value={["analysis_result", "weather_result"]}
        onChange={onChange}
        candidates={CANDIDATES}
      />,
    );
    fireEvent.click(checkboxOfRow("情报分析（analysis_result）"));
    expect(onChange).toHaveBeenLastCalledWith(["weather_result"]);
  });

  it("勾选态完全由 value 驱动：已选候选勾选、未选候选不勾选", () => {
    renderList({ value: ["weather_result"] });
    expect(checkboxOfRow("天气查询（weather_result）").checked).toBe(true);
    expect(checkboxOfRow("情报分析（analysis_result）").checked).toBe(false);
    expect(screen.getByText("已选 1 项")).toBeTruthy();
  });

  it("不修改传入的 value/candidates 数组（受控纯渲染）", () => {
    const value: string[] = ["analysis_result"];
    const candidates: NamedOutputCandidate[] = CANDIDATES.map((c) => ({ ...c }));
    const onChange = vi.fn();
    render(
      <OutputReferenceCheckboxList value={value} onChange={onChange} candidates={candidates} />,
    );

    fireEvent.click(checkboxOfRow("天气查询（weather_result）"));
    expect(onChange).toHaveBeenCalledWith(["analysis_result", "weather_result"]);
    // 入参未被原地修改
    expect(value).toEqual(["analysis_result"]);
    expect(candidates.length).toBe(3);
    expect(candidates[0].outputName).toBe("analysis_result");
  });
});

// ---------------------------------------------------------------------------
// ② 候选分组渲染
// ---------------------------------------------------------------------------

describe("② 候选分组渲染（AC-3.1）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });
  afterEach(() => {
    cleanup();
  });

  it("渲染「上游有效输出」分组、节点名/引用/类别徽标与「已选 N 项」摘要", () => {
    renderList({ value: ["analysis_result"] });

    expect(screen.getByText("上游有效输出")).toBeTruthy();
    expect(screen.getByText("已选 1 项")).toBeTruthy();
    // 候选行：节点名 / 引用表达式 / 类别徽标
    expect(screen.getByText("情报分析")).toBeTruthy();
    expect(screen.getByText("analysis_result")).toBeTruthy();
    expect(screen.getByText("结构化对象")).toBeTruthy();
    // 行 title 为全量描述（nodeName（outputName））
    expect(screen.getByTitle("情报分析（analysis_result）")).toBeTruthy();
  });

  it("候选按引用表达式去重：同名命名输出多拥有者取首见", () => {
    renderList({
      candidates: [
        { nodeId: "agent-1", nodeName: "首个拥有者", outputName: "shared_result" },
        { nodeId: "agent-2", nodeName: "后续拥有者", outputName: "shared_result" },
      ],
    });
    // 仅一行候选，且展示首见拥有者
    expect(screen.getAllByText("shared_result").length).toBe(1);
    expect(screen.getByText("首个拥有者")).toBeTruthy();
    expect(screen.queryByText("后续拥有者")).toBeNull();
  });

  it("候选为空 → 「暂无上游可引用输出」提示条，不报错", () => {
    expect(() => renderList({ candidates: [] })).not.toThrow();
    expect(screen.getByText("暂无上游可引用输出")).toBeTruthy();
    expect(screen.getByText("已选 0 项")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// ③ 候选外引用「已失效」标记行
// ---------------------------------------------------------------------------

describe("③ 候选外引用「已失效」标记行（AC-3.3）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });
  afterEach(() => {
    cleanup();
  });

  it("value 含候选外引用 → 「已失效」标记行：保持勾选、不可再勾选、可移除", () => {
    const onChange = vi.fn();
    render(
      <OutputReferenceCheckboxList
        value={["ghost_ref"]}
        onChange={onChange}
        candidates={CANDIDATES}
      />,
    );

    // 失效行渲染：title 为引用表达式、含「已失效」标记
    const row = screen.getByTitle("ghost_ref");
    expect(within(row).getByText("已失效")).toBeTruthy();

    // 复选框保持勾选且禁用（不可再勾选）
    const checkbox = within(row).getByRole("checkbox") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    expect(checkbox.disabled).toBe(true);
    fireEvent.click(checkbox);
    expect(onChange).not.toHaveBeenCalled();

    // 「✕」按钮移除 → 全量数组过滤
    fireEvent.click(within(row).getByText("✕"));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("失效引用去重渲染：value 重复元素仅一行", () => {
    const onChange = vi.fn();
    render(
      <OutputReferenceCheckboxList
        value={["ghost_ref", "ghost_ref"]}
        onChange={onChange}
        candidates={CANDIDATES}
      />,
    );
    expect(screen.getAllByTitle("ghost_ref").length).toBe(1);
    fireEvent.click(within(screen.getByTitle("ghost_ref")).getByText("✕"));
    expect(onChange).toHaveBeenCalledWith([]);
  });
});

// ---------------------------------------------------------------------------
// ④ 依赖未满足占位分组
// ---------------------------------------------------------------------------

describe("④ 依赖未满足占位分组（INV-D19 确定性降级）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });
  afterEach(() => {
    cleanup();
  });

  it("deferredGroups 渲染灰显标题与依赖说明、复选框禁用、点击不触发 onChange", () => {
    const onChange = vi.fn();
    render(
      <OutputReferenceCheckboxList
        value={[]}
        onChange={onChange}
        candidates={CANDIDATES}
        deferredGroups={DEFERRED_GROUPS}
      />,
    );

    expect(screen.getByText("上游输入参数")).toBeTruthy();
    expect(screen.getByText("待 upstream_input_ref 落地后启用")).toBeTruthy();

    const deferredCheckbox = screen
      .getByText("待 upstream_input_ref 落地后启用")
      .closest("div")
      ?.querySelector('input[type="checkbox"]') as HTMLInputElement;
    expect(deferredCheckbox).not.toBeNull();
    expect(deferredCheckbox.disabled).toBe(true);
    fireEvent.click(deferredCheckbox);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("占位分组不阻塞保存：候选勾选回写不受影响", () => {
    const onChange = vi.fn();
    render(
      <OutputReferenceCheckboxList
        value={[]}
        onChange={onChange}
        candidates={CANDIDATES}
        deferredGroups={DEFERRED_GROUPS}
      />,
    );
    fireEvent.click(checkboxOfRow("情报分析（analysis_result）"));
    expect(onChange).toHaveBeenCalledWith(["analysis_result"]);
  });
});

// ---------------------------------------------------------------------------
// ⑤ 双主题取色
// ---------------------------------------------------------------------------

describe("⑤ 双主题取色（AC-3.7）", () => {
  afterEach(() => {
    cleanup();
  });

  it("浅色：样式含浅色提示色；深色：含 darkTokens.text.hint", () => {
    mockTheme = "light";
    const { container: lightContainer, unmount: unmountLight } = renderList();
    const lightCss = lightContainer.querySelector("style")?.textContent ?? "";
    expect(lightCss).toContain("#999");
    expect(lightCss).toContain(".wfp-orcl-row");
    unmountLight();

    mockTheme = "dark";
    const { container: darkContainer } = renderList();
    const darkCss = darkContainer.querySelector("style")?.textContent ?? "";
    expect(darkCss).toContain(darkTokens.text.hint);
    expect(darkCss).toContain(".wfp-orcl-row--invalid");
    expect(darkCss).toContain(".wfp-orcl-row--deferred");
  });
});
