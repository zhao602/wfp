// =============================================================================
// 校验问题面板单元测试（execution-visualization/__tests__/ValidationIssuesPanel.test.tsx）
//
// 运行：npx vitest run src/execution-visualization/__tests__/ValidationIssuesPanel.test.tsx
//
// 覆盖用例（B13 U3 / design D5.2/D5.5）：
//   TC-VP-01 问题行渲染：code / message / `节点 X · 字段 Y · 行 Z` 定位文本
//   TC-VP-01b 严重级别色点：error 红 / warning 琥珀
//   TC-VP-02 「定位到画布」仅含 node_id 行出现，点击回调携带 node_id
//   TC-VP-03 定位文本逐级省略（字段单列 / 行号单列 / 全缺不渲染定位行）
//   TC-VP-04 标题错误/警告计数
//   TC-VP-05 关闭回调
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ValidationIssuesPanel from "../ValidationIssuesPanel";
import type { ValidationReport } from "../../workflow-definition/definitionTypes";

/** 基准报告：1 错误（含 node_id/field/line 全定位要素）+ 1 警告（全缺） */
const baseReport: ValidationReport = {
  valid: false,
  definition_id: "demo",
  issues: [
    {
      code: "WF_JOIN_WAIT_COUNT_INVALID",
      severity: "error",
      message: "wait_count 不能大于分支数",
      node_id: "join_1",
      field: "wait_count",
      location: { line: 12 },
    },
    {
      code: "WF_AGENT_MODEL_MISSING",
      severity: "warning",
      message: "未配置模型标识",
    },
  ],
};

/** 渲染面板并返回回调桩 */
function renderPanel(report: ValidationReport) {
  const onLocateNode = vi.fn();
  const onClose = vi.fn();
  render(
    <ValidationIssuesPanel
      report={report}
      onLocateNode={onLocateNode}
      onClose={onClose}
    />,
  );
  return { onLocateNode, onClose };
}

describe("ValidationIssuesPanel 校验问题面板", () => {
  it("TC-VP-01 问题行渲染：code / message / 定位文本", () => {
    renderPanel(baseReport);
    expect(screen.getByText("WF_JOIN_WAIT_COUNT_INVALID")).toBeInTheDocument();
    expect(screen.getByText("wait_count 不能大于分支数")).toBeInTheDocument();
    expect(
      screen.getByText("节点 join_1 · 字段 wait_count · 行 12"),
    ).toBeInTheDocument();
    expect(screen.getByText("WF_AGENT_MODEL_MISSING")).toBeInTheDocument();
    expect(screen.getByText("未配置模型标识")).toBeInTheDocument();
  });

  it("TC-VP-01b 严重级别色点：error 红 / warning 琥珀", () => {
    const { container } = render(
      <ValidationIssuesPanel
        report={baseReport}
        onLocateNode={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const dots = container.querySelectorAll(".wfp-validation-panel__dot");
    expect(dots).toHaveLength(2);
    expect(dots[0]).toHaveStyle({ backgroundColor: "#d32f2f" });
    expect(dots[1]).toHaveStyle({ backgroundColor: "#f59e0b" });
  });

  it("TC-VP-02 「定位到画布」仅含 node_id 行出现，点击回调携带 node_id", () => {
    const { onLocateNode } = renderPanel(baseReport);
    const locateButtons = screen.queryAllByText("定位到画布");
    // 警告行无 node_id → 不渲染按钮，全局仅 1 个
    expect(locateButtons).toHaveLength(1);
    fireEvent.click(locateButtons[0]);
    expect(onLocateNode).toHaveBeenCalledTimes(1);
    expect(onLocateNode).toHaveBeenCalledWith("join_1");
  });

  it("TC-VP-03 定位文本逐级省略：字段单列 / 行号单列 / 全缺不渲染", () => {
    const report: ValidationReport = {
      valid: false,
      issues: [
        {
          code: "C_FIELD",
          severity: "error",
          message: "m1",
          field: "wait_count",
        },
        {
          code: "C_LINE",
          severity: "error",
          message: "m2",
          location: { line: 7 },
        },
        { code: "C_NONE", severity: "warning", message: "m3" },
      ],
    };
    const { container } = render(
      <ValidationIssuesPanel
        report={report}
        onLocateNode={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("字段 wait_count")).toBeInTheDocument();
    expect(screen.getByText("行 7")).toBeInTheDocument();
    // 无 node_id 夹具：不产生「节点 」前缀文本
    expect(screen.queryByText(/^节点 /)).toBeNull();
    // 定位行逐项核：C_FIELD / C_LINE 各渲染一条，C_NONE 全缺不渲染定位行
    const items = container.querySelectorAll(".wfp-validation-panel__item");
    expect(items).toHaveLength(3);
    expect(
      items[0].querySelector(".wfp-validation-panel__location"),
    ).not.toBeNull();
    expect(
      items[1].querySelector(".wfp-validation-panel__location"),
    ).not.toBeNull();
    expect(
      items[2].querySelector(".wfp-validation-panel__location"),
    ).toBeNull();
  });

  it("TC-VP-04 标题错误/警告计数", () => {
    renderPanel(baseReport);
    expect(
      screen.getByText("校验未通过（1 错误 / 1 警告）"),
    ).toBeInTheDocument();
  });

  it("TC-VP-05 关闭回调", () => {
    const { onClose } = renderPanel(baseReport);
    fireEvent.click(screen.getByLabelText("关闭校验面板"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
