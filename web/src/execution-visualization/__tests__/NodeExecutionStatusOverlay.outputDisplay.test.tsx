// =============================================================================
// 输出展示节点执行叠加层事件展示测试
//
// 留待非沙箱环境执行：
//   npx vitest run src/execution-visualization/__tests__/NodeExecutionStatusOverlay.outputDisplay.test.tsx
//
// 覆盖 design.md §4.2 用例组（叠加层事件展示部分）：
//   OE-01 NODE_COMPLETED 含 display_content → 展示区呈现标题与内容（spec 5.3.1.2a）
//   OE-02 JSON 内容格式化展示（spec 5.3.1.3a）
//   OE-03 payload 无 display_content → 展示"（无输出）"占位（spec 5.3.1.4a）
//   OE-04 既有节点空 payload → 输出展示区不出现条目（INV-3/INV-14）
//   OE-05 深色主题 darkTokens 取色（spec 5.3.1.5a/INV-12）
//   OE-06 超长 JSON → 容器出现滚动条（spec 5.3.1.3b）
//
// 追加覆盖（optimize_output_display_node CHG-11，design §2.6.2/§4.2）：
//   UT-12 output_mode 不含 file → 无下载入口；含 file → 呈现下载按钮，
//        点击产出 Blob 且文件名含节点标识（mock URL.createObjectURL / anchor.click）
//   UT-13 文件内容与 display_content 序列化往返一致
//   UT-19 按 output_mode 决定渲染条目（含 screen 渲染 / 不含不渲染），
//        display_content 判定键不变
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import type { ExecutionEvent } from "../../api/apiTypes";
import type { WorkflowCanvasState } from "../../canvas/canvasTypes";

// 主题 mock
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

// SSE 客户端 mock：捕获 onEvent 回调供测试驱动
let mockOnEvent: ((event: ExecutionEvent) => void) | null = null;

vi.mock("../ExecutionEventStreamClient", () => ({
  ExecutionEventStreamClient: vi.fn().mockImplementation((config: {
    onEvent: (e: ExecutionEvent) => void;
    onRetryExhausted: () => void;
  }) => {
    mockOnEvent = config.onEvent;
    return {
      connect: vi.fn(),
      disconnect: vi.fn(),
    };
  }),
}));

// API 客户端 mock
vi.mock("../../api/orchestrationApiClient", () => ({
  orchestrationApiClient: {
    getInstance: vi.fn().mockResolvedValue({ status: "running" }),
  },
  getTenantId: vi.fn().mockReturnValue("tenant-test"),
  ApiException: class ApiException extends Error {},
}));

/** 构造最小画布状态 */
function makeCanvasState(): WorkflowCanvasState {
  return {
    nodes: [
      {
        id: "node-output-1",
        type: "output_display",
        position: { x: 0, y: 0 },
        data: {
          node_id: "node-output-1",
          name: "输出展示",
          node_type: "output_display",
          configuration: { node_type: "output_display" },
        },
      },
    ],
    edges: [],
  };
}

/** 构造 NODE_COMPLETED 事件 */
function makeNodeCompletedEvent(
  nodeId: string,
  payload: Record<string, unknown> = {},
): ExecutionEvent {
  return {
    event_id: "evt-1",
    instance_id: "inst-test",
    event_type: "NODE_COMPLETED",
    node_id: nodeId,
    timestamp: new Date().toISOString(),
    payload,
  } as ExecutionEvent;
}

// ---------------------------------------------------------------------------
// OE-01：display_content 呈现
// ---------------------------------------------------------------------------

describe("OE-01: 输出展示区呈现标题与内容", () => {
  it("NODE_COMPLETED 含 display_content 时展示标题与内容", async () => {
    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasState()}
      />,
    );

    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: "Hello World",
          display_title: "查询结果",
        }),
      );
    });

    expect(screen.getByText("查询结果")).toBeTruthy();
    expect(screen.getByText("Hello World")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// OE-02：JSON 内容格式化
// ---------------------------------------------------------------------------

describe("OE-02: JSON 内容格式化展示", () => {
  it("对象内容以 JSON.stringify 格式化", async () => {
    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasState()}
      />,
    );

    const obj = { rows: [1, 2, 3], count: 3 };
    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: obj,
          display_title: "数据结果",
        }),
      );
    });

    expect(screen.getByText("数据结果")).toBeTruthy();
    // 既有缺陷最小修复：@testing-library/dom 10 的 matches 仅归一化元素文本、
    // 不归一化查询串，多行字符串默认匹配必然失败；identity normalizer 保持
    // "全文精确相等"语义（比默认归一化更严格），断言意图不变。
    expect(
      screen.getByText(JSON.stringify(obj, null, 2), {
        normalizer: (text: string) => text,
      }),
    ).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// OE-03：空值占位
// ---------------------------------------------------------------------------

describe("OE-03: 空值占位", () => {
  it("display_content 为 null 时展示（无输出）", async () => {
    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasState()}
      />,
    );

    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: null,
          display_title: "空结果",
        }),
      );
    });

    expect(screen.getByText("空结果")).toBeTruthy();
    expect(screen.getByText("（无输出）")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// OE-04：既有节点空 payload 不出现输出展示条目
// ---------------------------------------------------------------------------

describe("OE-04: 既有节点空 payload 回归", () => {
  it("NODE_COMPLETED 空 payload 时不出现输出展示区", async () => {
    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasState()}
      />,
    );

    act(() => {
      mockOnEvent?.(makeNodeCompletedEvent("node-output-1", {}));
    });

    const outputSection = screen.queryByText("输出展示");
    expect(outputSection).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// OE-05：深色主题
// ---------------------------------------------------------------------------

describe("OE-05: 深色主题 darkTokens 取色", () => {
  it("深色模式下 wfp-overlay-output 类族样式含 darkTokens 值", async () => {
    mockTheme = "dark";
    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasState()}
      />,
    );

    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: "test",
          display_title: "标题",
        }),
      );
    });

    expect(screen.getByText("标题")).toBeTruthy();
    mockTheme = "light";
  });
});

