// =============================================================================
// 导入链空值形态归因测试（workflow-definition/__tests__/yamlImportNormalization.test.ts）
//
// 执行：cd web && npx vitest run src/workflow-definition/__tests__/yamlImportNormalization.test.ts
//
// fix_agent_null_length CHG-19 / DC-02（P1 透传实证 → P2 守卫定稿成对留痕）：
//   E2-a 自备 YAML `available_tools:`（键存在值为空）→ yamlToCanvas →
//       P1 实证（修复前）：画布节点 configuration.available_tools === null 透传
//       （候选 A 主通道成立，RED 留痕）；P2 定稿（L1/G-01 归一）：恒为 []（AC-04）
//   E2-b available_tools 键缺失 → P1 实证 === undefined（候选 D 变体）；
//       P2 定稿：归一为 []
//   E2-c 真实范例 YAML（conditional_loop_pipeline，agent 节点缺键，E-03 实证）→
//       P1 实证 === undefined（范例文件导入链直达渲染树）；
//       P2 定稿：归一为 []
//   导入后「点击 agent 节点渲染不崩溃」由 CHG-17 归因单测（E1-a/b 经
//   NodeConfigurationPanel 渲染入口）承接覆盖，两防线各自独立生效（DC-03）。
// =============================================================================

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { yamlToCanvas } from "../yamlToCanvasImporter";
import type { AgentNodeConfiguration } from "../definitionTypes";

/** 构造最小双节点工作流 YAML（trigger → agent），available_tools 字段形态由调用方注入 */
function makeAgentWorkflowYaml(availableToolsLines: string[] = []): string {
  return [
    'spec_version: "1.0"',
    'definition_id: "wf-null-guard"',
    'name: "空值守卫实验"',
    "definition_version: 1",
    "inputs: []",
    "outputs: []",
    "nodes:",
    "  - node_id: \"trigger_1\"",
    '    name: "触发"',
    '    node_type: "trigger"',
    "    configuration:",
    '      node_type: "trigger"',
    '      trigger_type: "manual"',
    "      input_template: {}",
    "  - node_id: \"agent_1\"",
    '    name: "智能体"',
    '    node_type: "agent"',
    '    output_name: "result"',
    "    configuration:",
    '      node_type: "agent"',
    '      model_identifier: "m-1"',
    '      system_prompt: "p"',
    ...availableToolsLines,
    "edges:",
    "  - edge_id: \"e1\"",
    '    source_node_id: "trigger_1"',
    '    target_node_id: "agent_1"',
  ].join("\n");
}

/** 从导入结果中取 agent_1 节点配置 */
function getAgentConfig(yamlText: string): AgentNodeConfiguration {
  const result = yamlToCanvas(yamlText);
  expect(result.ok).toBe(true);
  if (!result.ok) {
    throw new Error(result.error);
  }
  const node = result.canvasState.nodes.find((n) => n.id === "agent_1");
  expect(node).toBeDefined();
  return node!.data.configuration as AgentNodeConfiguration;
}

describe("yamlToCanvas 导入链 available_tools 归一守卫（fix_agent_null_length E2-a/b/c 定稿，AC-04）", () => {
  it("E2-a 自备 YAML available_tools: 空值 → L1 归一：画布配置恒为 []（P1 实证 null 透传已断根）", () => {
    const config = getAgentConfig(makeAgentWorkflowYaml(["      available_tools:"]));
    expect(config.available_tools).toEqual([]);
    expect(Array.isArray(config.available_tools)).toBe(true);
  });

  it("E2-b available_tools 键缺失 → L1 归一：画布配置恒为 []（P1 实证 undefined 透传已断根）", () => {
    const config = getAgentConfig(makeAgentWorkflowYaml([]));
    expect(config.available_tools).toEqual([]);
    expect(Array.isArray(config.available_tools)).toBe(true);
  });

  it("E2-c 真实范例 YAML（conditional_loop_pipeline）agent 节点缺键 → L1 归一：[] 直达画布（E-03 范例导入链）", () => {
    const yamlText = readExampleYaml("conditional_loop_pipeline/conditional_loop_pipeline.yaml");
    const result = yamlToCanvas(yamlText);
    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error(result.error);
    }
    const agentNode = result.canvasState.nodes.find((n) => n.data.node_type === "agent");
    expect(agentNode).toBeDefined();
    const config = agentNode!.data.configuration as AgentNodeConfiguration;
    expect(config.available_tools).toEqual([]);
    expect(Array.isArray(config.available_tools)).toBe(true);
  });

  it("对照：available_tools 合法数组导入保序透传（INV-02 零回归）", () => {
    const config = getAgentConfig(
      makeAgentWorkflowYaml([
        "      available_tools:",
        '        - "report_builder"',
        '        - "weather_query"',
      ]),
    );
    expect(config.available_tools).toEqual(["report_builder", "weather_query"]);
  });
});

// =============================================================================
// fix_agent_null_length tasks 5.5（DC-08 完全放行 / INV-06 旧 YAML 可加载）：
// 范例库数据侧的 tool 节点移除（P4 纯删除，OP-04）与导入链对存量 tool 资产的
// 放行正交——含 node_type: tool 的旧 YAML 经导入链照常解析、tool 节点入画布，
// 不跳过、不报错、不提示（不新增提示 UI 与开关）。
// =============================================================================
describe("旧 YAML 含 tool 节点兼容放行（fix_agent_null_length tasks 5.5，DC-08/INV-06）", () => {
  it("含 node_type: tool 的旧 YAML → 照常解析、tool 节点入画布、配置保真", () => {
    const legacyYaml = [
      'spec_version: "1.0"',
      'definition_id: "legacy-with-tool"',
      'name: "存量含 tool 工作流"',
      "definition_version: 1",
      "inputs: []",
      "outputs: []",
      "nodes:",
      "  - node_id: \"trigger_1\"",
      '    name: "触发"',
      '    node_type: "trigger"',
      "    configuration:",
      '      node_type: "trigger"',
      '      trigger_type: "manual"',
      "      input_template: {}",
      "  - node_id: \"tool_1\"",
      '    name: "旧工具节点"',
      '    node_type: "tool"',
      '    output_name: "tool_result"',
      "    configuration:",
      '      node_type: "tool"',
      '      tool_identifier: "report_builder"',
      '      tool_source: "local"',
      '      arguments: {}',
      "edges:",
      "  - edge_id: \"e1\"",
      '    source_node_id: "trigger_1"',
      '    target_node_id: "tool_1"',
    ].join("\n");

    const result = yamlToCanvas(legacyYaml);
    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error(result.error);
    }
    const toolNode = result.canvasState.nodes.find((node) => node.id === "tool_1");
    expect(toolNode).toBeDefined();
    expect(toolNode!.data.node_type).toBe("tool");
    const configuration = toolNode!.data.configuration as Record<string, unknown>;
    expect(configuration["tool_identifier"]).toBe("report_builder");
    expect(configuration["tool_source"]).toBe("local");
  });
});

/** 读取项目根 examples/ 下真实范例 YAML 文本（vitest 根为 web/，上溯一级至项目根；
 *  Windows 下 import.meta.url 会被 vite SSR 加 /@fs/ 前缀，故用 cwd 解析） */
function readExampleYaml(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), "..", "examples", relativePath), "utf-8");
}
