// =============================================================================
// 范例库面板窄窗换行回归测试（src/__tests__/exampleLibraryWrap.test.tsx）
//
// 覆盖验收（对应 spec.md FR-01/FR-03/FR-06、AC-05/AC-10）：
//   1. 面板根容器开启 flexWrap: wrap（CHG-01）
//   2. 根容器 minWidth: 0 契约保持（INV-01）
//   3. 范例按钮 whiteSpace: nowrap + flexShrink: 0（CHG-02）
//   4. 既有取值守卫：gap/alignItems 不被破坏（INV-10）
// 验证方式：V1 vitest 组件测试（jsdom）；留待非沙箱环境执行
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import ExampleLibraryPanel from "../example-library/ExampleLibraryPanel";
import { orchestrationApiClient } from "../api/orchestrationApiClient";
import { yamlToCanvas } from "../workflow-definition/yamlToCanvasImporter";
import type { ExampleEntry } from "../api/apiTypes";

/** 构造与 App 内行为一致的 onLoadExample 回调 mock（返回类型精确匹配 ImportResult） */
function makeOnLoadExample() {
  return vi.fn((yamlText: string) => yamlToCanvas(yamlText));
}

// jsdom 无 ResizeObserver，reactflow 画布在测试环境渲染需要桩实现（仅测试环境补丁）
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  },
);

// 后端返回 200 空对象，避免渲染全树时子面板的真实请求外溢
vi.stubGlobal(
  "fetch",
  vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200 })),
);

/** 构造范例条目数据 */
function makeExample(overrides: Partial<ExampleEntry>): ExampleEntry {
  return {
    example_id: "ex-1",
    name: "范例",
    yaml_path: "examples/ex.yaml",
    available: true,
    missing_dependencies: [],
    ...overrides,
  };
}

describe("范例库面板窄窗换行", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("根容器开启折行：flexWrap 为 wrap 且 minWidth 契约保持", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [makeExample({ name: "顺序管道" })],
    });

    const { container } = render(
      <ExampleLibraryPanel onLoadExample={makeOnLoadExample()} />,
    );

    // 等待范例按钮渲染，确认面板已挂载
    await screen.findByText("顺序管道");

    // 根容器即面板首个子元素
    const root = container.firstElementChild;
    expect(root).not.toBeNull();
    // CHG-01：开启窄窗折行
    expect(root).toHaveStyle({ flexWrap: "wrap" });
    // INV-01：minWidth 契约保持（既有 topbarVisual.test 同样依赖此值）
    // （React 数字 0 不追加 px，jsdom 内联值为 "0"）
    expect(root).toHaveStyle({ minWidth: "0" });
    // INV-10：既有取值守卫，防止后续变更破坏宽屏单行语义
    expect(root).toHaveStyle({ gap: "8px", alignItems: "center" });
  });

  it("范例按钮以整块换行：whiteSpace 为 nowrap 且 flexShrink 为 0", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [
        makeExample({ example_id: "ex-a", name: "范例A" }),
        makeExample({ example_id: "ex-b", name: "范例B" }),
      ],
    });

    render(<ExampleLibraryPanel onLoadExample={makeOnLoadExample()} />);

    const buttonA = await screen.findByText("范例A");
    const buttonB = await screen.findByText("范例B");

    // CHG-02：按钮文字不折行、按钮不被压缩，以完整单元参与换行
    expect(buttonA).toHaveStyle({ whiteSpace: "nowrap", flexShrink: "0" });
    expect(buttonB).toHaveStyle({ whiteSpace: "nowrap", flexShrink: "0" });

    // INV-03：按钮类名契约保持
    expect(buttonA.className).toBe("wfp-topbar-btn wfp-topbar-btn--sm");
    expect(buttonB.className).toBe("wfp-topbar-btn wfp-topbar-btn--sm");
  });
});