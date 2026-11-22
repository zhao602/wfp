// =============================================================================
// 节点面板卡片视觉优化回归测试（src/__tests__/paletteCardsVisual.test.tsx）
//
// 留待非沙箱环境执行（`npx vitest run src/__tests__/paletteCardsVisual.test.tsx`），
// 沙箱内不运行（esbuild 子进程被资源限制终止）。
//
// 覆盖验收（对应 spec.md 5.1/5.2/5.3，17 条 EARS 验收条件中的 jsdom 可断言子集）：
//   1. 默认渲染结构（八卡存在、label 原样、cursor grab）
//   2. 类型色条契约（borderLeftColor 与八组类型色逐一相等）
//   3. 皮肤类与注入块（wfp-palette-card 类、:hover 与 --dragging 规则）
//   4. 圆角与描边（6px 圆角 + 1px #d0d0d0 描边）
//   5. 拖拽进入与恢复（opacity 0.5 + --dragging 类 → dragend 归位）
//   6. 拖拽数据冻结（MIME 与 effectAllowed 不变）
//   7. 样式共存隔离（注入块不含 wfp-topbar- 类名）
//   8. llm/code 模板登记（NODE_TEMPLATES 含两类型、标签与类型色冻结，B1/CHG-F05）
//   9. llm/code 卡片渲染与拖拽数据（色条 borderLeftColor、MIME 携带类型）
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import NodePalette, { NODE_TEMPLATES } from "../canvas/NodePalette";

/** 八组类型色值（与 NODE_TEMPLATES 逐一对应，冻结契约；tool 已移除新建入口，merge_tool_into_agent_node DC-C/CHG-22） */
const TYPE_COLORS: Array<[string, string]> = [
  ["触发器", "#1976d2"],
  ["Agent", "#7b1fa2"],
  ["数据源", "#f57c00"],
  ["控制流", "#c62828"],
  ["子工作流", "#3f51b5"],
  ["动态注入", "#9c27b0"],
  ["数据转换", "#ff9800"],
  ["输出展示", "#00897b"],
];

/** 构造拖拽事件所需的 dataTransfer stub（jsdom 无原生 DataTransfer，业务函数体冻结不可改故 stub 必选） */
function makeDataTransferStub() {
  return {
    setData: vi.fn(),
    effectAllowed: "",
  } as unknown as DataTransfer;
}

