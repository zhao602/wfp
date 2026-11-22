// =============================================================================
// OEM 品牌配置（src/brand/brandConfig.ts，roadmap B5）
//
// 职责：构建期品牌单一来源——产品名 + 主色 10 阶 + 2 组 RGB 推导。
// 机制：模块加载期读 import.meta.env.VITE_OEM_*（Vite 5 自动透传进程
// VITE_*；由 scripts/build_oem.py 构建期注入固化，非运行时切换）。
// 默认值=自建平台现状值（逐字节一致）→ dev 模式与既有测试零行为变化。
// =============================================================================

/** env 值缺失/空白时回落默认值（OEM 缺省=自建平台现状） */
function orDefault(value: string | undefined, fallback: string): string {
  const trimmed = (value ?? "").trim();
  return trimmed ? trimmed : fallback;
}

/** hex 颜色转 CSS rgba() 的 "r, g, b" 参数串（"#1976d2" → "25, 118, 210"） */
function hexToRgb(hex: string): string {
  const value = hex.replace("#", "");
  return `${parseInt(value.slice(0, 2), 16)}, ${parseInt(value.slice(2, 4), 16)}, ${parseInt(value.slice(4, 6), 16)}`;
}

const env = import.meta.env;

/** 产品名：顶栏标题（<title> 由构建脚本改写产物，非运行时） */
export const PRODUCT_NAME: string = orDefault(env.VITE_OEM_PRODUCT_NAME, "工作流编排平台");

/** 主色：浅色主按钮背景 / 管理后台标题 dot 渐变起点 / 亮态激活描边 */
export const PRIMARY_COLOR: string = orDefault(env.VITE_OEM_PRIMARY_COLOR, "#1976d2");

/** 主色悬停：浅色主按钮 hover / 亮态激活文字 */
export const PRIMARY_COLOR_HOVER: string = orDefault(env.VITE_OEM_PRIMARY_COLOR_HOVER, "#1565c0");

/** 主色按压：浅色主按钮 active */
export const PRIMARY_COLOR_ACTIVE: string = orDefault(env.VITE_OEM_PRIMARY_COLOR_ACTIVE, "#0d47a1");

/** 亮阶：深色主按钮背景 / dot 渐变终点 / 暗态激活描边 */
export const PRIMARY_COLOR_LIGHT: string = orDefault(env.VITE_OEM_PRIMARY_COLOR_LIGHT, "#42a5f5");

/** 亮阶悬停：深色主按钮 hover */
export const PRIMARY_COLOR_LIGHT_HOVER: string = orDefault(env.VITE_OEM_PRIMARY_COLOR_LIGHT_HOVER, "#2196f3");

/** 柔阶：暗态激活文字 / 暗态 dot 渐变终点 */
export const PRIMARY_COLOR_SOFT: string = orDefault(env.VITE_OEM_PRIMARY_COLOR_SOFT, "#8ec5fc");

/** 浅底：亮态激活背景 */
export const PRIMARY_COLOR_TINT: string = orDefault(env.VITE_OEM_PRIMARY_COLOR_TINT, "#e8f1fb");

/** 浅底悬停：亮态激活 hover 背景 */
export const PRIMARY_COLOR_TINT_HOVER: string = orDefault(env.VITE_OEM_PRIMARY_COLOR_TINT_HOVER, "#dceaf8");

/** 暗底：暗态激活背景（默认=darkTokens.typeFill.trigger 现值） */
export const PRIMARY_COLOR_DARK_TINT: string = orDefault(env.VITE_OEM_PRIMARY_COLOR_DARK_TINT, "#1a2a3a");

/** 暗底悬停：暗态激活 hover 背景 */
export const PRIMARY_COLOR_DARK_TINT_HOVER: string = orDefault(env.VITE_OEM_PRIMARY_COLOR_DARK_TINT_HOVER, "#213045");

/** 主色 RGB 参数串（"25, 118, 210"）——rgba(${PRIMARY_COLOR_RGB}, α) 用 */
export const PRIMARY_COLOR_RGB: string = hexToRgb(PRIMARY_COLOR);

/** 亮阶 RGB 参数串（"66, 165, 245"）——rgba(${PRIMARY_COLOR_LIGHT_RGB}, α) 用 */
export const PRIMARY_COLOR_LIGHT_RGB: string = hexToRgb(PRIMARY_COLOR_LIGHT);
