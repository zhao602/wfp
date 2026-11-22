// =============================================================================
// 管理后台菜单配置（src/admin/menuConfig.ts，B2-C1/DC-07 提取）
//
// 职责：MenuKey/MenuItem/ALL_MENU_ITEMS/MENU_ITEMS/MENU_GROUPS 单一来源——自 AdminConsole.tsx
// 原样提取（18 项 id 零变化，GK-14）；router 与 AdminConsole 共享，
// 合法面板 id 集合运行时读 MENU_ITEMS（不复制清单——防漂移，DC-B2-01 ②）。
// OEM 构建期过滤（roadmap B5）：VITE_OEM_DISABLED_PANELS 由 scripts/build_oem.py
// 构建期烘焙禁用面板清单，MENU_ITEMS = 全量减禁用（菜单不渲染 + 深链
// not-found；默认无 env → 18 项零变化）。
// =============================================================================

export type MenuKey =
  | 'governance'
  | 'abac'
  | 'tombstone'
  | 'nl_orchestration'
  | 'sandbox'
  | 'sso'
  | 'evaluation'
  | 'agent_discovery'
  | 'plugin_nodes'
  | 'contribution_submit'
  | 'contribution_review'
  | 'enterprise_connectors'
  | 'agent_performance'
  | 'agent_market'
  | 'tool_market'
  | 'knowledge_base'
  | 'dev_submit'
  | 'dev_review';

export interface MenuItem {
  key: MenuKey;
  label: string;
  group: '画布与编排' | '安全与合规' | '生态与扩展' | '评估与发现';
}

export const ALL_MENU_ITEMS: MenuItem[] = [
  { key: 'governance', label: '治理总览', group: '画布与编排' },
  { key: 'nl_orchestration', label: 'NL 自然语言编排', group: '画布与编排' },
  { key: 'abac', label: 'ABAC 属性策略', group: '安全与合规' },
  { key: 'tombstone', label: '墓碑与降级管理', group: '安全与合规' },
  { key: 'sandbox', label: 'Agent 执行沙箱', group: '安全与合规' },
  { key: 'sso', label: 'SSO 企业身份', group: '安全与合规' },
  { key: 'enterprise_connectors', label: '企业连接器', group: '安全与合规' },
  { key: 'plugin_nodes', label: '插件节点管理', group: '生态与扩展' },
  { key: 'contribution_submit', label: '贡献提交向导', group: '生态与扩展' },
  { key: 'contribution_review', label: '贡献审核台', group: '生态与扩展' },
  { key: 'evaluation', label: 'Agent 效能评估', group: '评估与发现' },
  { key: 'agent_discovery', label: '语义索引 Agent 发现', group: '评估与发现' },
  { key: 'agent_performance', label: 'Agent 绩效聚合', group: '评估与发现' },
  { key: 'agent_market', label: 'Agent 市场', group: '生态与扩展' },
  { key: 'tool_market', label: 'Tool 市场', group: '生态与扩展' },
  { key: 'knowledge_base', label: '向量知识库管理', group: '生态与扩展' },
  { key: 'dev_submit', label: '开发包提交向导', group: '生态与扩展' },
  { key: 'dev_review', label: '开发包审核台', group: '生态与扩展' },
];

/**
 * 解析 OEM 禁用面板清单（roadmap B5，构建期烘焙）。
 * 输入为 VITE_OEM_DISABLED_PANELS 的 JSON 数组字符串（scripts/build_oem.py
 * 构建期注入；dev/测试环境缺省）。
 * Fail-soft：缺失/空白/非法 JSON/非数组 → 空集 + console.warn（菜单全显），
 * 仅构建期保证合法性；非字符串项静默剔除。
 */
export function parseDisabledPanels(raw: string | undefined): ReadonlySet<string> {
  const text = (raw ?? "").trim();
  if (!text) {
    return new Set<string>();
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (!Array.isArray(parsed)) {
      throw new Error("disabled panels must be a JSON array of strings");
    }
    const keys = new Set<string>();
    for (const entry of parsed) {
      if (typeof entry === "string" && entry.trim()) {
        keys.add(entry.trim());
      }
    }
    return keys;
  } catch (error) {
    console.warn(
      "[menuConfig] 无效的 VITE_OEM_DISABLED_PANELS，忽略并显示全部菜单：",
      error instanceof Error ? error.message : error
    );
    return new Set<string>();
  }
}

/** 构建期禁用面板集合（默认无 env → 空集，菜单全显） */
const DISABLED_PANELS: ReadonlySet<string> = parseDisabledPanels(import.meta.env.VITE_OEM_DISABLED_PANELS);

const FILTERED_MENU_ITEMS: MenuItem[] = ALL_MENU_ITEMS.filter((item) => !DISABLED_PANELS.has(item.key));

/**
 * 合法菜单项（OEM 构建期过滤出口，roadmap B5）：
 * 默认无 env → 等于全量 18 项（hashRouter/AdminConsole 零改动语义）；
 * 禁用后过滤为空 → 回落全量（防 hashRouter DEFAULT_ADMIN_PANEL =
 * MENU_ITEMS[0].key 越界；build_oem.py 构建期亦校验剩余 ≥1）。
 */
export const MENU_ITEMS: MenuItem[] =
  FILTERED_MENU_ITEMS.length > 0 ? FILTERED_MENU_ITEMS : ALL_MENU_ITEMS;

export const MENU_GROUPS: MenuItem['group'][] = [
  '画布与编排',
  '安全与合规',
  '生态与扩展',
  '评估与发现',
];
