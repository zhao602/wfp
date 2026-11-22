# 事件出口指南（Event Export Guide）

> 批次：roadmap B3「事件出口」（2026-11）。
> 本文是**线级契约的单一事实源**：订阅模型、管理 API、Webhook 签名头、重投语义、
> 死信队列（DLQ）、SSE 流与错误码在此冻结；后续 B2 SDK 按本文 1:1 包装（见 §6）。

## 1. 概述与范围

事件出口把平台内总线上的领域事件（`DomainEvent`）以三种形态向外送出：

| 形态 | 消费模式 | 语义 |
| --- | --- | --- |
| Webhook 投递 | worker 主动 HTTP POST | 指数退避重试，终态 ∈ {delivered, dead_lettered} |
| SSE 实时流 | 接收方拉取 `/event-export/stream` | 每连接独立消费组，断连即清理 |
| 管理 API | 拉取投递记录 / DLQ | 可观测与运维面 |

**范围**（roadmap B3）：内存总线同进程扇出、订阅注册、签名投递、DLQ 与重投、SSE。

**两项用户裁决（2026-11-21，入档）**：

1. **契约倒置**：本批冻结线级契约（本文档），B2 订阅方 SDK 后续按契约 1:1 包装；
   本批不产出 SDK 本体。
2. **内存态边界**：订阅持久化（B7 W3 重启保留）；DLQ / 重试态仍为进程内存态，重启清空；边界见 §7。

**Non-goals**：Kafka 真实出口投递（N 类禁试）；订阅持久化；跨进程扇出。

## 2. 开启方式

三处配置同步见 `docs/config.md`（开关表 + 参数表）。最小开启组合：

```dotenv
EVENT_EXPORT_ENABLED=true          # 出口装配总开关（关闭时零装配，基线零变化）
EVENT_EXPORT_API_ENABLED=true      # 管理 API + SSE 路由注册（子开关）
```

- `EVENT_EXPORT_API_ENABLED` 是 `EVENT_EXPORT_ENABLED` 的子开关：路由注册前置是
  出口装配就绪；总开关关闭时子开关无效。
- 事件产生依赖 `event_bus_publish_enabled=true`（平台既有开关）。
- 退避/尝试次数/超时/记录上限等 6 个参数键见 `docs/config.md` 参数表。

## 3. 线级契约冻结

### 3.1 订阅模型（WebhookSubscription）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `subscription_id` | str | 非空 | 订阅标识，注册即 upsert 主键 |
| `tenant_id` | str | 非空 | 租户标识 |
| `url` | str | 仅 `http://` / `https://` | Webhook 回调地址 |
| `secret` | str | 非空 | HMAC-SHA256 签名密钥 |
| `event_types` | list[str] | 空列表=全部 | 过滤条目以 `.` 结尾=前缀通配（如 `"node."`） |
| `enabled` | bool | 默认 true | 关闭后不投递、不删配置 |
| `description` | str | 默认空 | 人类可读描述 |

**线级约定**：任何 API 响应**不回显 `secret`**，代之以 `secret_set: true` 布尔。

### 3.2 管理 API（前缀 `/api/v1`）

| 方法 | 路径 | 语义 | 失败 |
| --- | --- | --- | --- |
| POST | `/event-export/subscriptions` | 注册/更新（upsert by `subscription_id`） | 校验失败 422 `EVENT_SUBSCRIPTION_INVALID` |
| GET | `/event-export/subscriptions` | 列出（`?tenant_id=` 可选过滤） | — |
| GET | `/event-export/subscriptions/{id}` | 取单个 | 404 `EVENT_SUBSCRIPTION_NOT_FOUND` |
| DELETE | `/event-export/subscriptions/{id}` | 删除（幂等：删不存在也 200） | — |
| POST | `/event-export/subscriptions/{id}:enable` | 启用 | 404 同上 |
| POST | `/event-export/subscriptions/{id}:disable` | 停用 | 404 同上 |
| GET | `/event-export/subscriptions/{id}/deliveries` | 投递记录（新到旧，环形上限） | 404 同上 |
| GET | `/event-export/dead-letters` | 死信条目（旧到新，含载荷快照） | — |
| POST | `/event-export/dead-letters/{entry_id}:redrive` | 死信重投（完整投递周期） | 404 `EVENT_DEAD_LETTER_NOT_FOUND` |
| GET | `/event-export/stream` | SSE 实时流（见 §3.5） | 未装配 503 |

