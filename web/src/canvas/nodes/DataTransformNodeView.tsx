// =============================================================================
// 数据转换节点渲染组件（src/canvas/nodes/DataTransformNodeView.tsx）
//
// 职责：在画布上渲染数据转换节点的视觉表示（统一外壳 NodeShell，数据家族切角形）。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

function DataTransformNodeViewInternal({
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
      nodeType="data_transform"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.data_transform : "#fff8e1"}
      accent={isDark ? darkTokens.typeBorder.data_transform : "#ff9800"}
      isDark={isDark}
      selected={selected}
    />
  );
}

const DataTransformNodeView = memo(DataTransformNodeViewInternal);
export default DataTransformNodeView;
