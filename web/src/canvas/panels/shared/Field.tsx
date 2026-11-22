// =============================================================================
// 配置面板表单基元族（src/canvas/panels/shared/Field.tsx）
//
// 职责：面板表单通用控件族——提示文案/提示锚点/角标提示/控件装饰器/字段行/
// 字段组，承载字段级提示气泡（nodeConfigHints）与控件 aria-describedby 关联。
// 来源：自 NodeConfigurationPanel.tsx 纯搬运上提（arch_review_gap_b1_hardening
// B1 批次 T1.2 共享基元落位），代码零语义变化，仅补 export 供面板族复用。
// =============================================================================

import { isValidElement, cloneElement } from "react";
import type { NodeType } from "../../../workflow-definition/definitionTypes";
import { resolveFieldHint, buildHintId } from "../../nodeConfigHints";
import { PanelIcon } from "./PanelIcon";

/**
 * 提示气泡：说明文案的浮层载体。
 * 为何 DOM 常驻：控件的 aria-describedby 指向本元素，若按需挂载，未悬停时
 * 读屏目标会消失；可见性一律交由 CSS 的 opacity/visibility 切换，
 * 结构恒定、命中区与无障碍关联零抖动。
 */
function FieldHintText({ id, text }: { id: string; text: string }): JSX.Element {
  return (
    <span id={id} className="wfp-ncp-field-hint" role="tooltip">
      {text}
    </span>
  );
}

/**
 * 说明锚点：标签行右侧的小号信息图标，悬停或键盘聚焦时浮出 FieldHintText。
 * 图标按钮不挂 aria-describedby——同一 id 同时挂在控件与图标上会让
 * 无障碍关系出现重复目标（且既有断言要求描述 id 唯一）。
 */
export function HintAnchor({
  id,
  text,
  label = "查看字段说明",
}: {
  id: string;
  text: string;
  label?: string;
}): JSX.Element {
  return (
    <span className="wfp-ncp-hint-anchor">
      <button type="button" className="wfp-ncp-hint-btn" aria-label={label}>
        <PanelIcon name="info" size={13} />
      </button>
      <FieldHintText id={id} text={text} />
    </span>
  );
}

/**
 * 行内说明锚点（无 id 绑定）：用于非字段级的次要说明（降级行为、输出形态等）。
 * 与字段说明同款图标 + 气泡，只是不参与 aria-describedby 关联。
 */
export function TipDot({ text, label }: { text: string; label: string }): JSX.Element {
  return (
    <span className="wfp-ncp-hint-anchor">
      <button type="button" className="wfp-ncp-hint-btn" aria-label={label}>
        <PanelIcon name="info" size={12} />
      </button>
      <span className="wfp-ncp-field-hint" role="tooltip">
        {text}
      </span>
    </span>
  );
}

/** 对单一原生控件元素合并提示相关属性；非原生元素原样返回（唯一 cloneElement 位置） */
export function decorateControl(
  children: React.ReactNode,
  describedById: string | undefined,
  placeholder: string | undefined,
): React.ReactNode {
  // 双重守卫：仅装饰原生 DOM 元素，排除 Fragment 与自定义组件
  if (!isValidElement(children) || typeof children.type !== "string") {
    return children;
  }
  const tag = children.type;
  const element = children as React.ReactElement<{
    type?: string;
    readOnly?: boolean;
    "aria-describedby"?: string;
    placeholder?: string;
  }>;
  // 仅合并提示相关两键，绝不覆盖 value/onChange/className/style 等既有属性
  const nextProps: {
    "aria-describedby"?: string;
    placeholder?: string;
  } = {};

  if (describedById) {
    nextProps["aria-describedby"] = describedById;
  }

  if (placeholder) {
    const isTextarea = tag === "textarea";
    const isTextInput =
      tag === "input" &&
      (element.props.type === undefined || element.props.type === "text");
    const isReadOnly = element.props.readOnly === true;
    if ((isTextarea || isTextInput) && !isReadOnly) {
      nextProps.placeholder = placeholder;
    }
  }

  return cloneElement(element, nextProps);
}

/**
 * 表单字段包装器（配置面板 v2）：
 *   标签行 = 标签文案 + 说明锚点（说明不再常驻正文，改为悬停气泡）；
 *   控件区 = 单块级 span（保留 label 包裹语义，控件仍可用 label 定位）；
 *   未命中提示时只渲染标签与控件，退化为既有行为。
 */
export function Field({
  nodeType,
  field,
  label,
  index,
  children,
}: {
  nodeType: NodeType | "common";
  field: string;
  label: string;
  index?: number;
  children: React.ReactNode;
}) {
  const hint = resolveFieldHint(nodeType, field);
  const hintId = hint && hint.helper ? buildHintId(nodeType, field, index) : undefined;
  return (
    <label className="wfp-ncp-field">
      <span className="wfp-ncp-labelrow">
        <span className="wfp-ncp-field-label">{label}</span>
        {hintId && hint ? <HintAnchor id={hintId} text={hint.helper} /> : null}
      </span>
      <span className="wfp-ncp-control">{decorateControl(children, hintId, hint?.placeholder)}</span>
    </label>
  );
}

/**
 * 多选控件字段包装器（optimize_output_display_node CHG-09a）：
 * 以 <div> + 标题 <span> 承载，避免既有 Field（<label> 包裹）与多选控件
 * 内部复选框行（<label>）造成 <label> 嵌套；复用 resolveFieldHint 提示机制，
 * 提示渲染结构与 Field 一致（HintAnchor + FieldHintText）；不改 Field 签名（INV-01）。
 */
export function FieldCheckboxGroup({
  nodeType,
  field,
  label,
  index,
  children,
}: {
  nodeType: NodeType | "common";
  field: string;
  label: string;
  index?: number;
  children: React.ReactNode;
}) {
  const hint = resolveFieldHint(nodeType, field);
  const hintId = hint && hint.helper ? buildHintId(nodeType, field, index) : undefined;
  return (
    <div className="wfp-ncp-field">
      <span className="wfp-ncp-labelrow">
        <span className="wfp-ncp-field-label">{label}</span>
        {hintId && hint ? <HintAnchor id={hintId} text={hint.helper} /> : null}
      </span>
      <div className="wfp-ncp-control" aria-describedby={hintId}>
        {children}
      </div>
    </div>
  );
}
