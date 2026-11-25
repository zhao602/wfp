// =============================================================================
// 管理后台根组件（src/admin/AdminConsole.tsx）
//
// 职责：聚合 phase3/phase4 全部治理与管理面板，提供统一侧边栏导航。
// 设计意图：
//   1. 将分散的治理/管理面板聚合为单一可达入口，消除"孤岛组件"；
//   2. 通过侧边栏菜单切换面板，无需引入 React Router；
//   3. 租户/用户上下文从 localStorage 读取，用默认值兜底。
// =============================================================================

import { useState, type JSX } from 'react';

import { useThemeMode, darkTokens, THEME_ROOT_CLASS } from "../theme/themeMode";
import { AdminErrorBoundary } from './AdminErrorBoundary';
import { MENU_ITEMS, MENU_GROUPS, type MenuKey } from './menuConfig';
import {
  PRIMARY_COLOR,
  PRIMARY_COLOR_HOVER,
  PRIMARY_COLOR_LIGHT,
  PRIMARY_COLOR_SOFT,
  PRIMARY_COLOR_RGB,
  PRIMARY_COLOR_LIGHT_RGB,
  PRIMARY_COLOR_TINT,
  PRIMARY_COLOR_TINT_HOVER,
  PRIMARY_COLOR_DARK_TINT,
  PRIMARY_COLOR_DARK_TINT_HOVER,
} from '../brand/brandConfig';
import { createLazySlot, loadLazySlot, LazyPanel, type LazySlot } from './panelLazy';
import type { AgentTemplate } from '../market/marketTypes';

// -----------------------------------------------------------------------------
// 上下文：从 localStorage 读取租户/用户信息，用默认值兜底
// -----------------------------------------------------------------------------

/** 模块内私有辅助：安全读取 localStorage，异常静默回落默认值（DC-8/DC-9，对齐 themeMode.ts readStoredTheme 范式） */
function readStoredString(key: string, fallback: string): string {
  try {
    const v = localStorage.getItem(key);
    return v ?? fallback;
  } catch {
    return fallback;
  }
}

const DEFAULT_TENANT_ID = readStoredString('tenantId', 'default-tenant');
const DEFAULT_TOKEN = readStoredString('authToken', '');
const DEFAULT_USER_ID = readStoredString('userId', 'admin');
const DEFAULT_PERMISSIONS: string[] = ['*'];

// -----------------------------------------------------------------------------
// 20 面板惰性加载槽（B2-C2/DC-B2-02）：每面板动态 import 一 chunk，静态导入
// 全部移除（首屏主 chunk 不含面板字节）；面板 props 契约经 render 回调在
// renderPanel 调用点逐一类型核对（GK-14 提取时 18 项 id 零变化；B16 增
// approval_queue / subscription_management 槽）。MENU_ITEMS 20 项。
// -----------------------------------------------------------------------------

const governanceSlot = createLazySlot(() =>
  import("../governance/GovernanceDashboard").then((m) => m.GovernanceDashboard),
);
const abacSlot = createLazySlot(() =>
  import("../governance/AbacPolicyPanel").then((m) => m.AbacPolicyPanel),
);
const tombstoneSlot = createLazySlot(() =>
  import("../governance/TombstonePanel").then((m) => m.TombstonePanel),
);
const nlOrchestrationSlot = createLazySlot(() =>
  import("../nl_orchestration/NLOrchestrationPanel").then((m) => m.NLOrchestrationPanel),
);
const sandboxSlot = createLazySlot(() =>
  import("../sandbox/SandboxPolicyPanel").then((m) => m.SandboxPolicyPanel),
);
const ssoSlot = createLazySlot(() =>
  import("../sso/SsoSettingsPanel").then((m) => m.SsoSettingsPanel),
);
const evaluationSlot = createLazySlot(() =>
  import("../evaluation/EvaluationDashboard").then((m) => m.EvaluationDashboard),
);
const agentDiscoverySlot = createLazySlot(() =>
  import("../agent_discovery/AgentDiscoveryPanel").then((m) => m.AgentDiscoveryPanel),
);
const pluginNodesSlot = createLazySlot(() =>
  import("../plugin_nodes/PluginNodeManager").then((m) => m.PluginNodeManager),
);
const contributionSubmitSlot = createLazySlot(() =>
  import("../market/ContributionSubmitWizard").then((m) => m.ContributionSubmitWizard),
);
const contributionReviewSlot = createLazySlot(() =>
  import("../market/ContributionReviewBoard").then((m) => m.ContributionReviewBoard),
);
const devSubmitSlot = createLazySlot(() =>
  import("../market/DevSubmitWizard").then((m) => m.DevSubmitWizard),
);
const devReviewSlot = createLazySlot(() =>
  import("../market/DevReviewBoard").then((m) => m.DevReviewBoard),
);
const enterpriseConnectorsSlot = createLazySlot(() =>
  import("../enterprise_connectors/EnterpriseConnectorPanel").then((m) => m.EnterpriseConnectorPanel),
);
const agentPerformanceSlot = createLazySlot(() =>
  import("../agent_performance/AgentPerformancePanel").then((m) => m.AgentPerformancePanel),
);
const agentMarketSlot = createLazySlot(() =>
  import("../market/AgentMarketPanel").then((m) => m.AgentMarketPanel),
);
const toolMarketSlot = createLazySlot(() =>
  import("../market/ToolMarketPanel").then((m) => m.ToolMarketPanel),
);
const knowledgeBaseSlot = createLazySlot(() =>
  import("../knowledge_base/KnowledgeBasePanel").then((m) => m.KnowledgeBasePanel),
);
const approvalQueueSlot = createLazySlot(() =>
  import("../approval-queue/ApprovalQueuePanel").then((m) => m.ApprovalQueuePanel),
);
const subscriptionManagementSlot = createLazySlot(() =>
  import("../subscription-management/SubscriptionManagementPanel").then((m) => m.SubscriptionManagementPanel),
);

