# 双运行时边界与能力声明（B8e · roadmap-v3 A5）

> 「子集外」能力自代码注释升级为对外文档（A5：避免「看起来支持」的认知风险）。
> 源锚点：`src/orchestration_platform/engine/adapter/langgraph_adapter.py`；
> 等价矩阵（13 例，主表由脚本生成）：[docs/langgraph-equivalence-matrix.md](langgraph-equivalence-matrix.md)。

## 1. 边界总览

- **主运行时**：内置图状态机引擎（`YamlWorkflowParser → GraphCompiler → Scheduler`）——全能力（含并行扇出、HITL、回滚重放、子工作流、监督者）。
- **第二运行时**：LangGraph 适配器——严格等价子集（顺序 / 条件分支单层 / 单层循环三类拓扑，见矩阵文档 §0.1），节点语义经执行器注册表单源复用（INV-B4-01）。
- **开关**：`langgraph_runtime_enabled` 默认 `False`（HC-07/GK-11）；生产 `start_instance` 链路零改（INV-B4-05）；开关 `True` 且依赖缺失时降级内置引擎并告警（INV-B4-03）。
- **隔离**：`engine/adapter/` 是全仓唯一允许 `import langgraph` 的位置（HC-03/GK-13；守护测试 `tests/unit/test_langgraph_isolation.py`）。

## 2. resume / rollback / replay / submit_review：子集外声明

四个 `IWorkflowEngine` 方法在 LangGraph 适配器中**不提供真实能力**——调用即明确报错（或不可达），不静默伪造支持。真实能力全部在内置引擎链路。

| 方法 | 适配器行为 | 错误码 | 真实能力归属（内置引擎链路） |
|---|---|---|---|
| `resume(instance_id, checkpoint_id)` | 抛错桩 | `STATE_CHECKPOINT_NOT_FOUND`（消息指回内置引擎链路） | 检查点恢复链路（内置引擎） |
| `rollback(instance_id, target_checkpoint_id)` | 抛错桩 | `STATE_CHECKPOINT_NOT_FOUND`（消息指回 rollback_replay_service） | `application/rollback_replay_service.py`：校验状态 → 校验检查点 → 恢复状态 → void_after → 写 ROLLBACK 事件 |
| `replay(request)` | 抛错桩 | `STATE_CHECKPOINT_NOT_FOUND`（同上） | 同上：`STRICT_REPLAY` 复用历史输出、跳过实际调用 / `RE_EXECUTE` 重新调用全部节点并记录差异清单 / 检查点损坏降级 `UNRECOVERABLE` |
| `submit_review(instance_id, decision)` | 最小占位实现（仅状态翻转，非 HITL 真实现） | 实例不存在 `INSTANCE_NOT_FOUND`；否则恒 `INSTANCE_NOT_WAITING` | 真实 HITL 挂起/审核/恢复链路（内置引擎；排除②，第二运行时不映射） |

**submit_review 不可达分析**：排除② 使 `_validate_subset` 拒绝 `HUMAN_REVIEW_GATE` 控制流节点，实例在第二运行时不可能进入 `WAITING_REVIEW` 状态，故 `submit_review` 的状态翻转分支实际不可达——调用结果恒为报错。此为「不伪造支持」的正确语义：报错并指回真实链路，而非假装恢复。

## 3. 五项排除清单与运行期防线

五项排除（硬边界，与[矩阵文档](langgraph-equivalence-matrix.md) §0.2 同源——修改须双侧同步）：

| # | 排除项 |
|---|---|
| ① | 并行扇出/汇聚 |
| ② | HITL 挂起 |
| ③ | 回滚重放 |
| ④ | 多租户运行时 |
| ⑤ | 子工作流嵌套（含 SUPERVISOR 监督者子编排） |

**运行期防线** `LangGraphAdapter._validate_subset`（图构建前执行，命中即抛 `WF_VALIDATION_FAILED`，不静默降级、不伪造等价）：

- 排除①②⑤ 为编译期可判——`PARALLEL_JOIN` / `HUMAN_REVIEW_GATE` / `SUBWORKFLOW` / `SUPERVISOR` 越界即拒；
- 排除③ 为方法级抛错桩（§2 表）；
- 排除④ 为部署/断言范围声明（租户级运行时隔离不在等价断言范围，非适配器编译期职责）。

守护测试：`test_na_examples_out_of_subset`（矩阵测试内 4 套 N/A 范例，硬边界 3 例运行期拒绝断言）、`tests/unit/test_langgraph_isolation.py`（业务代码禁止 `import langgraph`）。

## 4. SSO 企业身份：断言验签落地核对（roadmap S2 收口）

**S2 二选一**（[roadmap-v3](roadmap-v3-2026-11.md) S2）裁决结果：**实现断言验签＋重放防护**（[长期执行方案](roadmap-v3-longterm-execution-2026-11.md) §9 P3-②）。收口核对（2026-11-23）：

| 面向 | 状态 | 事实 |
|---|---|---|
| SAML 验签 | 落地 ✓ | `saml_adapter.py`：base64→XML→状态码→xmlsec 验签（响应级/断言级至少一处，含 Reference 摘要）→包裹加固（断言级 Reference URI 必须命中断言 ID）→受众/时效（`NotOnOrAfter` 必填，±60s 时钟偏移）→NameID/属性提取；断言标识服务端提取 |
| OIDC 验签 | 落地 ✓ | `oidc_adapter.py`：`id_token`/授权码双源；PyJWT 验签（签名/受众/`exp` 必填/签发者）；算法白名单与凭据类型绑定（对称 secret→HS256，证书/公钥 PEM→RS256/RS512/ES256/ES512），拒绝算法混淆 |
| 防重放 | 落地 ✓ | `replay_guard.py`：断言 ID SHA-256 摘要一次性缓存，重复断言抛 `SSO_REPLAY_DETECTED`；断言标识改由服务端从已验签断言提取（原客户端可伪造缺口关闭） |
| 依赖形态 | `[sso]` profile | `xmlsec>=1.3` / `PyJWT>=2.8` / `lxml>=4.9`（`pyproject.toml` optional-dependencies），惰性导入，缺失时 `is_available()` 显式降级 |
| 验证 | 27 例 | `tests/phase4/test_sso_verification.py`：正路径往返 + 篡改/错证书/无签名/错受众/过期/未生效/缺时效/包裹攻击/算法混淆/错密钥/重放全拒 |

SSO 对外不再带实验性标注；README 能力表与 `docs/sso_integration_guide.md` 已同步更新。已知边界：多副本共享防重放缓存（当前进程内）登记为移交项，见 S2 出口记录。

## 5. 相关文档

- [等价矩阵与 E1~E5 口径](langgraph-equivalence-matrix.md)（13 例；§1 主表由 `scripts/generate_dual_runtime_matrix.py` 生成，勿手改——一致性测试 `tests/unit/test_generate_dual_runtime_matrix.py` 守护）
- [性能基线](benchmark.md)（B8c，四基准）
- [架构评审基线](architecture-review-2026-11.md)
- [roadmap-v3](roadmap-v3-2026-11.md)（A5 / S2 条目）· [长期执行方案](roadmap-v3-longterm-execution-2026-11.md)（S2 可插小批）
