// =============================================================================
// 统一 API 错误契约单元测试（api/__tests__/apiError.test.ts）
//
// 覆盖（arch_review_gap_b1_hardening AC-B1-2 / INV-B1-3）：
//   1. 五层分层映射：后端 DomainError 样例 → ApiError 层级断言（分层映射单测）；
//   2. 与后端 {code, message, details} 响应结构逐字段核对（S：
//      domain/errors.py DomainError.to_dict + application_factory 异常处理器
//      422/409/404/500 + rbac_authorization_middleware 401 结构）；
//   3. 旧别名兼容：3 份旧异常类 instanceof ApiError / 字段 / name
//      （import 路径零变更，INV-B1-3）。
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  ApiError,
  ApiErrorCodeLayer,
  resolveApiErrorLayer,
  isTenantScopeCode,
} from "../apiError";
import { ApiException } from "../orchestrationApiClient";
import { KbApiException } from "../kbManagementApiClient";
import { GovernanceApiError } from "../../governance/governanceApiClient";

// =============================================================================
// 五层枚举
// =============================================================================

describe("ApiErrorCodeLayer 五层枚举", () => {
  it("五层成员与字符串值锁定（B1-DC-03）", () => {
    expect(ApiErrorCodeLayer.Transport).toBe("transport");
    expect(ApiErrorCodeLayer.Auth).toBe("auth");
    expect(ApiErrorCodeLayer.Tenant).toBe("tenant");
    expect(ApiErrorCodeLayer.Business).toBe("business");
    expect(ApiErrorCodeLayer.Validation).toBe("validation");
    expect(Object.keys(ApiErrorCodeLayer)).toHaveLength(5);
  });
});

// =============================================================================
// 分层映射（后端 DomainError 样例 → ApiError 层级断言）
// =============================================================================

describe("分层映射：后端 DomainError 样例 → ApiError 层级", () => {
  it("WorkflowValidationError → 422 → validation（application_factory 异常处理器）", () => {
    const e = ApiError.fromDomainError(
      { code: "WF_YAML_SYNTAX_ERROR", message: "YAML 语法错误", details: { line: 3 } },
      422,
    );
    expect(e.layer).toBe(ApiErrorCodeLayer.Validation);
  });

  it("ResourceNotFoundError → 404 → business", () => {
    const e = ApiError.fromDomainError(
      { code: "WF_NOT_FOUND", message: "工作流定义不存在", details: {} },
      404,
    );
    expect(e.layer).toBe(ApiErrorCodeLayer.Business);
  });

  it("InstanceStateConflictError → 409 → business", () => {
    const e = ApiError.fromDomainError(
      { code: "STATE_INSTANCE_RUNNING", message: "运行中实例禁止回滚", details: {} },
      409,
    );
    expect(e.layer).toBe(ApiErrorCodeLayer.Business);
  });

  it("DomainError 兜底处理器 → 500 → transport", () => {
    const e = ApiError.fromDomainError(
      { code: "WF_STATE_STORE_UNAVAILABLE", message: "状态存储不可用", details: {} },
      500,
    );
    expect(e.layer).toBe(ApiErrorCodeLayer.Transport);
  });

  it("鉴权 401/403 → auth（rbac_authorization_middleware 401 体 {code,message,details}）", () => {
    expect(
      ApiError.fromDomainError(
        { code: "AUTH_TOKEN_MISSING", message: "未提供访问令牌", details: {} },
        401,
      ).layer,
    ).toBe(ApiErrorCodeLayer.Auth);
    expect(resolveApiErrorLayer(403, "AUTH_TOKEN_INVALID")).toBe(
      ApiErrorCodeLayer.Auth,
    );
  });

  it("租户域错误码（TENANT_ 前缀，预留规则）→ tenant", () => {
    expect(resolveApiErrorLayer(429, "TENANT_QUOTA_EXCEEDED")).toBe(
      ApiErrorCodeLayer.Tenant,
    );
    expect(isTenantScopeCode("TENANT_QUOTA_EXCEEDED")).toBe(true);
    expect(isTenantScopeCode("WF_NOT_FOUND")).toBe(false);
  });

  it("无状态码 → transport（构造器默认 transport 层语义）", () => {
    expect(resolveApiErrorLayer(undefined, "UNKNOWN_ERROR")).toBe(
      ApiErrorCodeLayer.Transport,
    );
  });

  it("显式 layer 参数覆盖推导", () => {
    const e = new ApiError("X", "消息", {}, 404, ApiErrorCodeLayer.Tenant);
    expect(e.layer).toBe(ApiErrorCodeLayer.Tenant);
  });
});

