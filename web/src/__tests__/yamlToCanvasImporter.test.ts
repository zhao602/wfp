// =============================================================================
// YAML 导入画布测试（src/__tests__/yamlToCanvasImporter.test.ts）
//
// 覆盖验收：A11（导入成功还原、导入失败保持画布不变）
// 本次更新（fix-canvas-drag-clear-and-edge-loop-bugs / T10）：TC-YC-01~04 补齐
// 空集合拒绝分支（nodes/edges 任一空数组 → ok=false，FR-1.5），杜绝空画布经
// 导入链路合法生成（H1-b 断根）。
// ⚠️ 测试为交付物：留待非沙箱环境执行（cd web && npx vitest run）。
// =============================================================================

import { describe, it, expect } from "vitest";
import { yamlToCanvas } from "../workflow-definition/yamlToCanvasImporter";

const VALID_YAML = `
spec_version: "1.0"
definition_id: test_workflow
name: 测试工作流
definition_version: 1
inputs: []
outputs: []
nodes:
  - node_id: trigger_start
    name: 触发器
    node_type: trigger
    configuration:
      node_type: trigger
      trigger_type: manual
      input_template: {}
  - node_id: agent_work
    name: 处理
    node_type: agent
    output_name: result
    configuration:
      node_type: agent
      model_identifier: primary-model
      system_prompt: 处理数据
      available_tools: []
edges:
  - edge_id: e1
    source_node_id: trigger_start
    target_node_id: agent_work
`;

// nodes 非空但 edges 为空数组的边界样本（TC-YC-02）
const EMPTY_EDGES_YAML = `
spec_version: "1.0"
definition_id: empty_edges
name: 空连线
definition_version: 1
inputs: []
outputs: []
nodes:
  - node_id: trigger_start
    name: 触发器
    node_type: trigger
    configuration:
      node_type: trigger
      trigger_type: manual
      input_template: {}
edges: []
`;

// nodes 为空数组的边界样本（TC-YC-01）
const EMPTY_NODES_YAML = `
spec_version: "1.0"
definition_id: empty_nodes
name: 空节点
definition_version: 1
inputs: []
outputs: []
nodes: []
edges: []
`;

describe("yamlToCanvasImporter", () => {
  // TC-YC-03（FR-1.4 回归）：合法定义导入 → ok=true 且节点/连线数量与定义一致
  it("合法 YAML 应导入成功并还原节点", () => {
    const result = yamlToCanvas(VALID_YAML);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.canvasState.nodes).toHaveLength(2);
      expect(result.canvasState.edges).toHaveLength(1);

      const triggerNode = result.canvasState.nodes.find(
        (n) => n.data.node_type === "trigger",
      );
      expect(triggerNode).toBeDefined();
      expect(triggerNode!.data.name).toBe("触发器");
    }
  });

  // TC-YC-04（既有行为回归）：解析失败 → ok=false
  it("非法 YAML 应返回失败结果", () => {
    const result = yamlToCanvas("not: valid: yaml: : :");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("解析失败");
    }
  });

  // TC-YC-04（既有行为回归）：缺字段 → ok=false
  it("缺少 nodes 字段的 YAML 应返回失败", () => {
    const result = yamlToCanvas(`
spec_version: "1.0"
definition_id: incomplete
name: 不完整
definition_version: 1
`);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("nodes");
    }
  });

  // TC-YC-01（FR-1.5）：nodes 为空数组 → ok=false，拒绝导入
  it("nodes 为空数组的定义应被拒绝导入", () => {
    const result = yamlToCanvas(EMPTY_NODES_YAML);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("为空");
    }
  });

  // TC-YC-02（FR-1.5）：edges 为空数组 → ok=false，拒绝导入
  it("edges 为空数组的定义应被拒绝导入", () => {
    const result = yamlToCanvas(EMPTY_EDGES_YAML);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("为空");
    }
  });

  // 补充回归：导入后节点位置应被自动布局
  it("导入后节点位置应被自动布局", () => {
    const result = yamlToCanvas(VALID_YAML);

    expect(result.ok).toBe(true);
    if (result.ok) {
      for (const node of result.canvasState.nodes) {
        expect(node.position.x).toBeGreaterThanOrEqual(0);
        expect(node.position.y).toBeGreaterThanOrEqual(0);
      }
    }
  });
});