冒号动作（`:enable` / `:disable` / `:redrive`）为 Google AIP 风格资源动作。

### 3.3 Webhook 投递线格式

worker 对每个匹配且启用的订阅发起：

```http
POST {subscription.url}
Content-Type: application/json
X-Orch-Signature: t=<unix秒级时间戳>,v1=<hex(HMAC-SHA256)>
X-Orch-Event-Type: <event.event_type>
X-Orch-Instance-Id: <event.instance_id>

<canonical body>
```

- **canonical body**：领域事件 `DomainEvent` 的确定性 JSON 序列化——
  `model_dump(mode="json")` + `sort_keys=True` + 分隔符 `(",", ":")`。
- **签名算法**：`v1 = hex(hmac_sha256(secret, f"{t}.{body}"))`；`t` 为投递时刻
  Unix 秒级时间戳，**每次重试重新取时重新签名**。
- **验证容差**：`|now - t| ≤ 300` 秒（`DEFAULT_TIMESTAMP_TOLERANCE_SECONDS`）。

### 3.4 投递 / 重试 / 死信语义

- **成功判定**：HTTP `2xx` 即成功；其余状态码与网络异常均视为失败。
- **重试**：单事件最多 `EVENT_EXPORT_WEBHOOK_MAX_ATTEMPTS`（默认 5）次；
  指数退避 `base * 2^(n-1)`，上限 `EVENT_EXPORT_WEBHOOK_BACKOFF_CAP_SECONDS`
  （默认 60s）。
- **终态**：重试耗尽 → 死信队列（DLQ）。断连重投 0 丢失：进程生命周期内每事件
  终态 ∈ {delivered, dead_lettered}，无第三态。
- **redrive**：对死信执行完整投递周期；成功移出 DLQ，失败更新既有条目
  （累计 attempts，不新建条目）。
- **DLQ 上限**：`EVENT_EXPORT_DLQ_MAX_ENTRIES`（默认 1000），超限丢最旧并告警。

### 3.5 SSE 实时流

```
GET /api/v1/event-export/stream?instance_id=<可选精确>&event_types=<可选逗号分隔>
```

- 媒体类型 `text/event-stream`；每条 `data: <DomainEvent JSON>\n\n`。
- `event_types` 语义同订阅过滤：条目以 `.` 结尾=前缀通配。
- 每连接独立消费组（uuid4），**断连即清理**（unsubscribe），无泄漏。
- 未装配事件总线时 503。

### 3.6 错误码（追加于 `domain/errors.py`）

| 错误码 | HTTP | 场景 |
| --- | --- | --- |
| `EVENT_SUBSCRIPTION_NOT_FOUND` | 404 | 订阅不存在（取/启停/deliveries） |
| `EVENT_SUBSCRIPTION_INVALID` | 422 | 订阅校验失败（URL 协议/空 secret/空过滤条目） |
| `EVENT_DEAD_LETTER_NOT_FOUND` | 404 | 死信条目不存在（redrive） |

## 4. 接收端示例（FastAPI receiver）

```python
import hashlib
import hmac
import json
from fastapi import FastAPI, Request, Response

app = FastAPI()
SECRET = "<与注册时相同的 secret>"
TOLERANCE_SECONDS = 300.0


def verify_webhook(secret: str, header: str, body: str, *, now: float) -> bool:
    """校验 X-Orch-Signature（与平台 signature.py 同口径）。"""
    segments = header.split(",")
    t = next((s[2:] for s in segments if s.startswith("t=")), None)
    v1 = next((s[3:] for s in segments if s.startswith("v1=")), None)
    if t is None or v1 is None:
        return False
    if abs(now - int(t)) > TOLERANCE_SECONDS:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), f"{t}.{body}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, v1)


@app.post("/hook")
async def hook(request: Request) -> Response:
    body = (await request.body()).decode("utf-8")
    import time

    if not verify_webhook(SECRET, request.headers.get("X-Orch-Signature", ""), body,
                          now=time.time()):
        return Response(status_code=403)  # 拒绝：验签失败/时间戳过期
    event = json.loads(body)
    # event["event_type"] / event["instance_id"] / event["payload"] ...
    return Response(status_code=202)  # 2xx 即投递成功
```