const ALL_PANEL_SLOTS: Array<LazySlot<unknown>> = [
  governanceSlot,
  abacSlot,
  tombstoneSlot,
  nlOrchestrationSlot,
  sandboxSlot,
  ssoSlot,
  evaluationSlot,
  agentDiscoverySlot,
  pluginNodesSlot,
  contributionSubmitSlot,
  contributionReviewSlot,
  enterpriseConnectorsSlot,
  agentPerformanceSlot,
  agentMarketSlot,
  toolMarketSlot,
  knowledgeBaseSlot,
  approvalQueueSlot,
  subscriptionManagementSlot,
  devSubmitSlot,
  devReviewSlot,
];

// 测试环境模块作用域预热（B2-C2）：20 槽即刻 kick——mock/已缓存模块在 import 期
// 微任务内解析，使既有同步断言（AdminConsole.market.test TC-ACM-02/03/04）首渲
// 即命中 resolved 槽。生产构建该分支被静态替换剔除（"production" === "test" 恒
// 假，死代码消除），运行时零开销；生产按需 kick，与测试同一代码路径，仅数据
// 时序差异。
if (import.meta.env.MODE === "test") {
  ALL_PANEL_SLOTS.forEach((slot) => {
    loadLazySlot(slot);
  });
}

// -----------------------------------------------------------------------------
// 菜单定义：MenuKey/MenuItem/MENU_ITEMS/MENU_GROUPS 已提取至 menuConfig.ts
// （B2-C1 单一来源共享——router 运行时读 MENU_ITEMS 防漂移；GK-14 提取时
// 18 项 id 零变化；B16 增「运营与审批」组 approval_queue / subscription_management）
// -----------------------------------------------------------------------------

// -----------------------------------------------------------------------------
// 面板渲染器
// -----------------------------------------------------------------------------

/** 应用 Agent 模板回调类型（agent_tool_marketplace DC-06/T15.2） */
type OnApplyTemplate = (template: AgentTemplate, targetNodeId?: string | null) => void;

