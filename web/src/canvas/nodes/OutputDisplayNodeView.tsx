// =============================================================================
// 输出展示节点渲染组件（src/canvas/nodes/OutputDisplayNodeView.tsx）
//
// 职责：在画布上渲染输出展示节点的视觉表示（统一外壳 NodeShell，出口左强调条形）。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

function OutputDisplayNodeViewInternal({
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
      nodeType="output_display"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.output_display : "#e0f2f1"}
      accent={isDark ? darkTokens.typeBorder.output_display : "#00897b"}
      isDark={isDark}
      selected={selected}
    />
  );
}

const OutputDisplayNodeView = memo(OutputDisplayNodeViewInternal);
export default OutputDisplayNodeView;
