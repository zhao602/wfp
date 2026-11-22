// =============================================================================
// 统一 API 错误契约（src/api/apiError.ts）
//
// 职责：平台前端统一错误对象 ApiError 与五层分层（arch_review_gap_b1_hardening
// B1-DC-03 / AC-B1-2）。
// 设计意图：
//   1. 与后端 DomainError {code, message, details} 逐字段对齐
//      （domain/errors.py DomainError.to_dict + application_factory 异常处理器
//      + rbac_authorization_middleware 401 结构，均同构）；
//   2. 五层分层 transport/auth/tenant/business/validation，映射终版随
//      分层映射单测落定（api/__tests__/apiError.test.ts）；
//   3. 3 份旧异常类（ApiException/KbApiException/GovernanceApiError）在各原
//      模块内迁移为 ApiError 子类薄包装——import 路径零变更、instanceof 兼容
//      （INV-B1-3）。
// =============================================================================

import type { ApiErrorResponse } from "./apiTypes";

/** API 错误五层分层（B1-DC-03） */
export enum ApiErrorCodeLayer {
  /** 传输层：网络/HTTP 层失败（5xx、无状态码兜底） */
  Transport = "transport",
  /** 鉴权层：401/403 */
  Auth = "auth",
  /** 租户层：租户域错误码（TENANT_ 前缀，预留规则） */
  Tenant = "tenant",
  /** 业务层：其余 4xx 业务错误 */
  Business = "business",
  /** 校验层：422 请求/定义校验失败 */
  Validation = "validation",
}

/** 租户域错误码前缀（预留：后端错误码目录暂无 TENANT_ 前缀码，规则为租户域错误的确定性判定预留） */
const TENANT_SCOPE_CODE_PREFIX = "TENANT_";

/** 判定是否租户域错误码（预留规则：TENANT_ 前缀） */
export function isTenantScopeCode(code: string): boolean {
  return code.startsWith(TENANT_SCOPE_CODE_PREFIX);
}

/**
 * HTTP 状态码 + 错误码 → 分层（映射终版）：
 * TENANT_ 前缀码 → tenant；无状态码 → transport（构造器默认 transport 层语义）；
 * 5xx → transport；401/403 → auth；422 → validation；其余 4xx → business。
 */
export function resolveApiErrorLayer(
  httpStatus: number | undefined,
  code: string,
): ApiErrorCodeLayer {
  if (isTenantScopeCode(code)) {
    return ApiErrorCodeLayer.Tenant;
  }
  if (httpStatus === undefined) {
    return ApiErrorCodeLayer.Transport;
  }
  if (httpStatus >= 500) {
    return ApiErrorCodeLayer.Transport;
  }
  if (httpStatus === 401 || httpStatus === 403) {
    return ApiErrorCodeLayer.Auth;
  }
  if (httpStatus === 422) {
    return ApiErrorCodeLayer.Validation;
  }
  return ApiErrorCodeLayer.Business;
}

/** 统一 API 错误：携带后端结构化错误信息 + 五层分层 */
export class ApiError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly httpStatus?: number;
  readonly layer: ApiErrorCodeLayer;

  constructor(
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    httpStatus?: number,
    layer?: ApiErrorCodeLayer,
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.details = details;
    this.httpStatus = httpStatus;
    this.layer = layer ?? resolveApiErrorLayer(httpStatus, code);
  }

  /**
   * 后端 DomainError {code, message, details} → ApiError。
   * 逐字段对齐 domain/errors.py DomainError.to_dict()；httpStatus 取响应状态码。
   */
  static fromDomainError(
    payload: ApiErrorResponse,
    httpStatus?: number,
  ): ApiError {
    return new ApiError(
      payload.code,
      payload.message,
      payload.details,
      httpStatus,
    );
  }
}
