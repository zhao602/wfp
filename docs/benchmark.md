# 性能基线（B8c · roadmap-v3 P1）

> 四基准本机基线（线性 50 节点 / 并行 8 分支 / 10 万行管道 / SSE 1000 事件），
> 2026-11-22 实测。原始 JSON 存 `.codeartsdoer/specs/roadmap_b8c_perf_baseline/results/`。
> 数字用于回归对照（后续跨后端复测的前置基线），不作跨机器横评。

## 1. 环境声明

| 项 | 值 |
|---|---|
| 机型 | LENOVO 83AL（笔记本） |
| CPU | 13th Gen Intel Core i5-13500H（12C/16T） |
| 内存 | 16GB |
| OS | Windows 11（10.0.22631） |
| Python | 3.12.0（AMD64） |
| 存储 | 本地文件系统（`local_file` 存储后端） |
| 服务 | 全栈真实 uvicorn（`create_app --factory` 同构程序化启动）+ `wfp_sdk` 公共客户端 |

## 2. 口径与方法

- **两遍法**：计时遍不开 tracemalloc（纯墙钟）；内存遍基准进程与服务进程
  双双启用 `tracemalloc`，峰值 = max(客户端峰值, 服务端峰值)。
  tracemalloc 只计 Python 堆分配，不含 C 堆 / OS RSS。
- **计时指标**：`start_instance` → 终态 `succeeded` 的墙钟（含 POST / 轮询，
  轮询间隔 0.05s）；p50/p95 为 nearest-rank，先跑 1 预热轮不入统计。
- **链级 env 覆盖**（harness 注入）：`STORAGE_BACKEND=local_file`、
  `WORKFLOW_MAX_NODES=600`、临时 `LOCAL_STORAGE_ROOT`。
- **SSE 例外口径**：500 节点链一次性执行到终态为 setup 段（不入计时窗），
  计时段为同一终态实例的 SSE 回放整流消费（事件集恒定，执行抖动隔离）。

## 3. 结果汇总

| 基准 | 形状 | 轮数 | p50 (ms) | p95 (ms) | 内存峰值 (MB) |
|---|---|---|---|---|---|
| linear_50 | 50 节点 handle_nulls 链 × 100 行 | 5 | 21437.2 | 21759.9 | 42.3 |
| parallel_8 | 严格扇出 8 分支聚合 × 10k 行 | 5 | 15212.3 | 15872.8 | 59.7 |
| pipeline_100k | 100k 行清洗+聚合（payload 4.7MB） | 3 | 26331.5 | 27281.2 | 247.8 |
| sse_1000 | 500 节点链终态回放 1006 事件 | 3 | 76.0 | 85.3 | 48.8 |

分项明细（min/max/mean、客户端/服务端分量、事件类型分布）见
`results/*.json`；SSE setup 段执行耗时 45.184s（信息项，不入表）。

## 4. 合成数据标注（非真实业务数据）

- 确定性生成（`_bench_common.make_rows`）：`{id, category(8 桶 cat_0..cat_7),
  value((i×37) mod 1000)}`，同参数重跑字节级一致。
- `with_nulls=True`（仅 pipeline_100k）：每 20 行注入 `value=None`、每 25 行
  注入 `category=None` → `handle_nulls(drop_row)` 实际剔除 ~9% 行，清洗非空转。
- SSE 基准用空数据集（0 行）：度量为 SSE 序列化/传输/解析吞吐，与数据内容
  无关；空列表装配合法（data_transform 执行器 FR-14/AC-06）。

## 5. 已知特性（读数须知）

以下三点为本批次实测确认的平台行为，直接影响上表数字的解读：

1. **local_file 检查点 O(n²) 重解析**
   `LocalFileCheckpointRepository.append` 每步通过 `_find_same_round` 重读并
   反序列化该实例全部既有检查点
   （`src/orchestration_platform/persistence/local_file/local_file_checkpoint_repository.py`）。
   linear_50 每节点 ~430ms 中该二次项占主导；节点越多越贵。
   postgres 后端无此问题（唯一索引 `ON CONFLICT` O(1) 追加）。
2. **共享状态链级累缩**
   链式 data_transform 的各级输出全部驻留 `node_outputs`，检查点体积随
   链长 O(i×dataset) 累缩（linear_50 实测平均 260KB/检查点、最大 499KB）。
3. **执行期事件循环占用**
   执行链同步 I/O（检查点/事件落盘）无真正挂起点，实例执行期间事件循环被
   长期占用，`POST /instances` 响应被推迟到执行结束后才返回——计时指标
   ≈ 实例执行全长，客户端超时须按此设定。

另：spec `1.1` 定义节点数硬上限 200（`yaml_workflow_parser.py`），500 节点
SSE 基准走 spec `1.0`（上限读 `WORKFLOW_MAX_NODES`，harness 置 600）。

## 6. 复现

```bash
python scripts/perf/bench_linear_50.py --mode both --json
python scripts/perf/bench_parallel_8.py --mode both --json
python scripts/perf/bench_pipeline_100k.py --mode both --json
python scripts/perf/bench_sse_1000.py --mode both --json
```

- `--mode both` = 计时遍 + 内存遍（默认）；`--rounds N` 调计时轮数。
- `--out FILE` 另存纯 JSON；`--keep-temp` 保留临时目录（服务日志/峰值文件）。
- 脚本自带服务生命周期（起/停/健康检查/优雅停机），无需手工起服务。

## 7. 后续交叉引用

- 跨后端复测（`local_file` → `postgres`）由长期执行方案 B9 批出口验收承接
  （[docs/roadmap-v3-longterm-execution-2026-11.md](roadmap-v3-longterm-execution-2026-11.md)：
  四基准重跑 p95 漂移 ≤5%）——本页数字为其 local_file 基线对照。
- 双运行时边界声明（B8e · A5）见
  [docs/dual-runtime-boundaries.md](dual-runtime-boundaries.md)。
