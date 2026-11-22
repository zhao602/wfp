interface ImportMetaEnv {
  readonly VITE_SSO_ENABLED?: string;
  /** Vite 模式（development/production/test）——B2-C2 测试预热分支判定 */
  readonly MODE?: string;
  /** OEM 白标（roadmap B5，构建期固化）：产品名（缺省=工作流编排平台） */
  readonly VITE_OEM_PRODUCT_NAME?: string;
  /** OEM 主色 10 阶（#rrggbb；缺省=自建平台蓝色系现状值，scripts/build_oem.py 注入） */
  readonly VITE_OEM_PRIMARY_COLOR?: string;
  readonly VITE_OEM_PRIMARY_COLOR_HOVER?: string;
  readonly VITE_OEM_PRIMARY_COLOR_ACTIVE?: string;
  readonly VITE_OEM_PRIMARY_COLOR_LIGHT?: string;
  readonly VITE_OEM_PRIMARY_COLOR_LIGHT_HOVER?: string;
  readonly VITE_OEM_PRIMARY_COLOR_SOFT?: string;
  readonly VITE_OEM_PRIMARY_COLOR_TINT?: string;
  readonly VITE_OEM_PRIMARY_COLOR_TINT_HOVER?: string;
  readonly VITE_OEM_PRIMARY_COLOR_DARK_TINT?: string;
  readonly VITE_OEM_PRIMARY_COLOR_DARK_TINT_HOVER?: string;
  /** OEM 禁用面板（JSON 数组字符串，如 '["agent_market"]'；无效时 fail-soft 空集） */
  readonly VITE_OEM_DISABLED_PANELS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module "reactflow/dist/style.css";