function renderPanel(key: MenuKey, onApplyTemplate?: OnApplyTemplate): JSX.Element {
  switch (key) {
    case 'governance':
      return <LazyPanel slot={governanceSlot} render={(Panel) => <Panel />} />;
    case 'abac':
      return (
        <LazyPanel
          slot={abacSlot}
          render={(Panel) => <Panel token={DEFAULT_TOKEN} tenantId={DEFAULT_TENANT_ID} />}
        />
      );
    case 'tombstone':
      return (
        <LazyPanel
          slot={tombstoneSlot}
          render={(Panel) => <Panel token={DEFAULT_TOKEN} tenantId={DEFAULT_TENANT_ID} />}
        />
      );
    case 'nl_orchestration':
      return (
        <LazyPanel
          slot={nlOrchestrationSlot}
          render={(Panel) => (
            <Panel token={DEFAULT_TOKEN} tenantId={DEFAULT_TENANT_ID} userId={DEFAULT_USER_ID} />
          )}
        />
      );
    case 'sandbox':
      return (
        <LazyPanel
          slot={sandboxSlot}
          render={(Panel) => <Panel tenantId={DEFAULT_TENANT_ID} />}
        />
      );
    case 'sso':
      return (
        <LazyPanel
          slot={ssoSlot}
          render={(Panel) => <Panel tenantId={DEFAULT_TENANT_ID} />}
        />
      );
    case 'evaluation':
      return (
        <LazyPanel
          slot={evaluationSlot}
          render={(Panel) => <Panel tenantId={DEFAULT_TENANT_ID} />}
        />
      );
    case 'agent_discovery':
      return (
        <LazyPanel
          slot={agentDiscoverySlot}
          render={(Panel) => (
            <Panel
              userId={DEFAULT_USER_ID}
              tenantId={DEFAULT_TENANT_ID}
              permissions={DEFAULT_PERMISSIONS}
            />
          )}
        />
      );
    case 'plugin_nodes':
      return <LazyPanel slot={pluginNodesSlot} render={(Panel) => <Panel />} />;
    case 'contribution_submit':
      return (
        <LazyPanel
          slot={contributionSubmitSlot}
          render={(Panel) => <Panel contributorId={DEFAULT_USER_ID} />}
        />
      );
    case 'contribution_review':
      return (
        <LazyPanel
          slot={contributionReviewSlot}
          render={(Panel) => <Panel operatorId={DEFAULT_USER_ID} />}
        />
      );
    case 'enterprise_connectors':
      return (
        <LazyPanel
          slot={enterpriseConnectorsSlot}
          render={(Panel) => <Panel tenantId={DEFAULT_TENANT_ID} />}
        />
      );
    case 'agent_performance':
      return <LazyPanel slot={agentPerformanceSlot} render={(Panel) => <Panel />} />;
    case 'agent_market':
      return (
        <LazyPanel
          slot={agentMarketSlot}
          render={(Panel) => <Panel onApplyTemplate={onApplyTemplate} />}
        />
      );
    case 'tool_market':
      return <LazyPanel slot={toolMarketSlot} render={(Panel) => <Panel />} />;
    case 'knowledge_base':
      return <LazyPanel slot={knowledgeBaseSlot} render={(Panel) => <Panel />} />;
    case 'approval_queue':
      return (
        <LazyPanel
          slot={approvalQueueSlot}
          render={(Panel) => <Panel actorId={DEFAULT_USER_ID} />}
        />
      );
    case 'subscription_management':
      return <LazyPanel slot={subscriptionManagementSlot} render={(Panel) => <Panel />} />;
    case 'dev_submit':
      return (
        <LazyPanel
          slot={devSubmitSlot}
          render={(Panel) => <Panel developerId={DEFAULT_USER_ID} />}
        />
      );
    case 'dev_review':
      return (
        <LazyPanel
          slot={devReviewSlot}
          render={(Panel) => <Panel operatorId={DEFAULT_USER_ID} />}
        />
      );
  }
}

// -----------------------------------------------------------------------------
// 管理后台根组件
// -----------------------------------------------------------------------------

/**
 * 管理后台深色皮肤 + 侧边导航样式注入块。
 * 浅色下返回按钮无规则=原生外观零回归；深色下补皮肤（spec 4.5.2）。
 * 导航样式浅深双轨，Linear Aesthetic 风格：左侧指示条微发光、圆头标签、细滚动条。
 */
