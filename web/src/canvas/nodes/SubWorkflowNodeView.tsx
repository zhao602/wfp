// =============================================================================
// 子工作流节点渲染组件（src/canvas/nodes/SubWorkflowNodeView.tsx）
//
// 职责：在画布上渲染子工作流节点的视觉表示（统一外壳 NodeShell，容器内嵌环形）。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

function SubWorkflowNodeViewInternal({
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
      nodeType="subworkflow"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.subworkflow : "#e8eaf6"}
      accent={isDark ? darkTokens.typeBorder.subworkflow : "#3f51b5"}
      isDark={isDark}
      selected={selected}
    />
  );
}

const SubWorkflowNodeView = memo(SubWorkflowNodeViewInternal);
export default SubWorkflowNodeView;
