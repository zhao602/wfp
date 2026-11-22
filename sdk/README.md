# wfp-sdk —— 工作流编排平台嵌入 SDK（Python）

> 版本 0.2.0｜契约：[`docs/sdk-contract.md`](../docs/sdk-contract.md)（冻结）
> 同步客户端，仅依赖 `httpx>=0.24`，零平台内部依赖（隔离 venv 可独立安装）。
> 许可：Apache-2.0（随包分发）。

## 安装

```bash
# 方式一：PyPI（发布后；发布状态见 ../docs/release-sdk.md）
pip install wfp-sdk

# 方式二：从仓库 sdk/ 目录构建 wheel 并安装
python -m build --wheel --outdir dist
python -m pip install dist/wfp_sdk-0.2.0-py3-none-any.whl
```

## 30 行端到端

```python
from wfp_sdk import WorkflowClient

YAML = "..."  # 工作流 YAML 文本（spec_version 1.0/1.1）

with WorkflowClient("http://127.0.0.1:8310") as client:
    report = client.validate_definition(YAML)        # 1 校验（恒返报告）
    assert report.valid
    client.submit_definition(YAML)                   # 2 提交定义
    info = client.start_instance(                    # 3 触发执行
        "wf_demo", YAML, inputs={"query": 1},
    )
    for event in client.stream_events(info.instance_id):   # 4 SSE 订阅
        print(event.event_type)
    client.wait_for_completion(info.instance_id)     # 5 等待终态
    results = client.get_results(info.instance_id)   # 6 取结果
    print(results.outputs)
```

## 方法面（9 方法，契约 §1）

| 方法 | 说明 |
|---|---|
| `validate_definition(yaml_text)` | 校验（恒返 `ValidationReport`，非法定义不抛错） |
| `submit_definition(yaml_text)` | 提交定义（422 → `WfpValidationError`，issues 同构） |
| `start_instance(definition_id, yaml_text, inputs=None, business_category=None)` | 触发执行 |
| `get_instance(instance_id)` | 状态快照 |
| `stream_events(instance_id)` | SSE 订阅（生成器，终态自然收尾） |
| `poll_events(instance_id, include_voided=False)` | 事件轮询（降级通道） |
| `cancel_instance(instance_id)` | 取消执行中实例（409 → `WfpStateConflictError`） |
| `get_results(instance_id)` | 取结果（非终态 `outputs=None` 轮询友好） |
| `wait_for_completion(instance_id, timeout=120.0, poll_interval=1.0)` | 轮询至终态 |

## 错误处理（契约 §2）

```python
from wfp_sdk import WorkflowClient, WfpValidationError, INSTANCE_CANCEL_CONFLICT

with WorkflowClient("http://127.0.0.1:8310") as client:
    try:
        client.cancel_instance("inst-xxx")
    except WfpError as exc:          # 全部异常同构：exc.code / exc.message / exc.details
        print(exc.code)              # 如 INSTANCE_CANCEL_CONFLICT
```

- 异常家族：`WfpValidationError`(422) / `WfpStateConflictError`(409) /
  `WfpNotFoundError`(404) / `WfpAuthError`(401/403) / `WfpTimeoutError`（合成码
  `CLIENT_TIMEOUT`）/ `WfpTransportError`（合成码 `TRANSPORT_ERROR`）/ `WfpError` 兜底；
- 未知平台响应码原样透传（`exc.code` 保留原始字符串）。

## 租户与鉴权（契约 §3，冻结）

- 租户身份随 `Authorization: Bearer <token>` 令牌解析，**无租户请求头**；
  `WorkflowClient(base_url, token=...)` 传预签发令牌即可；
- 治理降级模式（local profile）无需令牌，回退平台默认租户。

## 版本策略与兼容矩阵（契约 §5）

- **semver**：SDK 语义化版本；破坏性契约变更须 `/api/v2` 或 SDK MAJOR；
  MINOR 仅追加能力，PATCH 仅修复。
- **`/api/v1` 面冻结**；工作流定义规范 `spec_version` 遵循平台
  [1.x 只增不删承诺](../docs/spec-versioning.md)。

| SDK 版本 | API 面 | spec_version | 说明 |
|---|---|---|---|
| 0.1.x | `/api/v1`（冻结） | 1.0 / 1.1 | B2 首发方法面（9 方法） |
| **0.2.0** | `/api/v1`（冻结） | 1.0 / 1.1 | 与平台 v0.2.0 同步发布；平台 usage 汇总等 B7 新增端点尚未纳入 SDK 方法面（后续 MINOR） |

- 平台侧 `spec_version` 新增（如未来 1.2）对 SDK 透传无感：定义文本原样提交，
  校验结果由平台返回——SDK 不内置版本枚举。
