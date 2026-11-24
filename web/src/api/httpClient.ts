// =============================================================================
// 统一 HTTP 客户端基元（src/api/httpClient.ts）
//
// 职责：15 客户端共用请求管线（arch_review_gap_b1_hardening B1-C2 / design §4.3）。
// 设计意图：
//   1. token 注入：localStorage Bearer（空 token 守卫沿用 fix_empty_bearer_auth_header
//      先例行为——仅非空令牌才携带 Authorization，禁发空 Bearer）；
//   2. 错误映射：非 2xx 统一解析 {code, message, details}（兼容 FastAPI {detail} 字符串
//      与市场/治理族 HTTPException 结构化 detail {detail:{error_code,error_message}}）
//      → 经 errorFactory 构造 ApiError 族；网络失败/超时 → ApiError transport 层兜底；
//   3. 超时沿用各客户端现值（默认不超时）；重试幂等方法限定 + 默认关闭（HC-01 红线）；
//   4. 调用方 signal 取消：裸 AbortError 透传（组件 e.name === "AbortError" 语义保留），
//      不包装为 ApiError；
//   5. 客户端经 errorFactory 注入自有异常子类（ApiException/KbApiException 等，
//      INV-B1-3 instanceof 兼容），传输级错误统一抛 ApiError 基类。
// =============================================================================

import type { ApiErrorResponse } from "./apiTypes";
import { ApiError, ApiErrorCodeLayer } from "./apiError";

/** 统一错误类引用（调用方自 httpClient 导入即可完成错误处理闭环） */
export { ApiError };

/** 重试策略（幂等方法限定；默认关闭，HC-01 行为兼容红线） */
export interface HttpClientRetryOptions {
  enabled: boolean;
  maxAttempts: number;
  idempotentMethodsOnly: boolean;
}

/** 错误工厂：客户端可注入以保留自有异常子类（INV-B1-3）；layer 缺省由 httpStatus/code 推导 */
export type ApiErrorFactory = (
  code: string,
  message: string,
  details: Record<string, unknown>,
  httpStatus: number | undefined,
  layer: ApiErrorCodeLayer | undefined,
) => ApiError;

/** 工厂选项（超时/重试/token/租户头均沿用作客户端现值，对照表登记） */
export interface HttpClientOptions {
  /** 基址前缀（与 path 直拼） */
  baseUrl?: string;
  /** 超时毫秒数（默认不超时，沿用现值） */
  timeoutMs?: number;
  /** 重试策略（默认关闭） */
  retry?: HttpClientRetryOptions;
  /** 访问令牌读取器（默认 localStorage op_access_token，trim 归一 + 容错） */
  getAccessToken?: () => string;
  /** 租户头键名（默认不发租户头；设置后注入 getTenantId() 值） */
  tenantHeaderName?: string;
  /** 租户 ID 读取器（默认 localStorage wfp_tenant_id，缺失回退 "default" + 容错） */
  getTenantId?: () => string;
  /** 错误工厂（默认构造 ApiError） */
  errorFactory?: ApiErrorFactory;
}

/** 单次请求描述（查询串由调用方构造后并入 path） */
export interface HttpClientRequest {
  /** 请求路径（含查询串），与 baseUrl 直拼 */
  path: string;
  /** HTTP 方法（默认 GET） */
  method?: string;
  /** JSON 请求体（FormData 时原样传递不序列化；undefined 时不携带 body） */
  body?: unknown;
  /** 调用方取消信号（abort 裸透传，不包装） */
  signal?: AbortSignal;
  /** 为 true 时 2xx 不解析响应体，resolve undefined */
  voidResponse?: boolean;
  /** 为 true 时不注入 Content-Type（multipart boundary 由运行时生成） */
  multipart?: boolean;
  /** 逐请求头覆写/追加（合并在默认头之上） */
  headers?: Record<string, string>;
}

/** httpClient 实例（导出面最小化：一个 request 方法） */
export interface HttpClient {
  request<T>(request: HttpClientRequest): Promise<T>;
}

/** 幂等方法集（重试限定） */
const IDEMPOTENT_METHODS: readonly string[] = ["GET", "HEAD", "OPTIONS", "PUT", "DELETE"];

/** 访问令牌 localStorage 键 */
const TOKEN_STORAGE_KEY = "op_access_token";

/** 租户 ID localStorage 键 */
const TENANT_STORAGE_KEY = "wfp_tenant_id";

/** 默认访问令牌读取：localStorage + trim 归一 + 容错（隐私模式抛异常视为无令牌）；导出供 SSE 特例复用（★P-04=A token 注入单源） */
export function getDefaultAccessToken(): string {
  try {
    return (localStorage.getItem(TOKEN_STORAGE_KEY) ?? "").trim();
  } catch {
    return "";
  }
}

/** 默认租户 ID 读取：localStorage + trim 归一，缺失回退 "default" + 容错 */
function defaultGetTenantId(): string {
  try {
    const tenantId = (localStorage.getItem(TENANT_STORAGE_KEY) ?? "").trim();
    return tenantId !== "" ? tenantId : "default";
  } catch {
    return "default";
  }
}

/** 默认错误工厂：构造 ApiError 基类（layer 缺省由 httpStatus/code 推导） */
const defaultErrorFactory: ApiErrorFactory = (code, message, details, httpStatus, layer) =>
  new ApiError(code, message, details, httpStatus, layer);

