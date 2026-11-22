// =============================================================================
// 插件节点类型定义（web/src/plugin_nodes/pluginNodeTypes.ts）
//
// 与后端 Pydantic 模型一一对应（TypeScript 严格模式）
// =============================================================================

/** 插件 manifest */
export interface PluginManifest {
  node_name: string;
  version: string;
  description: string;
  input_schema: Record<string, string>;
  output_schema: Record<string, string>;
  dependencies: string[];
  declared_file_paths: string[];
  declared_network_targets: string[];
  entry_point: string;
}

/** 插件节点注册信息 */
export interface PluginNodeRegistration {
  node_name: string;
  version: string;
  manifest: PluginManifest;
  healthy: boolean;
  registered_at: string;
  instance_count: number;
}

/** 校验结果 */
export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

/** 安装响应 */
export interface InstallResponse {
  registration: PluginNodeRegistration | null;
  validation: ValidationResult;
  error_code: string | null;
  error_message: string | null;
  error_detail: Record<string, unknown> | null;
}

/** 卸载响应 */
export interface UninstallResponse {
  node_name: string;
  success: boolean;
}

/** 节点列表响应 */
export interface PluginNodeListResponse {
  nodes: PluginNodeRegistration[];
  total: number;
}
