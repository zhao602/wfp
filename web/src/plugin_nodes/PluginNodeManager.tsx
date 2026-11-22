// =============================================================================
// 插件节点管理面板（web/src/plugin_nodes/PluginNodeManager.tsx）
// =============================================================================

import { useEffect, useState, useCallback, useRef } from 'react';
import type {
  PluginNodeRegistration,
  InstallResponse,
} from './pluginNodeTypes';
import {
  listPluginNodes,
  installPluginNode,
  uninstallPluginNode,
} from './pluginNodeApiClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';

/** 插件节点管理面板 */
export function PluginNodeManager(): JSX.Element {
  const [nodes, setNodes] = useState<PluginNodeRegistration[]>([]);
  const [loading, setLoading] = useState(false);
  const [installResult, setInstallResult] = useState<InstallResponse | null>(null);
  const [uninstallTarget, setUninstallTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const loadNodes = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listPluginNodes();
      setNodes(response.nodes);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadNodes();
  }, [loadNodes]);

  const handleFileChange = (file: File | undefined) => {
    if (file && file.name.endsWith(".zip")) {
      setSelectedFileName(file.name);
      setError(null);
    }
  };

  const handleDropAreaClick = () => {
    fileInputRef.current?.click();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) {
      const dt = new DataTransfer();
      dt.items.add(file);
      if (fileInputRef.current) {
        fileInputRef.current.files = dt.files;
      }
      handleFileChange(file);
    }
  };

  const handleInstall = async () => {
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    setError(null);
    setInstallResult(null);
    try {
      const result = await installPluginNode(file);
      setInstallResult(result);
      if (result.registration) {
        loadNodes();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '安装失败');
    }
  };

  const handleUninstallConfirm = async () => {
    if (!uninstallTarget) return;
    setError(null);
    try {
      await uninstallPluginNode(uninstallTarget);
      setUninstallTarget(null);
      loadNodes();
    } catch (err) {
      setError(err instanceof Error ? err.message : '卸载失败');
    }
  };

  return (
    <div className="wfp-pnm-panel">
      <style>{`
.wfp-pnm-input{width:100%;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark?darkTokens.text.primary:"#333333"};background-color:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:5px;outline:none;transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-pnm-input:hover{background-color:${isDark?darkTokens.surface.bgHover:"#fafafa"}}
.wfp-pnm-input:focus{border-color:${isDark?darkTokens.surface.borderStrong:"#bdbdbd"}}
.wfp-pnm-input::placeholder{color:${isDark?darkTokens.text.hint:"#b0b0b0"}}
.wfp-pnm-dropzone{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;width:100%;box-sizing:border-box;padding:20px 12px;border:1.5px dashed ${isDark?darkTokens.surface.border:"#c8c8c8"};border-radius:6px;cursor:pointer;transition:border-color 120ms ease,background-color 120ms ease;background-color:${isDark?"transparent":"#fafafa"};}
.wfp-pnm-dropzone:hover{border-color:${isDark?darkTokens.surface.borderStrong:"#9e9e9e"};background-color:${isDark?darkTokens.surface.bgHover:"#f5f5f5"};}
.wfp-pnm-dropzone--active{border-color:${isDark?"#64b5f6":"#1976d2"};background-color:${isDark?"rgba(100,181,246,0.08)":"rgba(25,118,210,0.04)"};}
.wfp-pnm-dropzone__icon{opacity:0.6;transition:opacity 120ms ease;}
.wfp-pnm-dropzone:hover .wfp-pnm-dropzone__icon{opacity:1;}
.wfp-pnm-dropzone__text{font-size:13px;color:${isDark?darkTokens.text.secondary:"#666666"};text-align:center;word-break:break-all;}
.wfp-pnm-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;overflow:hidden;}
.wfp-pnm-table thead th{background:${isDark?darkTokens.surface.bgHover:"#f5f5f5"};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"}}
.wfp-pnm-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#eee"};color:${isDark?darkTokens.text.primary:"#1a1a1a"}}
.wfp-pnm-table tbody tr:last-child td{border-bottom:none}
.wfp-pnm-table tbody tr:hover{background:${isDark?darkTokens.surface.bgHover:"#fafafa"}}
.wfp-pnm-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark?darkTokens.surface.border:"#d0d0d0"};background:${isDark?darkTokens.surface.bg:"#ffffff"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-pnm-btn:hover{background:${isDark?darkTokens.surface.bgHover:"#f5f5f5"};border-color:${isDark?darkTokens.surface.borderStrong:"#b8b8b8"}}
.wfp-pnm-btn:active{background:${isDark?darkTokens.surface.bgActive:"#e8e8e8"}}
.wfp-pnm-btn:disabled{opacity:0.5;cursor:not-allowed}
.wfp-pnm-btn--primary{background:${isDark?darkTokens.surface.bgActive:"#1a1a1a"};color:${isDark?darkTokens.text.primary:"#ffffff"};border-color:${isDark?darkTokens.surface.borderStrong:"#1a1a1a"}}
.wfp-pnm-btn--primary:hover{background:${isDark?darkTokens.surface.bgHover:"#333333"}}
.wfp-pnm-btn--danger{color:${isDark?darkTokens.danger:"#d32f2f"};background:transparent;border-color:${isDark?darkTokens.danger:"#d32f2f"}}
.wfp-pnm-btn--danger:hover{background:${isDark?"rgba(255,107,107,0.12)":"#fdecea"}}
.wfp-pnm-btn--sm{padding:4px 10px;border-radius:4px;font-size:12px;}
.wfp-pnm-panel{padding:20px 24px;background:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-pnm-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-pnm-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-left:3px solid ${isDark?darkTokens.danger:"#d32f2f"};background:${isDark?"rgba(255,107,107,0.08)":"#fdecea"};font-size:12px;color:${isDark?darkTokens.danger:"#d32f2f"};}
.wfp-pnm-form{margin-bottom:20px;padding:16px;background:${isDark?darkTokens.surface.bgHover:"#fafafa"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-pnm-section-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-pnm-form .wfp-pnm-input{margin-bottom:10px;}
.wfp-pnm-section{margin-bottom:20px;}
.wfp-pnm-result{margin-bottom:20px;padding:16px;border-radius:8px;background:${isDark?darkTokens.surface.bgHover:"#fafafa"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};}
.wfp-pnm-result h4{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-pnm-success{border-left:3px solid ${isDark?"#66bb6a":"#4caf50"};}
.wfp-pnm-failure{border-left:3px solid ${isDark?darkTokens.danger:"#d32f2f"};}
.wfp-pnm-result p{font-size:12px;margin:4px 0;color:${isDark?darkTokens.text.secondary:"#616161"};}
.wfp-pnm-detail-list{margin-top:8px;}
.wfp-pnm-detail-list>p{font-size:13px;font-weight:600;margin:0 0 4px;color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-pnm-detail-list ul{list-style:none;padding:0;margin:0;}
.wfp-pnm-detail-list li{font-size:12px;padding:4px 0;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#f0f0f0"};color:${isDark?darkTokens.text.secondary:"#616161"};}
.wfp-pnm-detail-list li:last-child{border-bottom:none;}
.wfp-pnm-healthy{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500;color:#ffffff;background:${isDark?"#66bb6a":"#4caf50"};}
.wfp-pnm-unhealthy{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500;color:#ffffff;background:${isDark?darkTokens.danger:"#d32f2f"};}
.wfp-pnm-empty{text-align:center;padding:24px 16px;color:${isDark?darkTokens.text.hint:"#9e9e9e"};font-size:13px;}
.wfp-pnm-dialog{margin-top:16px;padding:16px;border-radius:8px;background:${isDark?darkTokens.surface.bgHover:"#fafafa"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-left:3px solid ${isDark?"#ffa726":"#ff9800"};}
.wfp-pnm-dialog p{font-size:13px;margin:0 0 12px;color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-pnm-dialog .wfp-pnm-btn{margin-right:8px;}
`}</style>
      <h2 className="wfp-pnm-title">插件节点管理</h2>

      {error && <div className="wfp-pnm-error">{error}</div>}

      {/* 安装区域 */}
      <div className="wfp-pnm-form">
        <h3 className="wfp-pnm-section-title">安装节点包</h3>
        <input
          ref={fileInputRef}
          type="file"
          accept=".zip"
          data-testid="package-file-input"
          style={{ display: "none" }}
          onChange={(e) => handleFileChange(e.target.files?.[0])}
        />
        <div
          className={`wfp-pnm-dropzone${isDragOver ? " wfp-pnm-dropzone--active" : ""}`}
          onClick={handleDropAreaClick}
          onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={handleDrop}
          role="button"
          tabIndex={0}
          aria-label="选择或拖拽 .zip 插件包"
        >
          <svg className="wfp-pnm-dropzone__icon" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={isDark ? darkTokens.text.secondary : "#5a5a5a"} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 3v12 M7 8l5-5 5 5 M5 17v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2" />
          </svg>
          <span className="wfp-pnm-dropzone__text">
            {selectedFileName || "选择 .zip 文件或拖拽到此处"}
          </span>
        </div>
        <button className="wfp-pnm-btn wfp-pnm-btn--primary" onClick={handleInstall} data-testid="install-button" disabled={!selectedFileName}>
          安装
        </button>
      </div>

      {/* 安装结果展示 */}
      {installResult && (
        <div className="wfp-pnm-result" data-testid="install-result">
          <h4>安装结果</h4>
          {installResult.registration ? (
            <div className="wfp-pnm-success">
              <p>安装成功: {installResult.registration.node_name} v{installResult.registration.version}</p>
              <p>健康状态: {installResult.registration.healthy ? '健康' : '不健康'}</p>
            </div>
          ) : (
            <div className="wfp-pnm-failure">
              <p>安装失败: {installResult.error_message}</p>
              {installResult.validation.errors.length > 0 && (
                <div className="wfp-pnm-detail-list">
                  <p>错误详情:</p>
                  <ul>
                    {installResult.validation.errors.map((err, idx) => (
                      <li key={idx}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}
              {installResult.validation.warnings.length > 0 && (
                <div className="wfp-pnm-detail-list">
                  <p>警告:</p>
                  <ul>
                    {installResult.validation.warnings.map((warn, idx) => (
                      <li key={idx}>{warn}</li>
                    ))}
                  </ul>
                </div>
              )}
              {installResult.error_detail &&
                'missing_dependencies' in installResult.error_detail && (
                  <div className="wfp-pnm-detail-list">
                    <p>缺失依赖:</p>
                    <ul>
                      {(installResult.error_detail.missing_dependencies as string[]).map((dep, idx) => (
                        <li key={idx}>{dep}</li>
                      ))}
                    </ul>
                  </div>
                )}
            </div>
          )}
        </div>
      )}

      {/* 已安装节点列表 */}
      <div className="wfp-pnm-section">
        <h3 className="wfp-pnm-section-title">已安装节点 {loading && '(加载中...)'}</h3>
        <table className="wfp-pnm-table" data-testid="node-table">
          <thead>
            <tr>
              <th>节点名称</th>
              <th>版本</th>
              <th>健康标记</th>
              <th>注册时间</th>
              <th>实例数</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {nodes.length === 0 ? (
              <tr><td colSpan={6} className="wfp-pnm-empty">暂无已安装节点</td></tr>
            ) : nodes.map((node) => (
              <tr key={node.node_name}>
                <td>{node.node_name}</td>
                <td>{node.version}</td>
                <td>
                  <span className={node.healthy ? 'wfp-pnm-healthy' : 'wfp-pnm-unhealthy'}>
                    {node.healthy ? '健康' : '不健康'}
                  </span>
                </td>
                <td>{node.registered_at}</td>
                <td>{node.instance_count}</td>
                <td>
                  <button
                    className="wfp-pnm-btn wfp-pnm-btn--danger wfp-pnm-btn--sm"
                    onClick={() => setUninstallTarget(node.node_name)}
                    data-testid={`uninstall-button-${node.node_name}`}
                  >
                    卸载
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 卸载确认对话框 */}
      {uninstallTarget && (
        <div className="wfp-pnm-dialog" data-testid="uninstall-confirm">
          <p>确认卸载节点 {uninstallTarget}？</p>
          <button className="wfp-pnm-btn wfp-pnm-btn--danger" onClick={handleUninstallConfirm}>确认</button>
          <button className="wfp-pnm-btn" onClick={() => setUninstallTarget(null)}>取消</button>
        </div>
      )}
    </div>
  );
}
