// =============================================================================
// 开发包提交向导测试（src/market/__tests__/DevSubmitWizard.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/market/__tests__/DevSubmitWizard.test.tsx
//
// 覆盖用例（agent_market_user_development T23.1 / AC-DEV-21/22/23 / DC-06）：
//   TC-DSW-01 四步渲染：步骤 1 获取脚手架（DV-1/DV-2 加载）
//   TC-DSW-02 全流程：选脚手架 → 上传 → 元数据 → 提交 → 报告按 layer 分组展示
//   TC-DSW-03 提交失败 → 错误条且既有配置不清空（AC-DEV-22）
//   TC-DSW-04 脚手架加载失败 → 错误条且页面可渲染（AC-DEV-22）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { DevSubmitWizard } from "../DevSubmitWizard";
import {
  listDevScaffolds,
  getDevScaffold,
  submitDevPackage,
} from "../devSubmissionApiClient";
import type {
  DevScaffoldDetail,
  DevScaffoldListResponse,
  DevSubmissionResponse,
  DevValidationReport,
} from "../devSubmissionTypes";

vi.mock("../devSubmissionApiClient", () => ({
  listDevScaffolds: vi.fn(),
  getDevScaffold: vi.fn(),
  submitDevPackage: vi.fn(),
}));

/** 脚手架清单夹具 */
function makeScaffoldList(): DevScaffoldListResponse {
  return {
    scaffolds: [
      {
        scaffold_id: "basic-node",
        name: "基础节点脚手架",
        version: "1.0.0",
        summary: "最小可提交节点包",
        required_files: ["manifest.yaml", "node.py"],
      },
    ],
    total: 1,
  };
}

/** 脚手架详情夹具（五要素 + 文件模板内联） */
function makeScaffoldDetail(): DevScaffoldDetail {
  return {
    scaffold_id: "basic-node",
    name: "基础节点脚手架",
    version: "1.0.0",
    summary: "最小可提交节点包",
    directory_structure: "manifest.yaml\nnode.py",
    required_files: ["manifest.yaml", "node.py"],
    optional_files: ["requirements.txt"],
    manifest_spec: "node_name/version/input_schema/output_schema/entry_point",
    interface_contract: "def run(query: str) -> str",
    dependency_and_runtime: "零第三方依赖",
    file_templates: [{ path: "node.py", content: "def run(query):\n    return query", required: true }],
    fetch_hint: "文件模板内容已内联，可直接复制",
  };
}

/** 三层校验报告夹具（L1/L3 各一项发现） */
function makeReport(): DevValidationReport {
  return {
    report_id: "r1",
    valid: true,
    layers: ["L1", "L2", "L3"],
    findings: [
      {
        layer: "L1",
        rule_id: "input_schema_undeclared",
        severity: "warning",
        message: "input_schema 未声明",
        location: null,
      },
      {
        layer: "L3",
        rule_id: "dangerous_call",
        severity: "error",
        message: "检测到 eval 调用",
        location: "node.py:1",
      },
    ],
    scanner_version: "dev-validation/1.0",
    checked_at: "2026-11-11T00:00:00Z",
    error: null,
  };
}

/** 提交响应夹具 */
function makeSubmitResponse(): DevSubmissionResponse {
  return {
    submission_id: "sub-1",
    state: "submitted",
    accepted: true,
    validation_report: makeReport(),
    rejection_reason: "",
    package_digest: "d".repeat(64),
    package_size: 100,
    file_count: 2,
  };
}

