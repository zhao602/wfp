// =============================================================================
// 真实范例文件加载测试（__tests__/exampleLibraryFixtures.test.ts）
//
// 执行：cd web && npx vitest run src/__tests__/exampleLibraryFixtures.test.ts
//
// fix_agent_null_length CHG-21（E-09 勘误下的新增覆盖，tasks 5.3 / AC-07 / GK-08）：
//   - 9 个经 tool 节点移除（P4 §5.1/§5.2，14 实例）后的范例 YAML 逐文件断言：
//     ① 无 node_type: tool 节点（AC-08）
//     ② 全部 edges 的 source/target 指向存在节点（无悬空边）
//     ③ control_flow 的 conditions[].target_node_id / default_branch_target /
//        loop_body_entry / loop_target / parallel_branches 指向存在节点或字段已删除
//     ④ outputs 声明均有来源节点（output_name 匹配）或声明已删除
//     ⑤ agent 节点 available_tools 三态安全（缺省或数组，非 null）
//   - GK-08 范例计数守恒（T16.5 修正后基线）：
//     P4 移除 14 个 tool 节点（54 → 40）；T16.5 逻辑修正为各范例补齐
//     输出展示节点与默认分支承接节点（40 → 49）
//   - 12 个注册范例（T16.5 全量登记）yamlToCanvas 导入成功
// =============================================================================

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import yaml from "yaml";
import { yamlToCanvas } from "../workflow-definition/yamlToCanvasImporter";

interface ParsedNode {
  node_id: string;
  node_type: string;
  output_name?: string;
  configuration?: Record<string, unknown>;
}

interface ParsedEdge {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
}

interface ParsedOutputDeclaration {
  name: string;
}

interface ParsedDefinition {
  spec_version?: string;
  definition_id?: string;
  nodes: ParsedNode[];
  edges: ParsedEdge[];
  outputs?: ParsedOutputDeclaration[];
}

/** P4 tool 节点移除涉及的 9 个范例文件（3 注册 + 6 未注册，OP-03 一并移除） */
const TOOL_REMOVED_FILES: readonly string[] = [
  "conditional_loop_pipeline/conditional_loop_pipeline.yaml",
  "feature_engineering/feature_engineering.yaml",
  "drift_detection/drift_detection.yaml",
  "realtime_subscription/realtime_subscription_workflow.yaml",
  "reflection_iteration/reflection_iteration.yaml",
  "data_engineering/data_engineering_agent.yaml",
  "hierarchical_delegation/hierarchical_delegation.yaml",
  "subworkflow_nesting/subworkflow_nesting.yaml",
  "parallel_execution/parallel_execution.yaml",
];

/**
 * T16.5 修正后逐文件节点数：
 * - P4 销项后基线 40（原 54 − 14 个 tool 节点）；
 * - T16.5 为各范例补输出展示节点（8 个）与 drift_detection 的
 *   基线读取/基线未建立承接节点（2 个）+ hierarchical_delegation
 *   汇总 Agent（1 个）→ 修正后合计 49。
 */
const EXPECTED_NODE_COUNTS: Readonly<Record<string, number>> = {
  "conditional_loop_pipeline/conditional_loop_pipeline.yaml": 6,
  "feature_engineering/feature_engineering.yaml": 6,
  "drift_detection/drift_detection.yaml": 7,
  "realtime_subscription/realtime_subscription_workflow.yaml": 6,
  "reflection_iteration/reflection_iteration.yaml": 6,
  "data_engineering/data_engineering_agent.yaml": 5,
  "hierarchical_delegation/hierarchical_delegation.yaml": 5,
  "subworkflow_nesting/subworkflow_nesting.yaml": 3,
  "parallel_execution/parallel_execution.yaml": 5,
};

/** 12 个注册范例（T16.5 全量登记，与后端 _EXAMPLE_REGISTRY 一一对应） */
const REGISTERED_EXAMPLES: readonly string[] = [
  "sequential_pipeline/sequential_pipeline.yaml",
  "supervisor_routing/supervisor_routing.yaml",
  "conditional_loop_pipeline/conditional_loop_pipeline.yaml",
  "feature_engineering/feature_engineering.yaml",
  "drift_detection/drift_detection.yaml",
  "parallel_execution/parallel_execution.yaml",
  "hierarchical_delegation/hierarchical_delegation.yaml",
  "reflection_iteration/reflection_iteration.yaml",
  "data_engineering/data_engineering_agent.yaml",
  "data_transform_pipeline/data_transform_pipeline.yaml",
  "subworkflow_nesting/subworkflow_nesting.yaml",
  "realtime_subscription/realtime_subscription_workflow.yaml",
];

