// =============================================================================
// 范例库面板组件（src/example-library/ExampleLibraryPanel.tsx）
//
// 职责：展示范例列表与说明，支持一键加载预置范例并在画布中渲染。
// 设计意图（对应需求规格 5.5.3.3、任务 T20.3）：
//   1. 从后端 GET /examples 拉取范例列表；
//   2. 不可用范例灰显并展示原因（缺少环境变量等）；
//   3. 一键加载：调用后端获取范例 YAML，导入画布。
//   4. 折叠交互：默认收起（顶栏只占一个折叠钮宽度），点击展开显示全部范例；
//      展开体以 display: contents 回到根 flex 换行流，按钮排布与折叠改造前一致。
// 设计基调：精炼极简工具风——左侧竖线分组 + 集合图标标签 + 胶囊式范例按钮，
//   中性灰阶三档 + 120ms 克制动效，深浅双模式令牌同源（spec 5.3.3.2）。
// =============================================================================

import { useEffect, useRef, useState, useCallback } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import { orchestrationApiClient } from "../api/orchestrationApiClient";
import { ApiError } from "../api/apiError";
import type { ExampleEntry } from "../api/apiTypes";
import type { ImportResult } from "../workflow-definition/yamlToCanvasImporter";

/** 范例库面板属性 */
interface ExampleLibraryPanelProps {
  onLoadExample: (yamlText: string) => ImportResult;
}

/**
 * 范例库面板设计令牌（精炼极简工具风）。
 * 同源策略：深色分节逐键引用 darkTokens（编译期强绑定，spec 5.3.3.2）；
 * 浅色值与 topbarTokens 中性灰阶同档位，保持顶栏整体色谱统一。
 */
const exampleTokens = {
  /** 色彩：中性灰阶三档 + 现有红色系（spec 5.1.1.4a） */
  color: {
    label: "#666666", // 标签文字：次级灰
    labelStrong: "#333333", // 展开态标签：加深一档，与收起态形成"当前已打开"的读数差
    divider: "#d0d0d0", // 左侧分组竖线：比顶栏分隔线深半档，营造独立区域感
    hint: "#999999", // 加载提示
    danger: "#d32f2f", // 错误警示：沿用现有红色系（spec 4.5.2）
    hover: "#f2f2f2", // 折叠钮悬停底：与顶栏按钮同档灰阶
    active: "#e6e6e6", // 折叠钮按压底：深一档确认响应
    border: "#e0e0e0", // 折叠钮描边：与顶栏分隔线同款
    focus: "#333333", // 焦点轮廓：中性深灰（禁令色域内最深可用色）
    countBg: "#ececec", // 计数徽标底：收起态唯一的信息量载体
    countText: "#5a5a5a", // 计数徽标字
  },
  /** 字号：与顶栏同级（spec 5.3.1.2a）；计数徽标小一档（10px 仅承载数字） */
  fontSize: { label: "13px", body: "12px", count: "10px" },
  /** 深色分节：逐键引用 darkTokens（spec 5.3.3.2） */
  dark: {
    color: {
      label: darkTokens.text.secondary,
      labelStrong: darkTokens.text.primary,
      divider: darkTokens.surface.border,
      hint: darkTokens.text.hint,
      danger: darkTokens.danger,
      hover: darkTokens.surface.bgHover,
      active: darkTokens.surface.bgActive,
      border: darkTokens.surface.border,
      focus: darkTokens.text.focus,
      countBg: darkTokens.surface.bgActive,
      countText: darkTokens.text.secondary,
    },
  },
} as const;

/**
 * 折叠交互配色契约：浅深两套令牌同构，供样式注入函数统一消费。
 * 显式声明结构而非依赖 `as const` 的字面量类型——深色套值来自 darkTokens，
 * 与浅色字面量类型不同，需以 string 结构收敛后才可互相替代。
 */
interface ExampleColorSet {
  label: string;
  labelStrong: string;
  divider: string;
  hint: string;
  danger: string;
  hover: string;
  active: string;
  border: string;
  focus: string;
  countBg: string;
  countText: string;
}

/** 范例列表容器 id：折叠钮 aria-controls 的指向目标 */
const EXAMPLE_BODY_ID = "wfp-example-library-body";


/** 集合图标：16×16 内联 SVG，stroke=currentColor 继承标签色 */
const collectionIcon = (
  <svg
    viewBox="0 0 16 16"
    width={14}
    height={14}
    aria-hidden="true"
    focusable="false"
    stroke="currentColor"
    fill="none"
    strokeWidth={1.25}
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" />
    <rect x="9" y="2.5" width="4.5" height="4.5" rx="1" />
    <rect x="2.5" y="9" width="4.5" height="4.5" rx="1" />
    <rect x="9" y="9" width="4.5" height="4.5" rx="1" />
  </svg>
);

