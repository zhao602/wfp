// =============================================================================
// 模型单选列表组件测试（src/canvas/__tests__/ModelSelectionList.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/ModelSelectionList.test.tsx
//
// 覆盖 agent_model_selector T3.3 用例组（DC-02 / DC-03，FR-06~FR-08）：
//   TC-F1 候选渲染为单选卡片（AC-09/FR-06）
//   TC-F2 当前值高亮（AC-10/FR-07）
//   TC-F3 单击候选写回单值（AC-11/FR-08）
//   TC-F8 首屏非阻塞：挂载即渲染 loading 态
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { AvailableModelItem, AvailableModelsResponse } from "../../api/apiTypes";

// listAvailableModels mock：vi.hoisted 保证工厂引用在模块求值前完成初始化
const { listAvailableModelsMock } = vi.hoisted(() => ({
  listAvailableModelsMock: vi.fn(),
}));
vi.mock("../../api/orchestrationApiClient", () => ({
  orchestrationApiClient: {
    listAvailableModels: () => listAvailableModelsMock(),
  },
}));

import ModelSelectionList from "../ModelSelectionList";

/** 样例候选：主 / 备 各一项 */
const MODELS: AvailableModelItem[] = [
  { model_identifier: "gpt-primary", source: "primary" },
  { model_identifier: "gpt-fallback", source: "fallback" },
];

/** 组装候选响应体 */
function modelsResponse(models: AvailableModelItem[]): AvailableModelsResponse {
  return { models, total: models.length };
}

beforeEach(() => {
  listAvailableModelsMock.mockReset();
});

describe("TC-F1 候选渲染为单选卡片（AC-09/FR-06）", () => {
  it("加载成功后逐项渲染候选卡片（主/备来源标注）", async () => {
    listAvailableModelsMock.mockResolvedValue(modelsResponse(MODELS));
    render(<ModelSelectionList value="" onChange={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTitle("gpt-primary")).toBeTruthy();
    });
    expect(screen.getByTitle("gpt-fallback")).toBeTruthy();
    expect(screen.getByText("主模型")).toBeTruthy();
    expect(screen.getByText("备用")).toBeTruthy();
  });
});

describe("TC-F2 当前值高亮（AC-10/FR-07）", () => {
  it("value 命中的候选呈选中态，其余不选中", async () => {
    listAvailableModelsMock.mockResolvedValue(modelsResponse(MODELS));
    render(<ModelSelectionList value="gpt-fallback" onChange={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTitle("gpt-fallback")).toBeTruthy();
    });
    const selected = screen.getByTitle("gpt-fallback");
    const unselected = screen.getByTitle("gpt-primary");
    expect(selected.className).toContain("wfp-msl-card-selected");
    expect(unselected.className).not.toContain("wfp-msl-card-selected");
  });
});

