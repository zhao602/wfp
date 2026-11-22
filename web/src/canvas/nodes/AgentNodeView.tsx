// =============================================================================
// Agent 节点渲染组件（src/canvas/nodes/AgentNodeView.tsx）
//
// 职责：在画布上渲染 Agent 节点的视觉表示（统一外壳 NodeShell，智能家族大圆角）。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

/** Agent 节点渲染：左右双向句柄 */
function AgentNodeViewInternal({
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
      nodeType="agent"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.agent : "#f3e5f5"}
      accent={isDark ? darkTokens.typeBorder.agent : "#7b1fa2"}
      isDark={isDark}
      selected={selected}
    />
  );
}

const AgentNodeView = memo(AgentNodeViewInternal);
export default AgentNodeView;