const adminThemeCss = `
.wfp-adm-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.5px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: linear-gradient(135deg, #1a1a1a 0%, #424242 50%, #2a2a2a 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  position: relative;
  filter: drop-shadow(0 1px 1px rgba(0, 0, 0, 0.06));
}
.wfp-adm-title__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: linear-gradient(135deg, ${PRIMARY_COLOR}, ${PRIMARY_COLOR_LIGHT});
  box-shadow: 0 0 6px rgba(${PRIMARY_COLOR_RGB}, 0.35);
  animation: wfp-adm-breathe 2.4s ease-in-out infinite;
  flex-shrink: 0;
  position: relative;
}
.wfp-adm-title__dot::after {
  content: "";
  position: absolute;
  inset: -2px;
  border-radius: 50%;
  border: 1.5px solid rgba(${PRIMARY_COLOR_RGB}, 0.35);
  animation: wfp-adm-ping 2.4s ease-out infinite;
  pointer-events: none;
}
@keyframes wfp-adm-ping {
  0% { transform: scale(0.8); opacity: 0.5; }
  70% { opacity: 0; }
  100% { transform: scale(2.6); opacity: 0; }
}
@keyframes wfp-adm-breathe {
  0%, 100% { opacity: 1; box-shadow: 0 0 6px rgba(${PRIMARY_COLOR_RGB}, 0.35); }
  50% { opacity: 0.85; box-shadow: 0 0 10px rgba(${PRIMARY_COLOR_RGB}, 0.5); }
}

.wfp-theme--dark .wfp-adm-title {
  background: linear-gradient(135deg, ${darkTokens.text.primary} 0%, ${darkTokens.text.secondary} 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.3));
}
.wfp-theme--dark .wfp-adm-title__dot {
  background: linear-gradient(135deg, ${PRIMARY_COLOR_LIGHT}, ${PRIMARY_COLOR_SOFT});
  box-shadow: 0 0 8px rgba(${PRIMARY_COLOR_LIGHT_RGB}, 0.4);
}
.wfp-theme--dark .wfp-adm-title__dot::after {
  border-color: rgba(${PRIMARY_COLOR_LIGHT_RGB}, 0.4);
}

.wfp-theme--dark .wfp-admin-back-btn {
  background: ${darkTokens.surface.bgHover};
  color: ${darkTokens.text.primary};
  border: 1px solid ${darkTokens.surface.border};
  border-radius: 6px;
  padding: 5px 14px;
  cursor: pointer;
}

.wfp-adm-nav {
  width: 224px;
  border-right: 1px solid #e0e0e0;
  overflow-y: auto;
  flex-shrink: 0;
  padding: 6px 0 14px;
}
.wfp-adm-nav::-webkit-scrollbar { width: 5px; }
.wfp-adm-nav::-webkit-scrollbar-track { background: transparent; }
.wfp-adm-nav::-webkit-scrollbar-thumb { background: #d8d8d8; border-radius: 3px; }
.wfp-adm-nav::-webkit-scrollbar-thumb:hover { background: #bdbdbd; }

.wfp-adm-group { padding: 6px 0 8px; }
.wfp-adm-group:not(:last-child) { border-bottom: 1px solid #f0f0f0; }

.wfp-adm-group-label {
  padding: 6px 14px 6px;
  font-size: 10px;
  font-weight: 600;
  color: #9e9e9e;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  user-select: none;
}

.wfp-adm-item {
  display: block;
  padding: 7px 12px 7px 12px;
  margin: 1px 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 400;
  color: #555;
  border-left: 2px solid transparent;
  border-radius: 0 6px 6px 0;
  transition: background-color 120ms ease, color 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
  user-select: none;
  position: relative;
}
.wfp-adm-item:hover {
  background: #f5f5f5;
  color: #1a1a1a;
}
.wfp-adm-item--active {
  background: ${PRIMARY_COLOR_TINT};
  color: ${PRIMARY_COLOR_HOVER};
  font-weight: 500;
  border-left-color: ${PRIMARY_COLOR};
  border-left-width: 3px;
  box-shadow: -3px 0 10px -3px rgba(${PRIMARY_COLOR_RGB}, 0.3);
}
.wfp-adm-item--active:hover {
  background: ${PRIMARY_COLOR_TINT_HOVER};
}

.wfp-theme--dark .wfp-adm-nav { border-right-color: ${darkTokens.surface.divider}; }
.wfp-theme--dark .wfp-adm-nav::-webkit-scrollbar-thumb { background: ${darkTokens.surface.border}; }
.wfp-theme--dark .wfp-adm-nav::-webkit-scrollbar-thumb:hover { background: ${darkTokens.surface.borderStrong}; }
.wfp-theme--dark .wfp-adm-group:not(:last-child) { border-bottom-color: ${darkTokens.surface.divider}; }
.wfp-theme--dark .wfp-adm-group-label { color: ${darkTokens.text.hint}; }
.wfp-theme--dark .wfp-adm-item { color: ${darkTokens.text.secondary}; }
.wfp-theme--dark .wfp-adm-item:hover { background: ${darkTokens.surface.bgHover}; color: ${darkTokens.text.primary}; }
.wfp-theme--dark .wfp-adm-item--active {
  background: ${PRIMARY_COLOR_DARK_TINT};
  color: ${PRIMARY_COLOR_SOFT};
  border-left-color: ${PRIMARY_COLOR_LIGHT};
  border-left-width: 3px;
  box-shadow: -3px 0 12px -3px rgba(${PRIMARY_COLOR_LIGHT_RGB}, 0.4);
}
.wfp-theme--dark .wfp-adm-item--active:hover { background: ${PRIMARY_COLOR_DARK_TINT_HOVER}; }
`;

