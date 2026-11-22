// =============================================================================
// 条件配置区组件测试（canvas/__tests__/ConditionGroupEditor.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/ConditionGroupEditor.test.tsx
//
// 覆盖（expand_data_transform_ops DC-09.3 / FR-23 / AC-20）：
//   CGE-P1~P3 纯函数：行完整性判定 / 值字面量化（数值直通、字符串引号转义）/
//            条件组编译（跳过不完整行、AND/OR 拼接、空组空串）
//   CGE-01 初始渲染：零条件行、零原生 select、AND 选中 OR 未选、挂载零发射
//   CGE-02 单条件编译：添加空行与半填行均不发射；字段+值完整后发射 "a == 1"
//   CGE-03 双条件 AND→OR：完整两行按组合逻辑发射，切换 OR 重编译
//   CGE-04 字符串值引号：非数值值输出双引号字面量（a != "x"）
//   CGE-05 草稿保护：仅字段或空白值不发射（避免覆盖既有表达式）
//   CGE-06 删行清空：删除唯一完整行 → 显式发射空串
//   CGE-07 运算符切换重编译：六运算符渲染、aria-pressed 随切换、表达式重发射
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import ConditionGroupEditor, {
  CONDITION_OPERATORS,
  CONDITION_COMBINATORS,
  EMPTY_CONDITION_GROUP,
  isConditionRowComplete,
  formatConditionValue,
  compileConditionGroup,
} from "../ConditionGroupEditor";
import type { ConditionRow, ConditionGroup } from "../ConditionGroupEditor";

// 主题 mock：保留真实 darkTokens 供 <style> 插值（atomic/hints 同款约定）
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

/** 构造条件行 */
function makeRow(field: string, operator: ConditionRow["operator"], value: string): ConditionRow {
  return { field, operator, value };
}

/** 构造条件组 */
function makeGroup(rows: ConditionRow[], combinator: ConditionGroup["combinator"]): ConditionGroup {
  return { rows, combinator };
}

/** 渲染组件并返回容器与发射 spy */
function renderEditor() {
  const onCompile = vi.fn();
  const view = render(<ConditionGroupEditor onCompile={onCompile} />);
  return { onCompile, container: view.container, unmount: () => view.unmount() };
}

/** 第 index 条件行内的字段/值输入框 */
function getRowInputs(container: HTMLElement, index: number): HTMLInputElement[] {
  const row = container.querySelectorAll(".wfp-cge-row")[index];
  if (!row) {
    throw new Error(`未找到条件行 ${index}`);
  }
  const inputs = Array.from(row.querySelectorAll("input.wfp-ncp-input"));
  if (inputs.length !== 2 || !(inputs[0] instanceof HTMLInputElement) || !(inputs[1] instanceof HTMLInputElement)) {
    throw new Error("条件行输入框结构异常");
  }
  return [inputs[0], inputs[1]];
}

/** 行内指定运算符按钮 */
function getOperatorButton(container: HTMLElement, rowIndex: number, operator: string): HTMLButtonElement {
  const row = container.querySelectorAll(".wfp-cge-row")[rowIndex];
  const button = Array.from(row?.querySelectorAll(".wfp-cge-op") ?? []).find(
    (btn) => (btn.textContent ?? "").trim() === operator,
  );
  if (!(button instanceof HTMLButtonElement)) {
    throw new Error(`未找到运算符按钮 ${operator}`);
  }
  return button;
}

/** 指定组合逻辑（AND/OR）按钮 */
function getCombinatorButton(container: HTMLElement, text: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll(".wfp-cge-combinator")).find(
    (btn) => (btn.textContent ?? "").trim() === text,
  );
  if (!(button instanceof HTMLButtonElement)) {
    throw new Error(`未找到组合逻辑按钮 ${text}`);
  }
  return button;
}

/** 已发射表达式序列 */
function emittedExpressions(onCompile: ReturnType<typeof vi.fn>): string[] {
  return onCompile.mock.calls.map((call) => call[0]);
}

// ---------------------------------------------------------------------------
// 纯函数
// ---------------------------------------------------------------------------

