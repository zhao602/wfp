// =============================================================================
// 大模型节点渲染组件（src/canvas/nodes/LlmNodeView.tsx）
//
// 职责：在画布上渲染大模型节点的视觉表示（统一外壳 NodeShell，智能家族大圆角）。
// 身份色与面板同源：浅色描边 = NCP_TYPE_IDENTITY.llm.light / NodePalette 色条
// （#c2185b）；深色描边 = darkTokens.typeBorder.llm（= NCP_TYPE_IDENTITY.llm.dark，
// #ec407a）——llm_code_nodes_v2 CHG-F08 / DC-13-3 B8。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

function LlmNodeViewInternal({
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
      nodeType="llm"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.llm : "#fce4ec"}
      accent={isDark ? darkTokens.typeBorder.llm : "#c2185b"}
      isDark={isDark}
      selected={selected}
    />
  );
}

const LlmNodeView = memo(LlmNodeViewInternal);
export default LlmNodeView;
