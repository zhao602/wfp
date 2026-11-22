// =============================================================================
// 动态注入节点渲染组件（src/canvas/nodes/DynamicInjectorNodeView.tsx）
//
// 职责：在画布上渲染动态注入节点的视觉表示（统一外壳 NodeShell，数据家族切角形）。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

function DynamicInjectorNodeViewInternal({
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
      nodeType="dynamic_injector"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.dynamic_injector : "#f3e5f5"}
      accent={isDark ? darkTokens.typeBorder.dynamic_injector : "#9c27b0"}
      isDark={isDark}
      selected={selected}
    />
  );
}

const DynamicInjectorNodeView = memo(DynamicInjectorNodeViewInternal);
export default DynamicInjectorNodeView;