// ---------------------------------------------------------------------------
// OE-06：超长内容滚动
// ---------------------------------------------------------------------------

describe("OE-06: 超长内容滚动", () => {
  it("超长 JSON 内容容器有 max-height 与 overflow-y: auto", async () => {
    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasState()}
      />,
    );

    const largeObj = Array.from({ length: 100 }, (_, i) => ({ id: i, value: `item-${i}` }));
    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: largeObj,
          display_title: "大量数据",
        }),
      );
    });

    expect(screen.getByText("大量数据")).toBeTruthy();
    // 同 OE-02：identity normalizer 修复多行字符串默认匹配失效（既有缺陷），断言意图不变
    expect(
      screen.getByText(JSON.stringify(largeObj, null, 2), {
        normalizer: (text: string) => text,
      }),
    ).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// optimize_output_display_node（CHG-11）：output_mode 渲染策略 + 就地下载
// ---------------------------------------------------------------------------

/** 构造带 output_mode 的画布状态（configuration 为 output_display 配置） */
function makeCanvasStateWithMode(outputMode: string[]): WorkflowCanvasState {
  return {
    nodes: [
      {
        id: "node-output-1",
        type: "output_display",
        position: { x: 0, y: 0 },
        data: {
          node_id: "node-output-1",
          name: "输出展示",
          node_type: "output_display",
          configuration: {
            node_type: "output_display",
            output_mode: outputMode,
          },
        },
      },
    ],
    edges: [],
  };
}

describe("UT-12: 下载入口按 output_mode 呈现", () => {
  it("output_mode 不含 file 时无下载入口", async () => {
    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasStateWithMode(["screen"])}
      />,
    );

    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: "Hello World",
          display_title: "查询结果",
        }),
      );
    });

    expect(screen.getByText("查询结果")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "下载" })).toBeNull();
  });

  it("output_mode 含 file 时呈现下载按钮，点击产出 Blob 且文件名含节点标识", async () => {
    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    const clickedAnchors: HTMLAnchorElement[] = [];
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        clickedAnchors.push(this);
      });

    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasStateWithMode(["screen", "file"])}
      />,
    );

    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: { rows: [1, 2, 3] },
          display_title: "查询结果",
        }),
      );
    });

    act(() => {
      screen.getByRole("button", { name: "下载" }).click();
    });

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    expect(clickedAnchors).toHaveLength(1);
    const clickedAnchor = clickedAnchors[0];
    expect(clickedAnchor?.download).toBe("node-output-1-查询结果.json");
    expect(clickedAnchor?.href).toBe("blob:mock-url");
    clickSpy.mockRestore();
  });
});

describe("UT-13: 下载文件内容与 display_content 序列化往返一致", () => {
  /** 以 FileReader 读取 Blob 文本（jsdom 25 的 Blob 未实现 .text()） */
  function readBlobText(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(reader.error);
      reader.readAsText(blob);
    });
  }

  it("点击下载后 Blob 内容为 JSON.stringify(display_content)（结构化往返）", async () => {
    const createObjectURL = vi.fn((_blob: Blob) => "blob:mock-url");
    URL.createObjectURL = createObjectURL;
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function () {
        /* jsdom 不实现导航，阻断即可 */
      });

    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasStateWithMode(["screen", "file"])}
      />,
    );

    const content = { rows: [1, 2, 3], count: 3 };
    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: content,
          display_title: "数据结果",
        }),
      );
    });

    act(() => {
      screen.getByRole("button", { name: "下载" }).click();
    });

    const blob = createObjectURL.mock.calls[0]?.[0] as Blob;
    expect(blob).toBeTruthy();
    expect(blob.type).toBe("application/json");
    const text = await readBlobText(blob);
    expect(JSON.parse(text)).toEqual(content);
    clickSpy.mockRestore();
  });
});

describe("UT-19: 按 output_mode 决定渲染条目（display_content 判定键不变）", () => {
  it("仅 file 模式：标题与下载按钮呈现，内容条目不渲染", async () => {
    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasStateWithMode(["file"])}
      />,
    );

    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: "仅文件内容",
          display_title: "文件结果",
        }),
      );
    });

    expect(screen.getByText("文件结果")).toBeTruthy();
    expect(screen.getByRole("button", { name: "下载" })).toBeTruthy();
    expect(screen.queryByText("仅文件内容")).toBeNull();
  });

  it("screen+file 模式：内容条目与下载按钮同时呈现", async () => {
    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasStateWithMode(["screen", "file"])}
      />,
    );

    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: "双模式内容",
          display_title: "双模式结果",
        }),
      );
    });

    expect(screen.getByText("双模式内容")).toBeTruthy();
    expect(screen.getByRole("button", { name: "下载" })).toBeTruthy();
  });

  it("缺省 output_mode（旧配置）：内容条目渲染且无下载按钮", async () => {
    const { default: NodeExecutionStatusOverlay } = await import(
      "../NodeExecutionStatusOverlay"
    );
    render(
      <NodeExecutionStatusOverlay
        instanceId="inst-test"
        canvasState={makeCanvasState()}
      />,
    );

    act(() => {
      mockOnEvent?.(
        makeNodeCompletedEvent("node-output-1", {
          display_content: "旧配置内容",
          display_title: "旧配置结果",
        }),
      );
    });

    expect(screen.getByText("旧配置内容")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "下载" })).toBeNull();
  });
});