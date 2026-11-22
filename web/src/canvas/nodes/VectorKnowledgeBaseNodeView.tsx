// =============================================================================
// 向量知识库节点渲染组件（src/canvas/nodes/VectorKnowledgeBaseNodeView.tsx）
//
// 职责：在画布上渲染向量知识库节点的视觉表示（统一外壳 NodeShell，数据家族切角形）。
// =============================================================================

import { memo } from "react";
import { useThemeMode, darkTokens } from "../../theme/themeMode";
import { NodeShell } from "../nodeVisual";

function VectorKnowledgeBaseNodeViewInternal({
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
      nodeType="vector_knowledge_base"
      name={data.name}
      fill={isDark ? darkTokens.typeFill.vector_knowledge_base : "#e8f5e9"}
      accent={isDark ? darkTokens.typeBorder.vector_knowledge_base : "#2e7d32"}
      isDark={isDark}
      selected={selected}
    />
  );
}

const VectorKnowledgeBaseNodeView = memo(VectorKnowledgeBaseNodeViewInternal);
export default VectorKnowledgeBaseNodeView;
