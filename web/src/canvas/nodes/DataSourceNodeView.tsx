// =============================================================================
// 数据源节点渲染组件（src/canvas/nodes/DataSourceNodeView.tsx）
//
// 职责：在画布上渲染数据源节点的视觉表示（统一外壳 NodeShell，数据家族切角形）。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

/** 数据源节点渲染：左右双向句柄 */
function DataSourceNodeViewInternal({
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
      nodeType="data_source"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.data_source : "#fff3e0"}
      accent={isDark ? darkTokens.typeBorder.data_source : "#f57c00"}
      isDark={isDark}
      selected={selected}
    />
  );
}

const DataSourceNodeView = memo(DataSourceNodeViewInternal);
export default DataSourceNodeView;
