// =============================================================================
// 大模型/代码节点画布视图渲染测试（canvas/__tests__/LlmCodeNodeViews.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/LlmCodeNodeViews.test.tsx
//
// 覆盖用例（llm_code_nodes_v2 CHG-F08 / DC-13-3 B8，design 附录 A.4 表 B B2/B8）：
//   NV-01 LlmNodeView 浅色实例化：填充 #fce4ec、描边 #c2185b、类型文案「大模型」
//   NV-02 CodeNodeView 浅色实例化：填充 #eceff1、描边 #455a64、类型文案「代码」
//   NV-03 深色模式两视图：darkTokens.typeFill/typeBorder 含 llm/code 两键且被视图引用
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import LlmNodeView from "../nodes/LlmNodeView";
import CodeNodeView from "../nodes/CodeNodeView";
import { darkTokens } from "../../theme/themeMode";

// 主题 mock：保留 darkTokens 实物（深色断言取真实令牌值），仅覆写 useThemeMode
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

// reactflow Handle 依赖画布 Store 上下文（单元环境无 ReactFlow Provider）：
// 桩为空组件；Position 等枚举保留实物，浅色/深色双分支断言不受影响
vi.mock("reactflow", async (importOriginal) => {
  const actual = await importOriginal<typeof import("reactflow")>();
  return { ...actual, Handle: () => null };
});

describe("llm/code 画布视图渲染（CHG-F08/DC-13-3 B8）", () => {
  beforeEach(() => {
    mockTheme = "light";
    cleanup();
  });

  it("NV-01 LlmNodeView 浅色实例化：填充 #fce4ec、描边 #c2185b、类型文案「大模型」", () => {
    const { container } = render(<LlmNodeView data={{ name: "大模型节点" }} />);
    const node = container.querySelector('[data-testid="node-shell"]') as HTMLElement;
    expect(node).not.toBeNull();
    // 节点名与类型文案渲染（与既有 10 个 NodeView 同构）
    expect(screen.getByText("大模型节点")).toBeDefined();
    expect(screen.getByText("大模型")).toBeDefined();
    // 浅色身份色三处同源：填充层 = 同色相浅底、描边层 = 面板色条/身份色（#c2185b）
    const fill = node.querySelector('[data-testid="node-fill"]') as HTMLElement;
    const outline = node.querySelector('[data-testid="node-outline"]') as HTMLElement;
    expect(fill).toHaveStyle({ background: "#fce4ec" });
    expect(outline).toHaveStyle({ background: "#c2185b" });
  });

  it("NV-02 CodeNodeView 浅色实例化：填充 #eceff1、描边 #455a64、类型文案「代码」", () => {
    const { container } = render(<CodeNodeView data={{ name: "代码节点" }} />);
    const node = container.querySelector('[data-testid="node-shell"]') as HTMLElement;
    expect(node).not.toBeNull();
    expect(screen.getByText("代码节点")).toBeDefined();
    expect(screen.getByText("代码")).toBeDefined();
    const fill = node.querySelector('[data-testid="node-fill"]') as HTMLElement;
    const outline = node.querySelector('[data-testid="node-outline"]') as HTMLElement;
    expect(fill).toHaveStyle({ background: "#eceff1" });
    expect(outline).toHaveStyle({ background: "#455a64" });
  });

  it("NV-03 深色模式：typeFill/typeBorder 含 llm/code 两键且视图取值一致", () => {
    mockTheme = "dark";
    // 深色令牌两键存在性（7.6 验收：Record 含 llm/code 两键，机械判据）
    expect(darkTokens.typeFill.llm).toBe("#4a1a30");
    expect(darkTokens.typeFill.code).toBe("#1f2e36");
    expect(darkTokens.typeBorder.llm).toBe("#ec407a");
    expect(darkTokens.typeBorder.code).toBe("#78909c");

    // 大模型视图深色分支：取 darkTokens.typeFill.llm / typeBorder.llm
    const llmNode = render(<LlmNodeView data={{ name: "大模型节点" }} />).container.querySelector(
      '[data-testid="node-shell"]',
    ) as HTMLElement;
    expect(
      llmNode.querySelector('[data-testid="node-fill"]'),
    ).toHaveStyle({ background: darkTokens.typeFill.llm });
    expect(
      llmNode.querySelector('[data-testid="node-outline"]'),
    ).toHaveStyle({ background: darkTokens.typeBorder.llm });

    cleanup();
    // 代码视图深色分支：取 darkTokens.typeFill.code / typeBorder.code
    const codeNode = render(<CodeNodeView data={{ name: "代码节点" }} />).container.querySelector(
      '[data-testid="node-shell"]',
    ) as HTMLElement;
    expect(
      codeNode.querySelector('[data-testid="node-fill"]'),
    ).toHaveStyle({ background: darkTokens.typeFill.code });
    expect(
      codeNode.querySelector('[data-testid="node-outline"]'),
    ).toHaveStyle({ background: darkTokens.typeBorder.code });
  });
});
