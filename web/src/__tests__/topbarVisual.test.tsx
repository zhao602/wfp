// =============================================================================
// 顶栏视觉优化回归测试（src/__tests__/topbarVisual.test.tsx）
//
// 覆盖验收（对应 spec.md 5.1/5.2/5.3）：
//   1. 默认渲染结构（5.1.1.1a / 5.2.1.3a）
//   2. 按钮皮肤挂载（5.3.1.1a）
//   3. 点击行为不变（5.3.1.7a）
//   4. 错误态呈现与恢复（5.2.1.6a / 5.2.3.1 / 5.2.3.2）
//   5. 范例按钮统一风格与灰显（5.2.1.7a）
//   6. 范例按钮可点击加载（5.1.1.5a）
// 验证方式：V1 vitest 组件测试（jsdom）；浏览器目测项由人工执行
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import App from "../App";
import ExampleLibraryPanel from "../example-library/ExampleLibraryPanel";
import { orchestrationApiClient, ApiException } from "../api/orchestrationApiClient";
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

describe("顶栏默认渲染与皮肤挂载", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("默认渲染结构：header 存在、标题与按钮文本原样", () => {
    // 范例列表接口返回空列表，顶栏保持默认渲染
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    const { container } = render(<App />);
    const header = container.querySelector("header");

    // 顶栏存在，按钮位于顶栏内且右对齐元素文本不变
    expect(header).not.toBeNull();
    expect(header?.querySelector("h1")?.textContent).toBe("工作流编排平台");
    const firstButton = header?.querySelector("button");
    // 顶栏首个按钮为范例库折叠钮（新增的收起/展开出口）
    expect(firstButton?.className).toContain("wfp-example-toggle");
    // 管理后台入口：首个挂皮肤类的按钮，文本不变
    const adminButton = header?.querySelector("button.wfp-topbar-btn");
    expect(adminButton?.textContent).toBe("管理后台 →");

    // 标题语义保持 h1（spec 4.5.4）
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "工作流编排平台",
    );
  });

  it("按钮皮肤挂载：含皮肤类，注入块携带四态伪类规则与 120ms 过渡", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    const { container } = render(<App />);
    const styleNode = container.querySelector("header style");

    // <style> 节点随 header 挂载，且包含按压/焦点规则与短过渡声明
    expect(styleNode).not.toBeNull();
    const css = styleNode?.textContent ?? "";
    expect(css).toContain(".wfp-topbar-btn:active");
    expect(css).toContain(".wfp-topbar-btn:focus-visible");
    expect(css).toContain("120ms");

    // 管理后台入口按钮挂载了皮肤类
    const adminButton = container.querySelector("header button.wfp-topbar-btn");
    expect(adminButton).not.toBeNull();
    expect(adminButton?.textContent).toBe("管理后台 →");
  });

  it("点击行为不变：点击入口按钮后切换至管理后台视图", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    render(<App />);
    fireEvent.click(screen.getByText("管理后台 →"));

    // 视图切换：画布视图顶栏卸载（按钮消失），管理后台视图渲染
    // （管理后台默认面板自带 h1，须按可访问名过滤至后台标题）
    await waitFor(() => {
      expect(screen.queryByText("管理后台 →")).toBeNull();
      expect(screen.getByRole("heading", { level: 1, name: "管理后台" })).toHaveTextContent(
        "管理后台",
      );
    });
  });
});

describe("范例库状态区呈现", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("错误态呈现：警示样式展示完整错误、title 携带原文，恢复后回切列表", async () => {
    // 第一阶段：接口失败（HTTP 500）
    vi.spyOn(orchestrationApiClient, "listExamples").mockRejectedValue(
      // ApiException 签名为 (code, message, details, httpStatus)：消息须传第二参
      new ApiException("internal_error", "HTTP 500: internal error", {}, 500),
    );
    const { container } = render(
      <ExampleLibraryPanel
        onLoadExample={makeOnLoadExample()}
      />,
    );

    // 错误 span 原样渲染完整文本，title 携带完整内容（信息零丢失）
    const errorSpan = await screen.findByTitle("HTTP 500: internal error");
    expect(errorSpan.textContent).toBe("HTTP 500: internal error");
    expect(errorSpan).toHaveStyle({ color: "#d32f2f", maxWidth: "320px", textOverflow: "ellipsis" });

    // 状态区容器具备收缩约束（minWidth 0）——独立渲染时它就是首个子元素
    // （React 数字 0 不追加 px，jsdom 内联值为 "0"）
    const statusContainer = container.firstElementChild;
    expect(statusContainer).toHaveStyle({ minWidth: "0" });

    cleanup();

    // 第二阶段：后端恢复，错误提示移除并呈现范例列表
    vi.restoreAllMocks();
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [makeExample({ name: "顺序管道" })],
    });
    const second = render(
      <ExampleLibraryPanel
        onLoadExample={makeOnLoadExample()}
      />,
    );
    await waitFor(() => {
      expect(screen.queryByTitle("HTTP 500: internal error")).toBeNull();
      expect(screen.getByText("顺序管道")).toBeInTheDocument();
    });
    second.unmount();
  });

  it("范例按钮统一风格与灰显：双类挂载，不可用项 opacity 0.5 且 title 含原因", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [
        makeExample({ name: "可用范例" }),
        makeExample({
          name: "不可用范例",
          available: false,
          unavailable_reason: "缺少依赖",
        }),
      ],
    });

    render(
      <ExampleLibraryPanel
        onLoadExample={makeOnLoadExample()}
      />,
    );

    // 可用范例：皮肤类族挂载（基类 + 小号修饰类）
    const availableButton = await screen.findByText("可用范例");
    expect(availableButton.className).toBe("wfp-topbar-btn wfp-topbar-btn--sm");

    // 不可用范例：灰显 opacity 0.5 + not-allowed 光标 + title 含不可用原因
    const disabledButton = screen.getByText("不可用范例");
    expect(disabledButton).toHaveStyle({ opacity: "0.5", cursor: "not-allowed" });
    expect(disabledButton.getAttribute("title")).toContain("缺少依赖");
  });

  it("可点击加载：点击可用范例触发 onLoadExample 回调", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [makeExample({ name: "可用范例" })],
    });
    vi.spyOn(orchestrationApiClient, "loadExample").mockResolvedValue({
      spec_version: "1.0",
      definition_id: "def-ex-1",
      name: "范例定义",
      definition_version: 1,
      inputs: [],
      outputs: [],
      nodes: [],
      edges: [],
    });
    const onLoadExample = makeOnLoadExample();

    render(<ExampleLibraryPanel onLoadExample={onLoadExample} />);

    const exampleButton = await screen.findByText("可用范例");
    fireEvent.click(exampleButton);
    await waitFor(() => {
      expect(onLoadExample).toHaveBeenCalledTimes(1);
    });
  });
});