describe("ConditionGroupEditor 纯函数（DC-09.3）", () => {
  it("CGE-P1 行完整性判定：字段与值均非空白才算完整", () => {
    expect(isConditionRowComplete(makeRow("a", "==", "1"))).toBe(true);
    expect(isConditionRowComplete(makeRow(" a ", "==", " 1 "))).toBe(true);
    expect(isConditionRowComplete(makeRow("", "==", "1"))).toBe(false);
    expect(isConditionRowComplete(makeRow("  ", "==", "1"))).toBe(false);
    expect(isConditionRowComplete(makeRow("a", "==", ""))).toBe(false);
    expect(isConditionRowComplete(makeRow("a", "==", "   "))).toBe(false);
  });

  it("CGE-P2 值字面量化：数值直通、其余双引号字符串（含转义）", () => {
    expect(formatConditionValue("42")).toBe("42");
    expect(formatConditionValue("-3")).toBe("-3");
    expect(formatConditionValue("3.14")).toBe("3.14");
    expect(formatConditionValue(" 12 ")).toBe("12");
    expect(formatConditionValue("abc")).toBe('"abc"');
    expect(formatConditionValue("12a")).toBe('"12a"');
    expect(formatConditionValue("1e3")).toBe('"1e3"');
    expect(formatConditionValue('he"llo')).toBe('"he\\"llo"');
  });

  it("CGE-P3 条件组编译：跳过不完整行、AND/OR 拼接、空组空串", () => {
    expect(compileConditionGroup(EMPTY_CONDITION_GROUP)).toBe("");
    expect(compileConditionGroup(makeGroup([], "or"))).toBe("");
    expect(
      compileConditionGroup(
        makeGroup([makeRow("  ", ">", "5"), makeRow("a", "==", "1")], "and"),
      ),
    ).toBe("a == 1");
    expect(
      compileConditionGroup(
        makeGroup([makeRow("a", "==", "1"), makeRow("b", ">=", "2")], "and"),
      ),
    ).toBe("a == 1 and b >= 2");
    expect(
      compileConditionGroup(
        makeGroup([makeRow("a", "==", "1"), makeRow("b", ">=", "2")], "or"),
      ),
    ).toBe("a == 1 or b >= 2");
    expect(compileConditionGroup(makeGroup([makeRow("name", "==", "abc")], "and"))).toBe(
      'name == "abc"',
    );
  });

  it("CGE-P4 运算符/组合逻辑全集口径", () => {
    expect(CONDITION_OPERATORS).toEqual(["==", "!=", ">", ">=", "<", "<="]);
    expect(CONDITION_COMBINATORS).toEqual(["and", "or"]);
  });
});

// ---------------------------------------------------------------------------
// 组件交互
// ---------------------------------------------------------------------------