要点：**先验签再消费**；返回任意 `2xx` 视为成功；慢处理应先应答再异步消化
（重投会带来重复，消费端需幂等——**at-least-once** 语义）。

## 5. curl 演示

```bash
BASE=http://127.0.0.1:8000/api/v1/event-export

# 注册订阅（event_types 空数组=全量；"node."=前缀通配）
curl -sS -X POST $BASE/subscriptions -H 'Content-Type: application/json' -d '{
  "subscription_id": "sub-1", "tenant_id": "tenant-1",
  "url": "http://receiver.example/hook", "secret": "s3cret",
  "event_types": ["instance."], "enabled": true
}'

# 查投递记录 / 死信 / 重投
curl -sS $BASE/subscriptions/sub-1/deliveries
curl -sS $BASE/dead-letters
curl -sS -X POST $BASE/dead-letters/<entry_id>:redrive

# SSE 实时流（-N 关闭缓冲）
curl -sS -N "$BASE/stream?instance_id=inst-1&event_types=instance."
```

## 6. B2 SDK 1:1 包装注记（契约倒置裁决入档）

**裁决（2026-11-21）**：B3 冻结线级契约（本文 §3 即契约面），B2 订阅方 SDK
后续按本契约 1:1 包装，不引入第二套契约。SDK 对齐基线：

- 订阅注册/启停/删除 → §3.2 管理端点的直包装；
- 验签 → §3.3 签名算法（`verify_webhook` 同口径）；
- 事件过滤 → `event_type_matches` 同语义（`"."` 结尾前缀通配）；
- SSE 消费 → §3.5 流格式与断连清理语义。

契约漂移即缺陷：SDK 侧任何偏离本文的行为须回改到对齐，而非反向修改本文。

## 7. 已知边界（内存态 + 单实例，用户裁决 2 入档）

| 边界 | 说明 |
| --- | --- |
| 重启保留订阅 | 订阅持久化（B7 W3）：`EVENT_EXPORT_SUBSCRIPTIONS_PERSIST_PATH`（默认 `./.data/event_export/subscriptions.json`），变更后原子落盘、启动期恢复；投递记录、在途重试、DLQ 仍为进程内存态，重启清空 |
| 单实例 | 内存总线无跨进程扇出；多副本部署会重复投递（每副本独立 worker） |
| Kafka 真实出口 | N 类禁试：KafkaEventBus/EventBusManager 存量零改动，本批仅内存总线 |
| SSE 慢消费者 | 每连接独立队列无界积压风险；异常断连由客户端/代理超时兜底 |
| 在途取消 | 应用关停（stop）会取消在途投递；未落终态的事件随进程终止丢失 |
| 投递语义 | at-least-once：接收端必须幂等（重试与 redrive 均可能产生重复） |

## 8. 验收对应（roadmap §7 B3 三条）

1. **断连重投 0 丢失**：§3.4 终态语义；集成测试
   `test_delivered_union_dead_lettered_covers_all`。
2. **投递 p95 < 2s**：`EVENT_EXPORT_WEBHOOK_TIMEOUT_SECONDS` 默认 5s 单次上限；
   单测 `test_delivery_latency_p95`（20 事件采样的最大延迟口径）。
3. **订阅方 ≥2（同总线扇出实证）**：审计订阅方 + 出口 worker + SSE 各收全量；
   集成测试 `test_audit_worker_sse_each_receive_all`。

---

**相关文档**：`docs/config.md`（9 配置键三处同步）、`docs/governance_audit_compliance.md`
（审计订阅方）、`docs/kafka_event_bus_integration_guide.md`（Kafka 总线存量，本批未动）、
`.codeartsdoer/specs/roadmap_b3_event_export/`（批次档案：spec/design/tasks/_baseline）。
