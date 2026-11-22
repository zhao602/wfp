// =============================================================================
// 节点配置面板组件（src/canvas/NodeConfigurationPanel.tsx）
//
// 职责：选中节点时按节点类型展示与必备配置项一致的配置字段。
// 设计意图（对应需求规格 5.5.1.3、任务 T18.5）：
//   1. 按节点类型分派不同配置表单，字段与后端配置模型一一对应；
//   2. 未选中节点时显示提示；
//   3. 配置变更实时回写画布状态。
// =============================================================================

import { useCallback, useEffect, useRef, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import type { CanvasNodeData, WorkflowCanvasState } from "./canvasTypes";
import type { NodeConfiguration } from "../workflow-definition/definitionTypes";
import {
  isTriggerConfig,
  isAgentConfig,
  isToolConfig,
  isDataSourceConfig,
  isControlFlowConfig,
  isSubWorkflowConfig,
  isDynamicInjectorConfig,
  isDataTransformConfig,
  isOutputDisplayConfig,
  isVectorKnowledgeBaseConfig,
  isLlmConfig,
  isCodeConfig,
} from "../workflow-definition/definitionTypes";
import {
  getNodeHint,
  buildHintId,
  PANEL_COMMON_HINTS,
} from "./nodeConfigHints";
import OutputNameField from "./OutputNameField";
import { applyAgentTemplateToCanvas } from "./applyAgentTemplate";
import type { ApplyTemplateResult } from "./applyAgentTemplate";
import type { AgentTemplate } from "../market/marketTypes";
import { PanelIcon } from "./panels/shared/PanelIcon";
import { HintAnchor } from "./panels/shared/Field";
import { emptyStateCss, mainPanelCss, ncpScrollTokens, NCP_TYPE_IDENTITY, scrollCss } from "./panels/shared/panelStyle";
import { OutputContractSection } from "./panels/shared/OutputContractSection";
import { CodeConfigForm } from "./panels/code";
import { LlmConfigForm } from "./panels/llm";
import { OutputDisplayConfigForm } from "./panels/output_display";
import { VectorKnowledgeBaseConfigForm } from "./panels/vector_knowledge_base";
import { AgentConfigForm } from "./panels/agent";
import { DataSourceConfigForm } from "./panels/data_source";
import { DataTransformConfigForm } from "./panels/data_transform";
import { ControlFlowConfigForm } from "./panels/control_flow";
import { TriggerConfigForm } from "./panels/trigger";
import { SubWorkflowConfigForm } from "./panels/subworkflow";
import { DynamicInjectorConfigForm } from "./panels/dynamic_injector";
import { ToolConfigForm } from "./panels/tool";

/** 名称校验结果：成功无载荷；失败携带原因文案（判别联合，TS 窄化分支穷尽） */
type NameValidationResult = { ok: true } | { ok: false; reason: string };

/**
 * 节点名称校验纯函数（DC-3）：三重校验短路求值，返回首个失败原因。
 * 校验顺序：非空白 → 长度 ≤ 32 → 字符集（控制字符与零宽字符）。
 * 零副作用、零依赖，reason 文案集中在此唯一出处（INV-8）。
 */
export function validateNodeName(name: string): NameValidationResult {
  if (name.trim().length < 1) {
    return { ok: false, reason: "节点名称不能为空" };
  }
  if (name.length > 32) {
    return { ok: false, reason: "节点名称不能超过 32 个字符" };
  }
  if (/[\u0000-\u001F\u200B-\u200D\uFEFF]/.test(name)) {
    return { ok: false, reason: "节点名称包含不支持的字符" };
  }
  return { ok: true };
}

/** 配置面板默认宽度（px），未注入 width prop 时回落（向后兼容，spec 4.5.2） */
const DEFAULT_CONFIG_PANEL_WIDTH = 280;

/** 配置面板属性 */
interface NodeConfigurationPanelProps {
  selectedNode: CanvasNodeData | null;
  canvasState: WorkflowCanvasState;
  onCanvasStateChange: (update: React.SetStateAction<WorkflowCanvasState>) => void;
  /** 可选宽度注入（px），未传时回落默认 280px；未选中与选中两分支共用同一注入值 */
  width?: number;
  /** 删除当前选中节点的意图上抛；未传时不渲染删除按钮（可选，向后兼容） */
  onDeleteNode?: (nodeId: string) => void;
}

/** 节点配置面板：按节点类型展示对应配置字段 */
export default function NodeConfigurationPanel({
  selectedNode,
  canvasState,
  onCanvasStateChange,
  width = DEFAULT_CONFIG_PANEL_WIDTH,
  onDeleteNode,
}: NodeConfigurationPanelProps) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";

  /** 回滚基准：上次提交确认的合法名称（DC-4），切换节点时由 useEffect 重置 */
  const committedNameRef = useRef<string>(selectedNode?.name ?? "");
  /** 校验提示瞬态：null 表示无提示（DC-6），不参与画布状态持久化 */
  const [validationError, setValidationError] = useState<string | null>(null);

  /**
   * 更新选中节点的配置（CHG-2/DC-4）：函数式原子回写——updater 内以 prev.nodes
   * 为基准重算，消除闭包快照整份回写的覆盖窗口（RG-2）；依赖去除 canvasState，
   * 非目标节点与原引用逐字段不变；edges 原样透传。
   */
  const updateConfig = useCallback(
    (newConfig: NodeConfiguration) => {
      if (!selectedNode) return;
      onCanvasStateChange((prev) => ({
        nodes: prev.nodes.map((node) =>
          node.data.node_id === selectedNode.node_id
            ? { ...node, data: { ...node.data, configuration: newConfig } }
            : node,
        ),
        edges: prev.edges,
      }));
    },
    [selectedNode, onCanvasStateChange],
  );

  /**
   * 更新选中节点名称（DC-7/DC-2）：函数式回写目标节点名称，仅替换 data.name，
   * 非目标节点引用不变、edges 原样透传（INV-1/INV-2）；逐字符 onChange 调用无防抖。
   */
  const updateName = useCallback(
    (newName: string) => {
      if (!selectedNode) return;
      onCanvasStateChange((prev) => ({
        nodes: prev.nodes.map((node) =>
          node.data.node_id === selectedNode.node_id
            ? { ...node, data: { ...node.data, name: newName } }
            : node,
        ),
        edges: prev.edges,
      }));
    },
    [selectedNode, onCanvasStateChange],
  );

  /**
   * 更新选中节点的命名输出（output_name）：函数式回写目标节点 output_name，
   * 仅替换 data.output_name，非目标节点引用不变、edges 原样透传。
   */
  const updateOutputName = useCallback(
    (newOutputName: string | undefined) => {
      if (!selectedNode) return;
      onCanvasStateChange((prev) => ({
        nodes: prev.nodes.map((node) =>
          node.data.node_id === selectedNode.node_id
            ? { ...node, data: { ...node.data, output_name: newOutputName } }
            : node,
        ),
        edges: prev.edges,
      }));
    },
    [selectedNode, onCanvasStateChange],
  );

  /**
   * 最近一次应用成功的模板名（DC-07/T5.1）：面板级 useState 记忆，不写入任何
   * 节点字段（INV-NTD-03）；随节点切换重置（卸载随组件卸载自然重置）。
   */
  const [appliedTemplateName, setAppliedTemplateName] = useState<string | undefined>(undefined);
  useEffect(() => {
    setAppliedTemplateName(undefined);
  }, [selectedNode?.node_id]);

  /**
   * 应用 Agent 模板到选中节点（DC-02/T3.1）：控件经 onApplyTemplate 上抛意图，
   * 面板以既有纯函数按函数式原子回写执行（RG-2）——updater 内以 prev 为基准，
   * 仅目标节点合并预设（INV-NTD-14），既有纯函数签名零改动；结果同步返回供
   * 控件渲染一次性应用反馈（DC-07）。
   */
  const handleApplyAgentTemplate = useCallback(
    (template: AgentTemplate): ApplyTemplateResult | null => {
      if (!selectedNode) return null;
      // 以对象属性承载结果：闭包内赋值 + 同步段读取，规避 CFA 将 let 收窄为 never
      const resultBox: { result: ApplyTemplateResult | null } = { result: null };
      onCanvasStateChange((prev) => {
        resultBox.result = applyAgentTemplateToCanvas(prev, template, selectedNode.node_id);
        return resultBox.result.canvasState;
      });
      if (resultBox.result !== null && resultBox.result.applied) {
        setAppliedTemplateName(template.name);
      }
      return resultBox.result;
    },
    [selectedNode, onCanvasStateChange],
  );

  /**
   * 提交名称（DC-5）：blur/Enter 共用提交处理器，校验与提示设置在 updater 外
   * 同步段（INV-3）；合法时确认基准，非法时回滚回写并提示。
   */
  const commitName = useCallback(() => {
    if (!selectedNode) return;
    const result = validateNodeName(selectedNode.name);
    if (result.ok) {
      committedNameRef.current = selectedNode.name;
    } else {
      setValidationError(result.reason);
      updateName(committedNameRef.current);
    }
  }, [selectedNode, updateName]);

  /** 选中节点切换时重置回滚基准与清除提示（DC-4 同步策略） */
  useEffect(() => {
    if (selectedNode) {
      committedNameRef.current = selectedNode.name;
    }
    setValidationError(null);
  }, [selectedNode?.node_id]);

  if (!selectedNode) {
    return (
      <div
        className="wfp-ncp-scroll wfp-ncp-panel wfp-ncp-panel--empty"
        style={{
          width: `${width}px`,
          borderLeft: `1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}`,
          backgroundColor: isDark ? darkTokens.surface.bg : "#ffffff",
          padding: "16px",
          color: isDark ? darkTokens.text.hint : "#999",
          fontSize: "13px",
          overflowY: ncpScrollTokens.overflowY,
          minHeight: 0,
          overscrollBehavior: ncpScrollTokens.overscrollBehavior,
        }}
      >
        <style>{emptyStateCss(isDark) + scrollCss(isDark)}</style>
        <div className="wfp-ncp-empty">
          <span className="wfp-ncp-empty-glyph">
            <PanelIcon name="pencil" size={16} />
          </span>
          <span className="wfp-ncp-empty-title">未选中节点</span>
          <span className="wfp-ncp-empty-text">请选择一个节点以编辑配置</span>
        </div>
      </div>
    );
  }

  /** 节点级说明：8 种节点类型各有对应文案，缺失时不渲染 */
  const nodeHint = getNodeHint(selectedNode.node_type);

  return (
    <div
      className={`wfp-ncp-panel wfp-ncp-scroll wfp-ncp-panel--${selectedNode.node_type}`}
      style={{
          width: `${width}px`,
          borderLeft: `1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}`,
          backgroundColor: isDark ? darkTokens.surface.bg : "#ffffff",
          padding: "16px",
          overflowY: ncpScrollTokens.overflowY,
          minHeight: 0,
          overscrollBehavior: ncpScrollTokens.overscrollBehavior,
      }}
    >
      <div className="wfp-ncp-identity-bar" />
      <style>{mainPanelCss(isDark)}</style>
      <div className="wfp-ncp-head">
        <div className="wfp-ncp-head-row">
          <span className="wfp-ncp-type-badge">{NCP_TYPE_IDENTITY[selectedNode.node_type].badge}</span>
          {nodeHint && (
            <span className="wfp-ncp-hint-anchor wfp-ncp-hint-anchor--row">
              <button type="button" className="wfp-ncp-hint-btn" aria-label="节点说明">
                <PanelIcon name="info" size={13} />
              </button>
              <span className="wfp-ncp-node-hint">{nodeHint}</span>
            </span>
          )}
          {onDeleteNode && (
            <button
              type="button"
              className="wfp-ncp-btn wfp-ncp-btn--danger wfp-ncp-btn--icon"
              disabled={canvasState.nodes.length <= 1}
              title={canvasState.nodes.length <= 1 ? "至少保留一个节点" : "删除该节点"}
              aria-label="删除节点"
              onClick={() => onDeleteNode(selectedNode.node_id)}
            >
              <PanelIcon name="trash" size={13} />
            </button>
          )}
        </div>
        <h3 className="wfp-ncp-title">{selectedNode.name}</h3>
      </div>
      <div className="wfp-ncp-group">
        <label className="wfp-ncp-field">
          <span className="wfp-ncp-labelrow">
            <span className="wfp-ncp-field-label">节点名称</span>
            <HintAnchor id={buildHintId("common", "name")} text={PANEL_COMMON_HINTS.name.helper} />
          </span>
          <span className="wfp-ncp-control">
            <input
              type="text"
              value={selectedNode.name}
              className="wfp-ncp-input"
              aria-describedby={buildHintId("common", "name")}
              onChange={(e) => { updateName(e.target.value); setValidationError(null); }}
              onBlur={commitName}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitName(); } }}
            />
          </span>
          {validationError && <span className="wfp-ncp-error">{validationError}</span>}
        </label>
        <OutputNameField
          currentNodeId={selectedNode.node_id}
          value={selectedNode.output_name}
          allNodes={canvasState.nodes}
          onChange={updateOutputName}
        />
      </div>
      <OutputContractSection nodeType={selectedNode.node_type} />
      <div className="wfp-ncp-divider" />
      {isTriggerConfig(selectedNode.configuration) && (
        <TriggerConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
        />
      )}
      {isAgentConfig(selectedNode.configuration) && (
        <AgentConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
          canvasState={canvasState}
          targetNodeId={selectedNode.node_id}
          onCanvasStateChange={onCanvasStateChange}
          onApplyTemplate={handleApplyAgentTemplate}
          appliedTemplateName={appliedTemplateName}
        />
      )}
      {isToolConfig(selectedNode.configuration) && (
        <ToolConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
          canvasState={canvasState}
          targetNodeId={selectedNode.node_id}
        />
      )}
      {isDataSourceConfig(selectedNode.configuration) && (
        <DataSourceConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
        />
      )}
      {isControlFlowConfig(selectedNode.configuration) && (
        <ControlFlowConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
          allNodes={canvasState.nodes}
        />
      )}
      {isSubWorkflowConfig(selectedNode.configuration) && (
        <SubWorkflowConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
          canvasState={canvasState}
          targetNodeId={selectedNode.node_id}
        />
      )}
      {isDynamicInjectorConfig(selectedNode.configuration) && (
        <DynamicInjectorConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
        />
      )}
      {isDataTransformConfig(selectedNode.configuration) && (
        <DataTransformConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
        />
      )}
      {isOutputDisplayConfig(selectedNode.configuration) && (
        <OutputDisplayConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
          canvasState={canvasState}
          targetNodeId={selectedNode.node_id}
        />
      )}
      {isVectorKnowledgeBaseConfig(selectedNode.configuration) && (
        <VectorKnowledgeBaseConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
          canvasState={canvasState}
          targetNodeId={selectedNode.node_id}
        />
      )}
      {isLlmConfig(selectedNode.configuration) && (
        <LlmConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
          canvasState={canvasState}
          targetNodeId={selectedNode.node_id}
        />
      )}
      {isCodeConfig(selectedNode.configuration) && (
        <CodeConfigForm
          config={selectedNode.configuration}
          onChange={updateConfig}
          canvasState={canvasState}
          targetNodeId={selectedNode.node_id}
        />
      )}
    </div>
  );
}