beforeEach(() => {
  vi.mocked(listDevScaffolds).mockResolvedValue(makeScaffoldList());
  vi.mocked(getDevScaffold).mockResolvedValue(makeScaffoldDetail());
  vi.mocked(submitDevPackage).mockResolvedValue(makeSubmitResponse());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DevSubmitWizard", () => {
  it("TC-DSW-01 四步渲染：步骤 1 获取脚手架（清单 + 详情内联）", async () => {
    render(<DevSubmitWizard developerId="alice" />);

    // 步骤 1 渲染；步骤 2/3/4 未渲染
    expect(screen.getByTestId("dev-wizard-step-scaffold")).toBeInTheDocument();
    expect(screen.queryByTestId("dev-wizard-step-upload")).not.toBeInTheDocument();
    expect(screen.queryByTestId("dev-wizard-step-info")).not.toBeInTheDocument();
    expect(screen.queryByTestId("dev-wizard-step-result")).not.toBeInTheDocument();

    // 脚手架清单加载（DV-1）
    const option = await screen.findByText("基础节点脚手架（basic-node v1.0.0）");
    expect(option).toBeInTheDocument();

    // 选中 → 加载详情（DV-2 五要素内联）
    fireEvent.change(screen.getByTestId("dev-wizard-scaffold-select"), {
      target: { value: "basic-node" },
    });
    const detail = await screen.findByTestId("dev-wizard-scaffold-detail");
    expect(detail).toHaveTextContent("manifest.yaml");
    expect(detail).toHaveTextContent("def run(query: str) -> str");
    expect(getDevScaffold).toHaveBeenCalledWith("basic-node");
  });

  it("TC-DSW-02 全流程提交 → 报告按 layer 分组展示（AC-DEV-21）", async () => {
    render(<DevSubmitWizard developerId="alice" />);

    // 步骤 1：选中脚手架 → 下一步
    await screen.findByText("基础节点脚手架（basic-node v1.0.0）");
    fireEvent.change(screen.getByTestId("dev-wizard-scaffold-select"), {
      target: { value: "basic-node" },
    });
    fireEvent.click(screen.getByTestId("dev-wizard-next-1"));
    expect(screen.getByTestId("dev-wizard-step-upload")).toBeInTheDocument();

    // 步骤 2：上传包 → 下一步
    const file = new File(["zip-bytes"], "pkg.zip", { type: "application/zip" });
    fireEvent.change(screen.getByTestId("dev-wizard-package-input"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByTestId("dev-wizard-next-2"));
    expect(screen.getByTestId("dev-wizard-step-info")).toBeInTheDocument();

    // 步骤 3：元数据（developer 预填自 prop）→ 提交
    const developerInput = screen.getByTestId("dev-wizard-developer-input");
    expect(developerInput).toHaveValue("alice");
    fireEvent.change(screen.getByTestId("dev-wizard-node-name-input"), {
      target: { value: "demo-node" },
    });
    fireEvent.change(screen.getByTestId("dev-wizard-version-input"), {
      target: { value: "1.0.0" },
    });
    fireEvent.click(screen.getByTestId("dev-wizard-submit-button"));

    // 步骤 4：结果 + 三层报告（按 layer 分组）
    const result = await screen.findByTestId("dev-wizard-step-result");
    expect(result).toHaveTextContent("提交成功");
    expect(result).toHaveTextContent("sub-1");
    expect(submitDevPackage).toHaveBeenCalledTimes(1);
    const call = vi.mocked(submitDevPackage).mock.calls[0];
    expect(call?.[1]).toBe("alice");
    expect(call?.[2]).toBe("demo-node");
    expect(call?.[3]).toBe("1.0.0");

    const reportL1 = screen.getByTestId("dev-wizard-report-L1");
    expect(reportL1).toHaveTextContent("input_schema_undeclared");
    expect(screen.getByTestId("dev-wizard-report-L3")).toHaveTextContent("dangerous_call");
    expect(screen.getByTestId("dev-wizard-report-L2")).toHaveTextContent("0 项");
  });

  it("TC-DSW-03 提交失败 → 错误条且既有配置不清空（AC-DEV-22）", async () => {
    vi.mocked(submitDevPackage).mockRejectedValue(
      new Error("重复提交：alice/demo-node@1.0.0 已存在"),
    );
    render(<DevSubmitWizard developerId="alice" />);

    // 走到步骤 3 并填写
    await screen.findByText("基础节点脚手架（basic-node v1.0.0）");
    fireEvent.change(screen.getByTestId("dev-wizard-scaffold-select"), {
      target: { value: "basic-node" },
    });
    fireEvent.click(screen.getByTestId("dev-wizard-next-1"));
    const file = new File(["zip-bytes"], "pkg.zip");
    fireEvent.change(screen.getByTestId("dev-wizard-package-input"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByTestId("dev-wizard-next-2"));
    fireEvent.change(screen.getByTestId("dev-wizard-node-name-input"), {
      target: { value: "demo-node" },
    });
    fireEvent.change(screen.getByTestId("dev-wizard-version-input"), {
      target: { value: "1.0.0" },
    });
    fireEvent.click(screen.getByTestId("dev-wizard-submit-button"));

    // 错误条可见；步骤 3 既有配置未被清空
    const errorBar = await screen.findByTestId("dev-wizard-error");
    expect(errorBar).toHaveTextContent("重复提交：alice/demo-node@1.0.0 已存在");
    expect(screen.getByTestId("dev-wizard-node-name-input")).toHaveValue("demo-node");
    expect(screen.getByTestId("dev-wizard-version-input")).toHaveValue("1.0.0");
    expect(screen.queryByTestId("dev-wizard-step-result")).not.toBeInTheDocument();
  });

  it("TC-DSW-04 脚手架加载失败 → 错误条且页面可渲染（AC-DEV-22）", async () => {
    vi.mocked(listDevScaffolds).mockRejectedValue(new Error("查询开发脚手架清单失败"));
    render(<DevSubmitWizard developerId="alice" />);

    const errorBar = await screen.findByTestId("dev-wizard-error");
    expect(errorBar).toHaveTextContent("查询开发脚手架清单失败");
    // 页面可渲染：步骤 1 + 选择器仍在
    expect(screen.getByTestId("dev-wizard-step-scaffold")).toBeInTheDocument();
    expect(screen.getByTestId("dev-wizard-scaffold-select")).toBeInTheDocument();
  });
});