export interface AdminConsoleProps {
  /** 返回画布编辑器的回调。 */
  onBackToCanvas: () => void;
  /** 应用 Agent 模板回调（可选，agent_tool_marketplace DC-06/T15.2）；未注入时面板降级复制预设 JSON。 */
  onApplyTemplate?: OnApplyTemplate;
  /** 路由驱动的当前面板（可选，B2-C1/DC-07）；未注入时保持内部状态自治（旧行为零变化）。 */
  activePanel?: MenuKey;
  /** 面板切换回调（可选，B2-C1/DC-07）；注入时侧边栏点击经此回调（由宿主更新 hash），内部状态不自持。 */
  onPanelChange?: (panelId: MenuKey) => void;
  /** 404 兜底段（可选，B2-C1/DC-07）；注入时主区渲染 404 提示而非面板。 */
  notFoundSegment?: string;
}

/** 404 兜底面板（B2-C1/DC-07：未知路由段——AdminConsole 内 404 展示 + 返回画布链接） */
function AdminNotFound({ segment, onBackToCanvas }: { segment: string; onBackToCanvas: () => void }): JSX.Element {
  return (
    <div style={{ padding: '48px 16px', textAlign: 'center' }}>
      <h2 style={{ fontSize: '18px', fontWeight: 600, margin: '0 0 8px' }}>404 · 未找到对应页面</h2>
      <p style={{ fontSize: '13px', margin: '0 0 16px', opacity: 0.75 }}>
        路由段 <code>{segment}</code> 不存在，请从左侧菜单选择目标面板，或
      </p>
      <button className="wfp-admin-back-btn" onClick={onBackToCanvas}>
        ← 返回画布编辑器
      </button>
    </div>
  );
}

export function AdminConsole({
  onBackToCanvas,
  onApplyTemplate,
  activePanel,
  onPanelChange,
  notFoundSegment,
}: AdminConsoleProps): JSX.Element {
  const [internalActiveKey, setInternalActiveKey] = useState<MenuKey>('governance');
  const activeKey = activePanel ?? internalActiveKey;
  const handleMenuClick = (key: MenuKey): void => {
    if (onPanelChange) {
      onPanelChange(key);
    } else {
      setInternalActiveKey(key);
    }
  };
  const themeMode = useThemeMode(); const isDark = themeMode === "dark";


  return (
    <div
      className={isDark ? THEME_ROOT_CLASS : undefined}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        backgroundColor: isDark ? darkTokens.surface.bg : undefined,
        color: isDark ? darkTokens.text.primary : undefined,
      }}
    >
      <style>{adminThemeCss}</style>
      <header
        style={{
          padding: '8px 16px',
          borderBottom: `1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}`,
          display: 'flex',
          alignItems: 'center',
          gap: '16px',
        }}
      >
        <h1 className="wfp-adm-title">
          <span className="wfp-adm-title__dot" />
          管理后台
        </h1>
        <button
          className="wfp-admin-back-btn"
          onClick={onBackToCanvas}
          style={{ marginLeft: 'auto', fontSize: '13px' }}
        >
          ← 返回画布编辑器
        </button>
      </header>
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <nav className="wfp-adm-nav">
          {MENU_GROUPS.map((group) => (
            <div key={group} className="wfp-adm-group">
              <div className="wfp-adm-group-label">{group}</div>
              {MENU_ITEMS.filter((item) => item.group === group).map((item) => (
                <div
                  key={item.key}
                  className={`wfp-adm-item${activeKey === item.key ? ' wfp-adm-item--active' : ''}`}
                  onClick={() => handleMenuClick(item.key)}
                >
                  {item.label}
                </div>
              ))}
            </div>
          ))}
        </nav>
        <main style={{ flex: 1, overflow: 'auto', padding: '16px', backgroundColor: isDark ? darkTokens.surface.bg : undefined }}>
          <AdminErrorBoundary>
            {notFoundSegment !== undefined ? (
              <AdminNotFound segment={notFoundSegment} onBackToCanvas={onBackToCanvas} />
            ) : (
              renderPanel(activeKey, onApplyTemplate)
            )}
          </AdminErrorBoundary>
        </main>
      </div>
    </div>
  );
}
