// =============================================================================
// 面板内联图标基元（src/canvas/panels/shared/PanelIcon.tsx）
//
// 职责：配置面板通用的线性描边 SVG 图标（currentColor 取色，零外部依赖）。
// 来源：自 NodeConfigurationPanel.tsx 纯搬运上提（arch_review_gap_b1_hardening
// B1 批次 T1.2 共享基元落位），代码零语义变化，仅补 export 供面板族复用。
// =============================================================================

/**
 * 图标路径表：24×24 viewBox 线性图标。
 * 语义单一 + 全局同族（line 2px / round cap），替代原「文字按钮」，
 * 文案改由 aria-label + title 承载（可访问名不丢失）。
 */
const PANEL_ICON_PATHS = {
  info: "M12 16v-4M12 8h.01",
  trash: "M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v6M14 11v6",
  reset: "M3.5 12a8.5 8.5 0 1 0 2.5-6L3.5 8.5M3.5 3.5v5h5",
  pencil: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z",
  list: "M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01",
  plus: "M12 5v14M5 12h14",
  up: "M6 15l6-6 6 6",
  down: "M6 9l6 6 6-6",
  close: "M18 6 6 18M6 6l12 12",
} as const;

export type PanelIconName = keyof typeof PANEL_ICON_PATHS;

/** 线性图标：info 额外叠加外圈圆，与其余图标在轮廓上可区分 */
export function PanelIcon({ name, size = 14 }: { name: PanelIconName; size?: number }): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {name === "info" ? <circle cx="12" cy="12" r="9" /> : null}
      <path d={PANEL_ICON_PATHS[name]} />
    </svg>
  );
}
