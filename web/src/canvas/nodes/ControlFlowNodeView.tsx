// =============================================================================
// 控制流节点渲染组件（src/canvas/nodes/ControlFlowNodeView.tsx）
//
// 职责：在画布上渲染控制流节点（分支/循环/并行/审核）的视觉表示
//       （统一外壳 NodeShell，逻辑家族近直角形）。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

/** 控制流节点渲染：左右双向句柄 */
function ControlFlowNodeViewInternal({
  data,
  selected,
}: {
  data: { name: string };
  selected?: boolean;
}) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  return (
    <NodeShell
      nodeType="control_flow"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.control_flow : "#fce4ec"}
      accent={isDark ? darkTokens.typeBorder.control_flow : "#c62828"}
      isDark={isDark}
      selected={selected}
    />
  );
}

const ControlFlowNodeView = memo(ControlFlowNodeViewInternal);
export default ControlFlowNodeView;