/** 创建 httpClient 实例（一个工厂函数） */
export function createHttpClient(options: HttpClientOptions = {}): HttpClient {
  const baseUrl = options.baseUrl ?? "";
  const timeoutMs = options.timeoutMs;
  const retry = options.retry;
  const getAccessToken = options.getAccessToken ?? getDefaultAccessToken;
  const getTenantId = options.getTenantId ?? defaultGetTenantId;
  const tenantHeaderName = options.tenantHeaderName;
  const errorFactory = options.errorFactory ?? defaultErrorFactory;

  /** 构造请求头（纯对象：Content-Type 恒定或 multipart 省略；Authorization 仅非空令牌；租户头按配置；逐请求头覆写合并） */
  function buildHeaders(req: HttpClientRequest): Record<string, string> {
    const headers: Record<string, string> = {};
    if (req.multipart !== true) {
      headers["Content-Type"] = "application/json";
    }
    const token = getAccessToken();
    if (token !== "") {
      headers["Authorization"] = `Bearer ${token}`;
    }
    if (tenantHeaderName !== undefined) {
      headers[tenantHeaderName] = getTenantId();
    }
    if (req.headers !== undefined) {
      for (const key of Object.keys(req.headers)) {
        headers[key] = req.headers[key];
      }
    }
    return headers;
  }

  /** 统一响应处理：2xx 解析 JSON（或 voidResponse 跳过）；非 2xx 经 errorFactory 抛 ApiError 族 */
  async function handleResponse<T>(response: Response, voidResponse: boolean): Promise<T> {
    if (response.ok) {
      if (voidResponse) {
        return undefined as T;
      }
      return (await response.json()) as T;
    }
    let code = "HTTP_ERROR";
    let message = `HTTP ${response.status} ${response.statusText}`;
    let details: Record<string, unknown> = {};
    try {
      // 兼容三种错误体：平台统一结构 {code,message,details}、FastAPI 默认 {detail}（字符串）、
      // 市场/治理族 HTTPException 结构化 detail {detail:{error_code,error_message}}
      const raw = (await response.json()) as Partial<ApiErrorResponse> & {
        detail?: unknown;
      };
      const detailRecord =
        raw.detail !== null && typeof raw.detail === "object"
          ? (raw.detail as Partial<{ error_code: string; error_message: string }>)
          : undefined;
      code = raw.code ?? detailRecord?.error_code ?? "HTTP_ERROR";
      message =
        raw.message ??
        (typeof raw.detail === "string" ? raw.detail : detailRecord?.error_message) ??
        `HTTP ${response.status} ${response.statusText}`;
      details = raw.details ?? {};
    } catch {
      code = "UNKNOWN_ERROR";
      message = `HTTP ${response.status}: ${response.statusText}`;
      details = {};
    }
    throw errorFactory(code, message, details, response.status, undefined);
  }

  async function request<T>(req: HttpClientRequest): Promise<T> {
    const url = `${baseUrl}${req.path}`;
    const method = (req.method ?? "GET").toUpperCase();
    const retryEnabled = retry?.enabled === true;
    const maxAttempts = retryEnabled ? Math.max(1, retry.maxAttempts) : 1;
    const canRetry =
      retryEnabled && (!retry.idempotentMethodsOnly || IDEMPOTENT_METHODS.includes(method));

    // 超时：内部 AbortController 与调用方 signal 联动；未配置超时则直接透传调用方 signal
    let controller: AbortController | undefined;
    let timedOut = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (timeoutMs !== undefined) {
      controller = new AbortController();
      timer = setTimeout(() => {
        timedOut = true;
        controller?.abort();
      }, timeoutMs);
      if (req.signal !== undefined) {
        if (req.signal.aborted) {
          controller.abort();
        } else {
          req.signal.addEventListener("abort", () => controller?.abort(), { once: true });
        }
      }
    }
    const signal = controller?.signal ?? req.signal;

    try {
      let attempt = 0;
      for (;;) {
        attempt += 1;
        const init: RequestInit = { method, headers: buildHeaders(req) };
        if (req.body !== undefined) {
          // FormData（multipart）原样传递，其余 JSON 序列化
          init.body = req.body instanceof FormData ? req.body : JSON.stringify(req.body);
        }
        if (signal !== undefined) {
          init.signal = signal;
        }
        let response: Response;
        try {
          response = await fetch(url, init);
        } catch (error) {
          // 调用方取消：裸透传（组件依赖 e.name === "AbortError" 语义，不重试）
          if (req.signal?.aborted) {
            throw error;
          }
          if (!canRetry || attempt >= maxAttempts) {
            if (timedOut) {
              throw errorFactory(
                "TIMEOUT",
                `请求超时（${timeoutMs}ms）`,
                {},
                undefined,
                ApiErrorCodeLayer.Transport,
              );
            }
            const networkMessage = error instanceof Error ? error.message : String(error);
            throw errorFactory(
              "NETWORK_ERROR",
              networkMessage,
              {},
              undefined,
              ApiErrorCodeLayer.Transport,
            );
          }
          continue;
        }
        // 5xx 且可重试：进入下一次尝试（末次尝试落入标准错误映射）
        if (canRetry && attempt < maxAttempts && !response.ok && response.status >= 500) {
          continue;
        }
        return await handleResponse<T>(response, req.voidResponse === true);
      }
    } finally {
      if (timer !== undefined) {
        clearTimeout(timer);
      }
    }
  }

  return { request };
}