describe("TC-F3 单击候选写回单值（AC-11/FR-08）", () => {
  it("单击未选中候选 → onChange 以该标识单值回写", async () => {
    listAvailableModelsMock.mockResolvedValue(modelsResponse(MODELS));
    const onChange = vi.fn();
    render(<ModelSelectionList value="gpt-primary" onChange={onChange} />);
    await waitFor(() => {
      expect(screen.getByTitle("gpt-fallback")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("gpt-fallback"));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("gpt-fallback");
  });
});

describe("TC-F8 首屏非阻塞：挂载即渲染 loading 态", () => {
  it("请求未返回时同步渲染加载提示，不阻塞首屏", () => {
    listAvailableModelsMock.mockReturnValue(new Promise(() => {}));
    render(<ModelSelectionList value="" onChange={vi.fn()} />);
    expect(screen.getByText("模型候选加载中…")).toBeTruthy();
  });
});

describe("TC-F4 空候选降级（DC-06）", () => {
  it("total==0 → 手工输入框 + 空候选提示，value 原值进入输入框", async () => {
    listAvailableModelsMock.mockResolvedValue(modelsResponse([]));
    const onChange = vi.fn();
    render(<ModelSelectionList value="legacy-model" onChange={onChange} />);
    await waitFor(() => {
      expect(
        screen.getByText("未检测到已配置模型，请检查 .env 的 LLM_PRIMARY_MODEL"),
      ).toBeTruthy();
    });
    const input = screen.getByPlaceholderText("例如：通用对话模型");
    expect((input as HTMLInputElement).value).toBe("legacy-model");
    fireEvent.change(input, { target: { value: "manual-model" } });
    expect(onChange).toHaveBeenCalledWith("manual-model");
  });
});

describe("TC-F5 请求失败降级、无未捕获异常（DFX §4.2.1）", () => {
  it("拉取异常 → 手工输入框 + 失败提示，可编辑、无未捕获异常", async () => {
    listAvailableModelsMock.mockRejectedValue(new Error("network down"));
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const onChange = vi.fn();
    render(<ModelSelectionList value="" onChange={onChange} />);
    await waitFor(() => {
      expect(screen.getByText("未能获取已配置模型，可手工填写")).toBeTruthy();
    });
    const input = screen.getByPlaceholderText("例如：通用对话模型");
    fireEvent.change(input, { target: { value: "offline-model" } });
    expect(onChange).toHaveBeenCalledWith("offline-model");
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});

describe("TC-F6 旧值不在候选回显保留且未被清空（AC-14/DC-04）", () => {
  it("候选外 value → 末位「当前值」选中态卡片 + 标注，onChange 不被触发", async () => {
    listAvailableModelsMock.mockResolvedValue(
      modelsResponse([{ model_identifier: "m-primary", source: "primary" }]),
    );
    const onChange = vi.fn();
    render(<ModelSelectionList value="custom-model-v2" onChange={onChange} />);
    await waitFor(() => {
      expect(screen.getByTitle("m-primary")).toBeTruthy();
    });
    const currentCard = screen.getByTitle("custom-model-v2");
    expect(currentCard.className).toContain("wfp-msl-card-selected");
    expect(screen.getByText("未在已配置模型内")).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("TC-F7 自定义入口写回（OP-05）", () => {
  it("触发「自定义模型」→ 手工输入并写回，旁附提示可见", async () => {
    listAvailableModelsMock.mockResolvedValue(modelsResponse(MODELS));
    const onChange = vi.fn();
    render(<ModelSelectionList value="gpt-primary" onChange={onChange} />);
    await waitFor(() => {
      expect(screen.getByTitle("gpt-primary")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("自定义模型"));
    expect(screen.getByText("未经 .env 配置的模型可能不可用")).toBeTruthy();
    const input = screen.getByPlaceholderText("例如：通用对话模型");
    fireEvent.change(input, { target: { value: "my-custom-llm" } });
    expect(onChange).toHaveBeenCalledWith("my-custom-llm");
  });
});

describe("TC-F9 卸载丢弃迟到响应（无状态更新警告）", () => {
  it("卸载后迟到 resolve 不产生状态更新或控制台错误", async () => {
    let resolveLate:
      | ((res: AvailableModelsResponse) => void)
      | undefined;
    listAvailableModelsMock.mockImplementation(
      () =>
        new Promise<AvailableModelsResponse>((resolve) => {
          resolveLate = resolve;
        }),
    );
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { unmount } = render(
      <ModelSelectionList value="" onChange={vi.fn()} />,
    );
    unmount();
    resolveLate?.(modelsResponse(MODELS));
    await Promise.resolve();
    await Promise.resolve();
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// L-02 value 运行时宽容归一（fix_agent_null_length G-05 / DC-05，隐患清单五要素映射）
// 双态：null / undefined → 归一空串安全渲染；合法 string 同值零回归（对照 TC-F2/F4/F6）
// ---------------------------------------------------------------------------

describe("L-02 value 运行时宽容归一（fix_agent_null_length）", () => {
  it("L-02-a value=null：候选态不崩溃、无选中高亮、无「当前值」卡片，单击候选可写回", async () => {
    listAvailableModelsMock.mockResolvedValue(modelsResponse(MODELS));
    const onChange = vi.fn();
    render(
      <ModelSelectionList value={null as unknown as string} onChange={onChange} />,
    );
    await waitFor(() => {
      expect(screen.getByTitle("gpt-primary")).toBeTruthy();
    });
    expect(
      screen.getByTitle("gpt-primary").className,
    ).not.toContain("wfp-msl-card-selected");
    expect(screen.queryByText("未在已配置模型内")).toBeNull();
    fireEvent.click(screen.getByTitle("gpt-primary"));
    expect(onChange).toHaveBeenCalledWith("gpt-primary");
  });

  it("L-02-b value=undefined：降级手工输入框值为空串，可编辑写回，无未捕获异常", async () => {
    listAvailableModelsMock.mockRejectedValue(new Error("network down"));
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const onChange = vi.fn();
    render(
      <ModelSelectionList value={undefined as unknown as string} onChange={onChange} />,
    );
    await waitFor(() => {
      expect(screen.getByText("未能获取已配置模型，可手工填写")).toBeTruthy();
    });
    const input = screen.getByPlaceholderText("例如：通用对话模型");
    expect((input as HTMLInputElement).value).toBe("");
    fireEvent.change(input, { target: { value: "typed-model" } });
    expect(onChange).toHaveBeenCalledWith("typed-model");
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});
