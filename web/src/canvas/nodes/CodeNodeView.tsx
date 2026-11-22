// =============================================================================
// 代码节点渲染组件（src/canvas/nodes/CodeNodeView.tsx）
//
// 职责：在画布上渲染代码节点的视觉表示（统一外壳 NodeShell，逻辑家族近直角形
//       + 节点名等宽字体）。
// 身份色与面板同源：浅色描边 = NCP_TYPE_IDENTITY.code.light / NodePalette 色条
// （#455a64）；深色描边 = darkTokens.typeBorder.code（= NCP_TYPE_IDENTITY.code.dark，
// #78909c）——llm_code_nodes_v2 CHG-F08 / DC-13-3 B8。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

function CodeNodeViewInternal({
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
      nodeType="code"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.code : "#eceff1"}
      accent={isDark ? darkTokens.typeBorder.code : "#455a64"}
      isDark={isDark}
      selected={selected}
      monoName
    />
  );
}

const CodeNodeView = memo(CodeNodeViewInternal);
export default CodeNodeView;