/** 读取项目根 examples/ 下真实范例 YAML 文本（vitest 根为 web/，上溯一级至项目根） */
function readExampleYaml(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), "..", "examples", relativePath), "utf-8");
}

function parseExample(relativePath: string): ParsedDefinition {
  const parsed = yaml.parse(readExampleYaml(relativePath)) as ParsedDefinition;
  expect(Array.isArray(parsed.nodes)).toBe(true);
  expect(Array.isArray(parsed.edges)).toBe(true);
  return parsed;
}

describe("真实范例文件加载（fix_agent_null_length CHG-21，AC-07/AC-08/GK-08）", () => {
  it("9 个 tool 移除后范例：无 tool 节点、无悬空边、control_flow/outputs 引用闭合、agent 配置三态安全", () => {
    for (const relativePath of TOOL_REMOVED_FILES) {
      const definition = parseExample(relativePath);
      const nodeIds = new Set(definition.nodes.map((node) => node.node_id));
      expect(nodeIds.size).toBe(definition.nodes.length);

      // ① 无 tool 节点（AC-08）
      const toolNodes = definition.nodes.filter((node) => node.node_type === "tool");
      expect(toolNodes).toEqual([]);

      // ② 边引用闭合
      for (const edge of definition.edges) {
        expect(nodeIds.has(edge.source_node_id)).toBe(true);
        expect(nodeIds.has(edge.target_node_id)).toBe(true);
      }

      // ③ control_flow 引用闭合（指向存在节点或字段已删除）
      for (const node of definition.nodes) {
        if (node.node_type !== "control_flow") {
          continue;
        }
        const config = node.configuration ?? {};
        const conditions = config["conditions"];
        if (Array.isArray(conditions)) {
          for (const condition of conditions) {
            const targetNodeId = (condition as Record<string, unknown>)["target_node_id"];
            expect(nodeIds.has(targetNodeId as string)).toBe(true);
          }
        }
        const referenceFields = ["default_branch_target", "loop_body_entry", "loop_target"] as const;
        for (const field of referenceFields) {
          const value = config[field];
          if (value !== undefined && value !== null) {
            expect(nodeIds.has(value as string)).toBe(true);
          }
        }
        const parallelBranches = config["parallel_branches"];
        if (Array.isArray(parallelBranches)) {
          for (const branch of parallelBranches) {
            expect(nodeIds.has(branch as string)).toBe(true);
          }
        }
      }

      // ④ outputs 键必备且为列表（后端结构校验器 REQUIRED_TOP_LEVEL_FIELDS，
      //    definition_structure_validator.py:25——整键删除将致「缺少必填要素：
      //    outputs」加载失败，悬空声明条目删除后保留空列表）；
      //    声明条目均有来源节点（output_name 匹配）
      expect(Array.isArray(definition.outputs)).toBe(true);
      for (const output of definition.outputs ?? []) {
        const sourceNode = definition.nodes.find((node) => node.output_name === output.name);
        expect(sourceNode).toBeDefined();
      }

      // ⑤ agent 节点 available_tools 三态安全（缺省或数组，非 null/非数组）
      for (const node of definition.nodes) {
        if (node.node_type !== "agent") {
          continue;
        }
        const config = node.configuration ?? {};
        const availableTools = config["available_tools"];
        if (availableTools !== undefined) {
          expect(Array.isArray(availableTools)).toBe(true);
        }
      }

      // T16.5 逐文件节点数（P4 销项 + 逻辑修正补节点）
      expect(definition.nodes.length).toBe(EXPECTED_NODE_COUNTS[relativePath]);
    }
  });

  it("GK-08 范例计数守恒（T16.5 修正后）：9 文件节点总数 = P4 销项 40 + 补齐 9 = 49", () => {
    const total = TOOL_REMOVED_FILES.reduce(
      (sum, relativePath) => sum + parseExample(relativePath).nodes.length,
      0,
    );
    expect(total).toBe(49);
  });

  it("12 个注册范例 yamlToCanvas 导入成功（T16.5 全量登记）", () => {
    expect(REGISTERED_EXAMPLES.length).toBe(12);
    for (const relativePath of REGISTERED_EXAMPLES) {
      const result = yamlToCanvas(readExampleYaml(relativePath));
      expect(result.ok).toBe(true);
      if (result.ok) {
        expect(result.canvasState.nodes.length).toBeGreaterThan(0);
      } else {
        throw new Error(`${relativePath} 导入失败：${result.error}`);
      }
    }
  });
});
