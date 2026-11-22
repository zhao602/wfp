import type { APIRequestContext } from "@playwright/test";

// 后端 API 帮助函数：全部经 vite 代理（/api → WFP_E2E_API_TARGET）或
// request 上下文（baseURL 同源）访问 e2e 后端（design D2）。

const API = "/api/v1";

export interface ExampleEntry {
  example_id: string;
  name: string;
  available: boolean;
}

export interface InstanceInfo {
  instance_id: string;
  status: string;
}

export interface SubscriptionInfo {
  subscription_id: string;
  workflow_definition_id: string;
  status: string;
}

async function jsonOrThrow<T>(res: { ok(): boolean; status(): number; json(): Promise<T> }, label: string): Promise<T> {
  if (!res.ok()) {
    throw new Error(`${label} -> HTTP ${res.status()}`);
  }
  return await res.json();
}

/** 范例列表（ExampleLibraryPanel 同源端点）。 */
export async function fetchExamples(request: APIRequestContext): Promise<ExampleEntry[]> {
  const res = await request.get(`${API}/examples`);
  const body = await jsonOrThrow<{ examples: ExampleEntry[] }>(res, "GET /examples");
  return body.examples;
}

/** 实例详情（轮询终态用）。 */
export async function getInstance(request: APIRequestContext, instanceId: string): Promise<InstanceInfo> {
  const res = await request.get(`${API}/instances/${instanceId}`);
  return await jsonOrThrow<InstanceInfo>(res, `GET /instances/${instanceId}`);
}

/** 轮询实例状态直至命中目标集合（SSE 之外的稳定兜底，design D8）。 */
export async function waitForInstanceStatus(
  request: APIRequestContext,
  instanceId: string,
  targets: string[],
  timeoutMs = 30_000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const inst = await getInstance(request, instanceId);
    if (targets.includes(inst.status)) {
      return inst.status;
    }
    if (Date.now() > deadline) {
      throw new Error(
        `实例 ${instanceId} 等待状态 ${targets.join("/")} 超时（当前 ${inst.status}）`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
}

/** 人工审批决策（混合路径③：审批面板 UI 缺失，经 API 提交，spec §2③）。 */
export async function submitReview(
  request: APIRequestContext,
  instanceId: string,
  decision: "approve" | "reject" = "approve",
  actor = "e2e-reviewer",
): Promise<void> {
  const res = await request.post(`${API}/instances/${instanceId}/review`, {
    data: { decision, actor },
  });
  if (!res.ok()) {
    throw new Error(`POST /instances/${instanceId}/review -> HTTP ${res.status()}`);
  }
}

/** 创建实时数据订阅（混合路径④：订阅面板 UI 缺失，经 API CRUD，spec §2④）。 */
export async function createSubscription(
  request: APIRequestContext,
  workflowDefinitionId: string,
): Promise<SubscriptionInfo> {
  const res = await request.post(`${API}/subscriptions`, {
    data: {
      workflow_definition_id: workflowDefinitionId,
      workflow_definition_version: 1,
      source_type: "kafka",
      connection_reference: "e2e-conn",
      event_source: "e2e-topic",
    },
  });
  return await jsonOrThrow<SubscriptionInfo>(res, "POST /subscriptions");
}

export async function listSubscriptions(request: APIRequestContext): Promise<SubscriptionInfo[]> {
  const res = await request.get(`${API}/subscriptions`);
  return await jsonOrThrow<SubscriptionInfo[]>(res, "GET /subscriptions");
}

export async function getSubscription(
  request: APIRequestContext,
  subscriptionId: string,
): Promise<SubscriptionInfo> {
  const res = await request.get(`${API}/subscriptions/${subscriptionId}`);
  return await jsonOrThrow<SubscriptionInfo>(res, `GET /subscriptions/${subscriptionId}`);
}

export async function pauseSubscription(
  request: APIRequestContext,
  subscriptionId: string,
): Promise<SubscriptionInfo> {
  const res = await request.post(`${API}/subscriptions/${subscriptionId}/pause`);
  return await jsonOrThrow<SubscriptionInfo>(res, `POST /subscriptions/${subscriptionId}/pause`);
}

export async function resumeSubscription(
  request: APIRequestContext,
  subscriptionId: string,
): Promise<SubscriptionInfo> {
  const res = await request.post(`${API}/subscriptions/${subscriptionId}/resume`);
  return await jsonOrThrow<SubscriptionInfo>(res, `POST /subscriptions/${subscriptionId}/resume`);
}

export async function getSubscriptionHistory(
  request: APIRequestContext,
  subscriptionId: string,
): Promise<unknown[]> {
  const res = await request.get(`${API}/subscriptions/${subscriptionId}/history`);
  return await jsonOrThrow<unknown[]>(res, `GET /subscriptions/${subscriptionId}/history`);
}

export async function deleteSubscription(
  request: APIRequestContext,
  subscriptionId: string,
): Promise<void> {
  const res = await request.delete(`${API}/subscriptions/${subscriptionId}`);
  if (!res.ok()) {
    throw new Error(`DELETE /subscriptions/${subscriptionId} -> HTTP ${res.status()}`);
  }
}
