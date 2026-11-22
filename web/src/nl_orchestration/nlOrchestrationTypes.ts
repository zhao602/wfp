// =============================================================================
// NL 编排前端类型定义（nlOrchestrationTypes.ts）
//
// 职责：定义自然语言编排模块的 TypeScript 类型，与后端模型一一对应。
// 设计意图（对应偏差修正 spec 5.1 / design 2.2.2.1）：
//   1. TypeScript 严格模式，禁止 any；
//   2. 类型与后端 nl_orchestration/ 模型一一对应。
// =============================================================================

// ----- 枚举类型 -----

export type IntentType = 'create_workflow' | 'modify_workflow';

export type NLErrorCode =
  | 'nl_parse_failed'
  | 'topo_invalid'
  | 'node_not_found'
  | 'injection_detected'
  | 'definition_not_found';

// ----- 请求/响应模型 -----

export interface CreateFromNlRequest {
  directive: string;
  tenant_id: string;
  user_id: string;
}

export interface ModifyFromNlRequest {
  directive: string;
  target_yaml: string;
  tenant_id: string;
  user_id: string;
}

export interface ValidationIssue {
  code: string;
  severity: string;
  message: string;
  node_id?: string | null;
  edge_id?: string | null;
}

export interface NLOrchestrationResponse {
  success: boolean;
  yaml_text?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  validation_issues: ValidationIssue[];
}
