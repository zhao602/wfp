// =============================================================================
// 范例库折叠交互测试（example-library/__tests__/ExampleLibraryPanel.collapse.test.tsx）
//
// 覆盖用例：
//   TC-CL-01 默认收起：aria-expanded=false、展开体 data-open=false、折叠钮可辨识
//   TC-CL-02 点击折叠钮展开：aria-expanded=true、展开体 data-open=true
//   TC-CL-03 再次点击收起
//   TC-CL-04 展开态按 Esc 收起并把焦点交还折叠钮
//   TC-CL-05 展开态点击面板外收起
//   TC-CL-06 收起态下加载失败的错误反馈仍可见（不因收起而丢失）
//   TC-CL-07 折叠钮 aria-controls 指向存在的展开体
//
// 说明：展开/收起以 data-open 与 aria-expanded 双通道断言。
// 显隐由 data-open 驱动 CSS（display），jsdom 对样式表计算不完整，
// 故不使用 toBeVisible，改用"状态属性 + DOM 契约"这一类稳定断言。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import ExampleLibraryPanel from "../ExampleLibraryPanel";
import { orchestrationApiClient } from "../../api/orchestrationApiClient";
import type { ExampleEntry } from "../../api/apiTypes";
import type { WorkflowCanvasState } from "../../canvas/canvasTypes";
import type { ImportResult } from "../../workflow-definition/yamlToCanvasImporter";
import type { WorkflowDefinition } from "../../workflow-definition/definitionTypes";

/** 两个范例条目：折叠态计数徽标应为 2 */
const exampleEntries: ExampleEntry[] = [
  {
    example_id: "ex-1",
    name: "顺序管道",
    yaml_path: "examples/a.yaml",
    available: true,
    missing_dependencies: [],
  },
  {
    example_id: "ex-2",
    name: "监督者路由",
    yaml_path: "examples/b.yaml",
    available: false,
    unavailable_reason: "缺少 LLM 配置",
    missing_dependencies: ["LLM_PRIMARY_API_KEY"],
  },
];

/** 范例定义夹具：handleLoad 仅 JSON.stringify 透传，内容不影响断言 */
const exampleDefinition: WorkflowDefinition = {
  spec_version: "1.0",
  definition_id: "def-ex-1",
  name: "范例定义",
  definition_version: 1,
  inputs: [],
  outputs: [],
  nodes: [],
  edges: [],
};

const okResult: ImportResult = {
  ok: true,
  canvasState: { nodes: [], edges: [] } as WorkflowCanvasState,
};

const failResult: ImportResult = {
  ok: false,
  error: "范例定义中节点或连线集合为空，已拒绝导入",
};

/** 渲染面板并等待范例列表拉取完成 */
async function renderPanel(result: ImportResult = okResult) {
  const onLoadExample = vi.fn((_yamlText: string) => result);
  const utils = render(<ExampleLibraryPanel onLoadExample={onLoadExample} />);
  await waitFor(() => {
    expect(orchestrationApiClient.listExamples).toHaveBeenCalled();
  });
  return { ...utils, onLoadExample };
}

/** 折叠钮（无 aria-label，可访问名即可见文案「范例库」；计数徽标 aria-hidden 不参与命名） */
function getToggle(): HTMLElement {
  return screen.getByRole("button", { name: "范例库" });
}

/** 展开体容器（折叠钮 aria-controls 的指向目标） */
function getBody(container: HTMLElement): HTMLElement {
  const body = container.querySelector<HTMLElement>(".wfp-example-body");
  if (!body) {
    throw new Error("未找到展开体容器");
  }
  return body;
}

describe("范例库折叠交互", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // TC-CL-01：默认收起，节省顶栏空间；收起态用计数徽标承载"有多少个范例"
  it("TC-CL-01 默认收起：aria-expanded=false 且展开体 data-open=false", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: exampleEntries,
    });
    const { container } = await renderPanel();

    const toggle = getToggle();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("title")).toBe("展开范例库");
    expect(getBody(container).getAttribute("data-open")).toBe("false");

    // 收起态仍可辨识数量（徽标 aria-hidden，不污染可访问名）
    const badge = toggle.querySelector(".wfp-example-count");
    expect(badge?.textContent).toBe("2");
  });

  // TC-CL-02：点击展开 → 全部范例可见可点
  it("TC-CL-02 点击折叠钮展开，范例按钮可加载", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: exampleEntries,
    });
    vi.spyOn(orchestrationApiClient, "loadExample").mockResolvedValue(exampleDefinition);
    const { container, onLoadExample } = await renderPanel();

    fireEvent.click(getToggle());

    expect(getToggle().getAttribute("aria-expanded")).toBe("true");
    expect(getToggle().getAttribute("title")).toBe("收起范例库");
    expect(getBody(container).getAttribute("data-open")).toBe("true");

    // 展开后按钮维持既有胶囊样式与类名
    const button = screen.getByText("顺序管道");
    expect(button.className).toBe("wfp-topbar-btn wfp-topbar-btn--sm");

    fireEvent.click(button);
    await waitFor(() => expect(onLoadExample).toHaveBeenCalledTimes(1));
  });

  // TC-CL-03：再次点击收起（幂等往返，状态不残留）
  it("TC-CL-03 再次点击折叠钮收起", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: exampleEntries,
    });
    const { container } = await renderPanel();

    fireEvent.click(getToggle());
    expect(getBody(container).getAttribute("data-open")).toBe("true");

    fireEvent.click(getToggle());
    expect(getToggle().getAttribute("aria-expanded")).toBe("false");
    expect(getBody(container).getAttribute("data-open")).toBe("false");
  });

  // TC-CL-04：Esc 收起 + 焦点回到折叠钮（键盘不丢失位置）
  it("TC-CL-04 展开态按 Esc 收起并把焦点交还折叠钮", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: exampleEntries,
    });
    const { container } = await renderPanel();

    fireEvent.click(getToggle());
    fireEvent.keyDown(document, { key: "Escape" });

    expect(getBody(container).getAttribute("data-open")).toBe("false");
    expect(document.activeElement).toBe(getToggle());
  });

  // TC-CL-05：点击面板外收起（页面其余区域点击即离开）
  it("TC-CL-05 展开态点击面板外区域收起", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: exampleEntries,
    });
    const { container } = await renderPanel();

    fireEvent.click(getToggle());
    expect(getBody(container).getAttribute("data-open")).toBe("true");

    fireEvent.mouseDown(document.body);
    expect(getBody(container).getAttribute("data-open")).toBe("false");
  });

  // TC-CL-06：收起态保留反馈（错误提示不受折叠影响）
  it("TC-CL-06 收起后加载失败的错误提示仍可见", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: exampleEntries,
    });
    vi.spyOn(orchestrationApiClient, "loadExample").mockResolvedValue(exampleDefinition);
    await renderPanel(failResult);

    fireEvent.click(getToggle());
    fireEvent.click(screen.getByText("顺序管道"));
    expect(await screen.findByText(failResult.error)).toBeDefined();

    // 收起面板：错误提示位于展开体之外，必须继续可见
    fireEvent.click(getToggle());
    expect(getToggle().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(failResult.error)).not.toBeNull();
  });

  // TC-CL-07：可访问性关联完整（aria-controls 指向真实容器）
  it("TC-CL-07 折叠钮 aria-controls 指向存在的展开体", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: exampleEntries,
    });
    const { container } = await renderPanel();

    const controls = getToggle().getAttribute("aria-controls");
    expect(controls).toBeTruthy();
    const body = container.querySelector(`#${controls}`);
    expect(body).not.toBeNull();
    expect(body).toBe(getBody(container));
  });
});
