// =============================================================================
// 触发器节点渲染组件（src/canvas/nodes/TriggerNodeView.tsx）
//
// 职责：在画布上渲染触发器节点的视觉表示（统一外壳 NodeShell，入口胶囊形）。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

/** 触发器节点渲染：左侧无输入句柄（入口节点），右侧输出句柄 */
function TriggerNodeViewInternal({
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
      nodeType="trigger"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.trigger : "#e3f2fd"}
      accent={isDark ? darkTokens.typeBorder.trigger : "#1976d2"}
      isDark={isDark}
      selected={selected}
      hideLeftHandle
    />
  );
}

const TriggerNodeView = memo(TriggerNodeViewInternal);
export default TriggerNodeView;