// =============================================================================
// 与后端 {code, message, details} 响应结构逐字段核对（S）
// =============================================================================

describe("与后端 {code, message, details} 响应结构逐字段核对", () => {
  it("fromDomainError 字段逐一对齐 DomainError.to_dict()", () => {
    // domain/errors.py: to_dict() → {"code": self.code, "message": self.message, "details": self.details}
    const e = ApiError.fromDomainError(
      {
        code: "WF_NOT_FOUND",
        message: "工作流定义不存在",
        details: { definition_id: "wf-demo" },
      },
      404,
    );
    expect(e.code).toBe("WF_NOT_FOUND");
    expect(e.message).toBe("工作流定义不存在");
    expect(e.details).toEqual({ definition_id: "wf-demo" });
    expect(e.httpStatus).toBe(404);
  });

  it("ApiError 基类默认值：details 空对象、httpStatus 未定义、name=ApiError", () => {
    const e = new ApiError("UNKNOWN_ERROR", "网络错误");
    expect(e instanceof Error).toBe(true);
    expect(e.name).toBe("ApiError");
    expect(e.details).toEqual({});
    expect(e.httpStatus).toBeUndefined();
    expect(e.layer).toBe(ApiErrorCodeLayer.Transport);
  });
});

// =============================================================================
// 旧别名兼容（INV-B1-3：import 路径零变更，instanceof 兼容）
// =============================================================================

describe("旧别名兼容（3 份旧异常类为 ApiError 族薄包装）", () => {
  it("ApiException（api/orchestrationApiClient 原路径导出）", () => {
    const e = new ApiException("DELETE_FAILED", "删除订阅失败", {}, 409);
    expect(e instanceof ApiError).toBe(true);
    expect(e instanceof Error).toBe(true);
    expect(e.name).toBe("ApiException");
    expect(e.code).toBe("DELETE_FAILED");
    expect(e.details).toEqual({});
    expect(e.httpStatus).toBe(409);
    expect(e.layer).toBe(ApiErrorCodeLayer.Business);
  });

  it("KbApiException（api/kbManagementApiClient 原路径导出）", () => {
    const e = new KbApiException(
      "VECTOR_KB_DRAFT_VERSION_CONFLICT",
      "草稿版本冲突",
      { draft_version: "v1" },
      409,
    );
    expect(e instanceof ApiError).toBe(true);
    expect(e.name).toBe("KbApiException");
    expect(e.code).toBe("VECTOR_KB_DRAFT_VERSION_CONFLICT");
    expect(e.details).toEqual({ draft_version: "v1" });
    expect(e.httpStatus).toBe(409);
    expect(e.layer).toBe(ApiErrorCodeLayer.Business);
  });

  it("GovernanceApiError（governance/governanceApiClient 原路径导出）3 参构造兼容", () => {
    const three = new GovernanceApiError("AUTH_TOKEN_INVALID", "令牌无效");
    expect(three instanceof ApiError).toBe(true);
    expect(three.name).toBe("GovernanceApiError");
    expect(three.details).toEqual({});
    expect(three.httpStatus).toBeUndefined();
    expect(three.layer).toBe(ApiErrorCodeLayer.Transport);
  });

  it("GovernanceApiError 4 参构造携带 httpStatus 时分层随状态码推导", () => {
    const four = new GovernanceApiError("AUTH_TOKEN_INVALID", "令牌无效", {}, 401);
    expect(four.httpStatus).toBe(401);
    expect(four.layer).toBe(ApiErrorCodeLayer.Auth);
  });

  it("4 参构造先例（topbarVisual：code, message, details, httpStatus）字段全保留", () => {
    const e = new ApiException("internal_error", "HTTP 500: internal error", {}, 500);
    expect(e.layer).toBe(ApiErrorCodeLayer.Transport);
  });
});