/** 警示图标：12×12 内联 SVG，stroke=currentColor 继承危险色 */
const warningIcon = (
  <svg
    viewBox="0 0 12 12"
    width={12}
    height={12}
    aria-hidden="true"
    focusable="false"
    stroke="currentColor"
    fill="none"
    strokeWidth={1.25}
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M6 1.5L10.5 10h-9z" />
    <path d="M6 5v2.5" />
    <circle cx="6" cy="8.8" r="0.5" fill="currentColor" stroke="none" />
  </svg>
);

/**
 * 折叠箭头：12×12 内联 SVG，stroke=currentColor。
 * 收起态向下（提示"可展开"），展开态由 CSS 旋转 180° 转向上——旋转只走 transform，
 * 不触发重排，且不依赖 React 状态切换两份图元。
 */
const caretIcon = (
  <svg
    viewBox="0 0 12 12"
    width={12}
    height={12}
    aria-hidden="true"
    focusable="false"
    stroke="currentColor"
    fill="none"
    strokeWidth={1.5}
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M2.5 4.5L6 8l3.5-3.5" />
  </svg>
);

/**
 * 折叠交互样式注入文本（类名前缀 wfp-example- 限定作用域）。
 * 关键取舍：
 *   1. 展开体用 display: contents —— 包装元素在布局上"消失"，范例按钮回到面板根
 *      的 flex 换行流中，展开后的排布与折叠前完全一致（不改按钮任何样式）；
 *   2. 显隐由 data-open 驱动而非 hidden 属性——作者样式会覆盖 UA 的 [hidden]
 *      规则，二者混用会出现"收起后仍占位"的竞态；
 *   3. 动效仅 opacity/transform，120~160ms，且尊重 prefers-reduced-motion。
 */
function panelCss(t: ExampleColorSet): string {
  return `
.wfp-example-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: inherit;
  font-size: ${exampleTokens.fontSize.label};
  line-height: 1.2;
  color: ${t.label};
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  /* 负外边距抵消内边距：折叠钮不改变原有标签的视觉起点与顶栏高度 */
  padding: 2px 6px;
  margin: -2px 0 -2px -6px;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease;
}
.wfp-example-toggle:hover {
  background: ${t.hover};
  border-color: ${t.border};
  color: ${t.labelStrong};
}
.wfp-example-toggle:active {
  /* 书写于 :hover 之后，按压优先于悬停 */
  background: ${t.active};
  border-color: ${t.border};
}
.wfp-example-toggle:focus-visible {
  outline: 2px solid ${t.focus};
  outline-offset: 2px;
}
.wfp-example-toggle[aria-expanded="true"] {
  color: ${t.labelStrong};
  background: ${t.hover};
  border-color: ${t.border};
}
.wfp-example-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  min-width: 16px;
  height: 15px;
  padding: 0 4px;
  font-size: ${exampleTokens.fontSize.count};
  font-weight: 500;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  color: ${t.countText};
  background: ${t.countBg};
  border-radius: 7px;
}
.wfp-example-caret {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 12px;
  height: 12px;
  transition: transform 160ms cubic-bezier(0.4, 0, 0.2, 1);
}
.wfp-example-toggle[aria-expanded="true"] .wfp-example-caret {
  transform: rotate(180deg);
}
.wfp-example-body {
  display: contents;
}
.wfp-example-body[data-open="false"] {
  display: none;
}
.wfp-example-body[data-open="true"] > button {
  animation: wfp-example-in 160ms ease-out both;
}
@keyframes wfp-example-in {
  from {
    opacity: 0;
    transform: translateY(-3px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
@media (prefers-reduced-motion: reduce) {
  .wfp-example-toggle,
  .wfp-example-caret {
    transition: none;
  }
  .wfp-example-body[data-open="true"] > button {
    animation: none;
  }
}
`;
}