describe("ConditionGroupEditor 组件（DC-09.3）", () => {
  it("CGE-01 初始渲染：零条件行、零 select、AND 选中、挂载零发射", () => {
    const { onCompile, container } = renderEditor();
    expect(container.querySelectorAll(".wfp-cge-row").length).toBe(0);
    expect(container.querySelectorAll("select").length).toBe(0);
    expect(getCombinatorButton(container, "AND").getAttribute("aria-pressed")).toBe("true");
    expect(getCombinatorButton(container, "OR").getAttribute("aria-pressed")).toBe("false");
    expect(
      Array.from(container.querySelectorAll(".wfp-cge-add")).some(
        (btn) => (btn.textContent ?? "").includes("添加条件"),
      ),
    ).toBe(true);
    expect(onCompile).not.toHaveBeenCalled();
  });

  it("CGE-02 单条件编译：空行与半填行不发射，完整后发射", () => {
    const { onCompile, container } = renderEditor();
    fireEvent.click(container.querySelector(".wfp-cge-add") as HTMLButtonElement);
    expect(onCompile).not.toHaveBeenCalled();
    const [fieldInput, valueInput] = getRowInputs(container, 0);
    fireEvent.change(fieldInput, { target: { value: "a" } });
    expect(onCompile).not.toHaveBeenCalled();
    fireEvent.change(valueInput, { target: { value: "1" } });
    expect(onCompile).toHaveBeenCalledTimes(1);
    expect(onCompile).toHaveBeenLastCalledWith("a == 1");
  });

  it("CGE-03 双条件 AND→OR：完整两行按组合逻辑编译，切换 OR 重发射", () => {
    const { onCompile, container } = renderEditor();
    // 行 1：a == 1
    fireEvent.click(container.querySelector(".wfp-cge-add") as HTMLButtonElement);
    const [row1Field, row1Value] = getRowInputs(container, 0);
    fireEvent.change(row1Field, { target: { value: "a" } });
    fireEvent.change(row1Value, { target: { value: "1" } });
    // 行 2：b >= 2
    fireEvent.click(container.querySelector(".wfp-cge-add") as HTMLButtonElement);
    const [row2Field, row2Value] = getRowInputs(container, 1);
    fireEvent.change(row2Field, { target: { value: "b" } });
    fireEvent.click(getOperatorButton(container, 1, ">="));
    fireEvent.change(row2Value, { target: { value: "2" } });
    expect(emittedExpressions(onCompile)).toContain("a == 1 and b >= 2");
    // 切换 OR
    fireEvent.click(getCombinatorButton(container, "OR"));
    expect(onCompile).toHaveBeenLastCalledWith("a == 1 or b >= 2");
    expect(getCombinatorButton(container, "OR").getAttribute("aria-pressed")).toBe("true");
    expect(getCombinatorButton(container, "AND").getAttribute("aria-pressed")).toBe("false");
  });

  it("CGE-04 字符串值引号：非数值值输出双引号字面量", () => {
    const { onCompile, container } = renderEditor();
    fireEvent.click(container.querySelector(".wfp-cge-add") as HTMLButtonElement);
    const [fieldInput, valueInput] = getRowInputs(container, 0);
    fireEvent.change(fieldInput, { target: { value: "a" } });
    fireEvent.click(getOperatorButton(container, 0, "!="));
    fireEvent.change(valueInput, { target: { value: "x" } });
    expect(onCompile).toHaveBeenLastCalledWith('a != "x"');
  });

  it("CGE-05 草稿保护：仅字段或空白值不发射，补全后才发射", () => {
    const { onCompile, container } = renderEditor();
    fireEvent.click(container.querySelector(".wfp-cge-add") as HTMLButtonElement);
    const [fieldInput, valueInput] = getRowInputs(container, 0);
    fireEvent.change(fieldInput, { target: { value: "draft" } });
    expect(onCompile).not.toHaveBeenCalled();
    fireEvent.change(valueInput, { target: { value: "   " } });
    expect(onCompile).not.toHaveBeenCalled();
    fireEvent.change(valueInput, { target: { value: "1" } });
    expect(onCompile).toHaveBeenCalledTimes(1);
    expect(onCompile).toHaveBeenLastCalledWith("draft == 1");
  });

  it("CGE-06 删行清空：删除唯一完整行显式发射空串", () => {
    const { onCompile, container } = renderEditor();
    fireEvent.click(container.querySelector(".wfp-cge-add") as HTMLButtonElement);
    const [fieldInput, valueInput] = getRowInputs(container, 0);
    fireEvent.change(fieldInput, { target: { value: "a" } });
    fireEvent.change(valueInput, { target: { value: "1" } });
    expect(onCompile).toHaveBeenLastCalledWith("a == 1");
    fireEvent.click(container.querySelector(".wfp-cge-remove") as HTMLButtonElement);
    expect(onCompile).toHaveBeenLastCalledWith("");
    expect(container.querySelectorAll(".wfp-cge-row").length).toBe(0);
  });

  it("CGE-07 运算符切换重编译：六运算符渲染且 aria-pressed 随切换", () => {
    const { onCompile, container } = renderEditor();
    fireEvent.click(container.querySelector(".wfp-cge-add") as HTMLButtonElement);
    const row = container.querySelectorAll(".wfp-cge-row")[0];
    const opButtons = Array.from(row.querySelectorAll(".wfp-cge-op"));
    expect(opButtons.map((btn) => (btn.textContent ?? "").trim())).toEqual([
      "==",
      "!=",
      ">",
      ">=",
      "<",
      "<=",
    ]);
    const [fieldInput, valueInput] = getRowInputs(container, 0);
    fireEvent.change(fieldInput, { target: { value: "a" } });
    fireEvent.change(valueInput, { target: { value: "1" } });
    expect(onCompile).toHaveBeenLastCalledWith("a == 1");
    fireEvent.click(getOperatorButton(container, 0, ">"));
    expect(onCompile).toHaveBeenLastCalledWith("a > 1");
    expect(getOperatorButton(container, 0, "==").getAttribute("aria-pressed")).toBe("false");
    expect(getOperatorButton(container, 0, ">").getAttribute("aria-pressed")).toBe("true");
  });
});
