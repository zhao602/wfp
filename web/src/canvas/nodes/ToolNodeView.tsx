// =============================================================================
// Tool 节点渲染组件（src/canvas/nodes/ToolNodeView.tsx）
//
// 职责：在画布上渲染存量 Tool 节点的视觉表示（merge_tool_into_agent_node
//       DC-C/CHG-18：新建入口已移除，本组件仅服务存量画布渲染，INV-8）。
// 视觉：统一外壳 NodeShell，虚线描边承载「已并入 Agent」迁移语义。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

/** Tool 节点渲染：左右双向句柄 + 迁移提示徽标 */
function ToolNodeViewInternal({
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
      nodeType="tool"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.tool : "#e8f5e9"}
      accent={isDark ? darkTokens.typeBorder.tool : "#388e3c"}
      isDark={isDark}
      selected={selected}
      extraTag={
        /* 迁移提示徽标（FR-3 规则 2）：沿用灰阶辅助色，不引入新色值 */
        <span
          style={{
            padding: "1px 5px",
            borderRadius: "3px",
            fontSize: "10px",
            color: isDark ? darkTokens.text.hint : "#999",
            background: isDark ? darkTokens.surface.bgHover : "#f0f0f0",
          }}
        >
          已并入 Agent
        </span>
      }
    />
  );
}

const ToolNodeView = memo(ToolNodeViewInternal);
export default ToolNodeView;
