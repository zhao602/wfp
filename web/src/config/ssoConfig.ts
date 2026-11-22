// web/src/config/ssoConfig.ts
// SSO 启用状态读取：从 Vite 构建期注入的环境变量解析为布尔值
// 解析规则：value === "true" → true；其余（含 undefined / "false" / 空串 / 非法值）→ false
// 运行时恒定（构建期静态替换），不产生任何网络请求

export function isSsoEnabled(): boolean {
  return import.meta.env.VITE_SSO_ENABLED === "true";
}