/** 范例库面板：列出预置范例，支持一键加载 */
export default function ExampleLibraryPanel({
  onLoadExample,
}: ExampleLibraryPanelProps) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [examples, setExamples] = useState<ExampleEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 展开态：默认收起，避免 12 个范例常驻顶栏挤压画布可视区 */
  const [expanded, setExpanded] = useState(false);
  /** 面板根节点：用于判定"点击落点是否在面板外" */
  const containerRef = useRef<HTMLDivElement | null>(null);
  /** 折叠钮：收起后把焦点交还触发者，键盘用户不丢失操作位置 */
  const toggleRef = useRef<HTMLButtonElement | null>(null);

  /**
   * 展开态的全局交互收口：点击面板外收起、Esc 收起并交还焦点。
   * 仅在展开时挂监听，收起态零额外全局监听（避免空转）。
   */
  useEffect(() => {
    if (!expanded) {
      return undefined;
    }
    const handlePointerDown = (event: MouseEvent) => {
      const node = containerRef.current;
      if (node && event.target instanceof Node && !node.contains(event.target)) {
        setExpanded(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setExpanded(false);
        toggleRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [expanded]);

  /** 拉取范例列表 */
  const fetchExamples = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await orchestrationApiClient.listExamples();
      setExamples(response.examples);
    } catch (err) {
        if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("加载范例列表失败");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchExamples();
  }, [fetchExamples]);

  /**
   * 一键加载范例：获取 YAML 文本并导入画布（CHG-6/DC-8）。
   * 消费导入结果（FR-2.3）：导入失败（ok=false）与网络失败同走 error 反馈链路，
   * 消除静默失败；导入成功时清除历史错误（FR-2.4 配套）。
   */
  const handleLoad = useCallback(
    async (exampleId: string) => {
      try {
        const definition = await orchestrationApiClient.loadExample(exampleId);
        const yamlText = JSON.stringify(definition, null, 2);
        const result = onLoadExample(yamlText);
        if (result.ok) {
          setError(null);
        } else {
          setError(result.error);
        }
      } catch (err) {
      if (err instanceof ApiError) {
          setError(`加载范例失败: ${err.message}`);
        } else {
          setError("加载范例失败");
        }
      }
    },
    [onLoadExample],
  );


  const t: ExampleColorSet = isDark ? exampleTokens.dark.color : exampleTokens.color;

  return (
    <div
      ref={containerRef}
      style={{
        display: "flex",
        flexWrap: "wrap", // 窄窗自动折行：空间不足时范例按钮换到下一行，避免溢出页面
        alignItems: "center",
        gap: "8px",
        minWidth: 0, // flex 子项默认拒绝收缩，显式归零是错误省略号生效的前提
        flexShrink: 1, // 窄窗时状态区先收缩，保证右侧按钮始终完整
        paddingLeft: "12px",
        marginLeft: "4px",
        borderLeft: `2px solid ${t.divider}`, // 左侧竖线分组：范例库视觉独立区域
      }}
    >
      <style>{panelCss(t)}</style>

      {/* 折叠钮：默认收起。展开态由 aria-expanded + 箭头朝向 + 深色标签三重标识 */}
      <button
        ref={toggleRef}
        type="button"
        className="wfp-example-toggle"
        aria-expanded={expanded}
        aria-controls={EXAMPLE_BODY_ID}
        title={expanded ? "收起范例库" : "展开范例库"}
        onClick={() => setExpanded((prev) => !prev)}
      >
        {collectionIcon}
        <span>范例库</span>
        {examples.length > 0 && (
          <span className="wfp-example-count" aria-hidden="true">
            {examples.length}
          </span>
        )}
        <span className="wfp-example-caret" aria-hidden="true">
          {caretIcon}
        </span>
      </button>

      {/* 加载/错误反馈常驻折叠钮之后：收起态同样可见，避免"收起即无反馈" */}
      {loading && (
        <span
          style={{
            fontSize: exampleTokens.fontSize.body,
            color: t.hint,
            lineHeight: 1.4,
            letterSpacing: "0.5px", // 微字距提升提示语精致感
          }}
        >
          加载中…
        </span>
      )}
      {error && (
        <span
          title={error} // 悬浮查看完整错误原文，视觉省略不丢失信息
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "4px",
            fontSize: exampleTokens.fontSize.body,
            color: t.danger, // 红色系警示（spec 4.5.2 现有红色系）
            maxWidth: "320px", // 长错误文本视觉省略，不撑开顶栏布局
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            lineHeight: 1.4,
          }}
        >
          {warningIcon}
          {error}
        </span>
      )}

      {/* 展开体：display: contents 使按钮回到根 flex 换行流，排布与折叠前一致 */}
      <div
        id={EXAMPLE_BODY_ID}
        className="wfp-example-body"
        data-open={expanded ? "true" : "false"}
        role="group"
        aria-label="范例列表"
      >
        {examples.map((example) => (
          <button
            key={example.example_id}
            className="wfp-topbar-btn wfp-topbar-btn--sm"
            onClick={() => void handleLoad(example.example_id)}
            disabled={!example.available}
            title={
              example.available
                ? `加载范例: ${example.name}`
                : `不可用: ${example.unavailable_reason ?? "未知原因"}${
                    example.missing_dependencies.length > 0
                      ? `（缺少配置: ${example.missing_dependencies.join(", ")}）`
                      : ""
                  }`
            }
            style={{
              whiteSpace: "nowrap", // 按钮文字保持单行，以完整单元参与换行
              flexShrink: 0, // 按钮不被压缩，保证整块换行而非文字截断
              fontSize: "12px",
              padding: "2px 10px",
              borderRadius: "10px", // 胶囊圆角：与矩形导航按钮区分"可加载标签"语义
              opacity: example.available ? 1 : 0.5,
              cursor: example.available ? "pointer" : "not-allowed",
              textDecoration: example.available ? "none" : "line-through", // 不可用删除线强化禁用语义
            }}
          >
            {example.name}
          </button>
        ))}
      </div>
    </div>
  );
}