describe("节点面板卡片视觉优化", () => {
  afterEach(() => {
    cleanup();
  });

  it("默认渲染结构：八张卡片存在、八类 label 原样、cursor grab", () => {
    const { container } = render(<NodePalette />);

    // 注入块为容器首个子节点（标题之前）
    const sidebar = container.firstElementChild as HTMLElement;
    expect(sidebar.querySelector("style")?.nextElementSibling?.textContent).toBe(
      "节点面板",
    );

    // 八类卡片逐一原样渲染，光标为 grab（spec 5.1.1.5a）
    for (const [label] of TYPE_COLORS) {
      const card = screen.getByText(label);
      expect(card).toHaveStyle({ cursor: "grab" });
      expect(card.tagName).toBe("DIV");
    }
  });

  it("类型色条契约：八张卡片 borderLeftColor 与八组类型色逐一相等", () => {
    render(<NodePalette />);

    // 类型色跨区域契约：面板卡片与画布节点同色（spec 4.5.2 八组色值逐一冻结）
    for (const [label, color] of TYPE_COLORS) {
      const card = screen.getByText(label);
      expect(card).toHaveStyle({ borderLeftColor: color });
    }
  });

  it("皮肤类与注入块：卡片含基类，注入块含 :hover 与 --dragging 规则且全为类选择器", () => {
    const { container } = render(<NodePalette />);

    const card = screen.getByText("触发器");
    expect(card.className).toContain("wfp-palette-card");
    expect(card.className).not.toContain("wfp-palette-card--dragging");

    // 注入块文本携带悬停与拖拽中态规则（spec 5.2.1.1a / 5.3.1.1a）
    const styleText = container.querySelector("style")?.textContent ?? "";
    expect(styleText).toContain(".wfp-palette-card:hover:not(.wfp-palette-card--dragging)");
    expect(styleText).toContain(".wfp-palette-card--dragging");
    expect(styleText).toContain("120ms");

    // 每条规则选择器以 . 开头（纯类选择器，spec 4.4.2 防外溢）；
    // 先剥离规则间 /* … */ 注释再切分（注释随深色作用域规则引入，属文本噪声非选择器）
    const selectors = styleText
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("}")
      .map((rule) => rule.trim().split("{")[0]?.trim())
      .filter(Boolean);
    for (const selector of selectors) {
      expect(selector.startsWith(".")).toBe(true);
    }
  });

  it("圆角与描边：注入块文本含 6px 圆角与 1px #d0d0d0 中性描边", () => {
    const { container } = render(<NodePalette />);

    const styleText = container.querySelector("style")?.textContent ?? "";
    // 默认态由类基类承担：1px 中性灰描边 + 6px 圆角（spec 5.1.1.2a / 5.1.1.4a）
    expect(styleText).toContain("border-radius: 6px");
    expect(styleText).toContain("border: 1px solid #d0d0d0");

    // 内联对象不再包含 2px 彩色全包围边框与 8px 圆角（防内联残留覆盖类值）
    const card = screen.getByText("触发器");
    expect(card.style.border).toBe("");
    expect(card.style.borderRadius).toBe("");
  });

  it("拖拽进入与恢复：dragStart 呈现 0.5 透明度与 --dragging 类，dragEnd 单点归位", () => {
    render(<NodePalette />);

    const card = screen.getByText("触发器");

    // 拖拽开始：源卡片 opacity 0.5 + 拖拽中类挂载（spec 5.3.1.1a）
    fireEvent.dragStart(card, { dataTransfer: makeDataTransferStub() });
    expect(card).toHaveStyle({ opacity: "0.5" });
    expect(card.className).toContain("wfp-palette-card--dragging");

    // 拖拽结束：无条件归位，opacity 1 + 类移除（drop 成功/无效释放/Esc 三路径均触发 dragend）
    fireEvent.dragEnd(card);
    expect(card).toHaveStyle({ opacity: "1" });
    expect(card.className).not.toContain("wfp-palette-card--dragging");
  });

  it("拖拽数据冻结：MIME 与 effectAllowed 与优化前一致", () => {
    render(<NodePalette />);

    const card = screen.getByText("触发器");
    const dataTransfer = makeDataTransferStub();
    const setDataSpy = vi.spyOn(dataTransfer, "setData");

    fireEvent.dragStart(card, { dataTransfer });

    // 业务数据写入零改动（spec 5.3.1.2a 冻结禁止项）
    expect(setDataSpy).toHaveBeenCalledWith("application/node-type", "trigger");
    expect(dataTransfer.effectAllowed).toBe("move");
  });

  it("样式共存隔离：注入块不含顶栏 wfp-topbar- 类名", () => {
    const { container } = render(<NodePalette />);

    // 与顶栏注入块前缀隔离共存、互不匹配（spec 5.1.3.3）
    const styleText = container.querySelector("style")?.textContent ?? "";
    expect(styleText).not.toContain("wfp-topbar-");
  });

  it("llm/code 模板登记（B1/CHG-F05）：NODE_TEMPLATES 含两类型，标签与类型色冻结", () => {
    // 程序化断言模板表含 llm/code 两键（design 附录 A.4 表 B B1——渲染绿不等于登记绿）
    const llm = NODE_TEMPLATES.find((t) => t.type === "llm");
    expect(llm).toEqual({ type: "llm", label: "大模型", color: "#c2185b" });
    const code = NODE_TEMPLATES.find((t) => t.type === "code");
    expect(code).toEqual({ type: "code", label: "代码", color: "#455a64" });
    // 面板共十一类模板（tool 已移除新建入口，DC-C）
    expect(NODE_TEMPLATES).toHaveLength(11);
    expect(NODE_TEMPLATES.some((t) => t.type === "tool")).toBe(false);
  });

  it("llm/code 卡片渲染与拖拽数据：色条与类型色逐一相等，MIME 携带节点类型", () => {
    render(<NodePalette />);

    // 两卡以浅色类型色渲染左侧色条（CHG-F05 冻结契约）
    const llmCard = screen.getByText("大模型");
    expect(llmCard).toHaveStyle({ borderLeftColor: "#c2185b", cursor: "grab" });
    const codeCard = screen.getByText("代码");
    expect(codeCard).toHaveStyle({ borderLeftColor: "#455a64", cursor: "grab" });

    // 拖拽数据与既有八卡同构：MIME 携带节点类型（application/node-type = llm/code）
    const llmDataTransfer = makeDataTransferStub();
    fireEvent.dragStart(llmCard, { dataTransfer: llmDataTransfer });
    expect(llmDataTransfer.setData).toHaveBeenCalledWith("application/node-type", "llm");

    const codeDataTransfer = makeDataTransferStub();
    fireEvent.dragStart(codeCard, { dataTransfer: codeDataTransfer });
    expect(codeDataTransfer.setData).toHaveBeenCalledWith("application/node-type", "code");
  });
});