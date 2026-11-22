# 插件/节点 SDK 开发指南

> 本文档说明插件 SDK 的基类契约、manifest 编写、打包规范、本地调试运行器使用以及贡献提交流程。

## 1. 概览

插件 SDK（plugin_sdk）为第三方扩展提供标准化开发契约，确保插件在沙箱内安全执行并符合平台治理要求。SDK 提供 `setup` / `execute` / `teardown` 三段式生命周期。

### 1.1 核心概念

- **Plugin**：以节点形式扩展平台能力，如自定义数据源、转换器、输出器。
- **Manifest**：插件元数据声明文件，描述入口、参数、权限与依赖。
- **Runtime**：平台加载插件后，在沙箱内实例化并调用。

### 1.2 配置项清单（默认值）

```yaml
plugin_sdk:
  enabled: true
  version: "1.0.0"
  manifest_schema: ./schemas/plugin-manifest.json
  auto_upgrade: false
  strict_signature: false      # 是否强制签名校验
  local_debug_runner: true
  default_timeout_ms: 30000
```

## 2. 基类契约

所有插件须继承 `BasePlugin` 并实现三段生命周期方法。

### 2.1 生命周期

```
加载 → setup(context) → execute(input) → teardown() → 卸载
```

| 方法 | 调用时机 | 职责 | 异常处理 |
| ---- | -------- | ---- | -------- |
| `setup` | 实例化后一次 | 初始化资源、校验配置、建立连接 | 抛异常则插件不加载 |
| `execute` | 每次节点调用 | 执行核心逻辑，返回输出 | 异常由运行时捕获并标记失败 |
| `teardown` | 卸载时一次 | 释放资源、关闭连接 | 异常仅记录日志 |

### 2.2 基类示例（Python）

```python
from plugin_sdk import BasePlugin, PluginContext, PluginInput, PluginOutput

class MyPlugin(BasePlugin):
    def setup(self, ctx: PluginContext) -> None:
        # 校验配置、初始化资源
        self.api_key = ctx.config.get("api_key")
        if not self.api_key:
            raise ValueError("api_key is required")

    def execute(self, inp: PluginInput) -> PluginOutput:
        # 执行核心逻辑
        result = self._call_external(inp.payload)
        return PluginOutput(data=result)

    def teardown(self) -> None:
        # 释放资源
        pass
```

### 2.3 契约约束

- `setup` 必须幂等：重复调用不应产生副作用。
- `execute` 必须无状态：不依赖实例字段保存调用间状态（使用 `ctx.state`）。
- `teardown` 必须安全：即使 `setup` 未完成也应可调用。
- 所有方法须在 `default_timeout_ms` 内返回，超时由运行时终止。

## 3. Manifest 编写

Manifest 文件 `plugin.yaml` 描述插件元数据。

### 3.1 完整字段

```yaml
# plugin.yaml
api_version: v1
kind: Plugin
metadata:
  name: my-data-source
  version: 1.2.0
  author: zhangsan@example.com
  description: "自定义数据源插件"
  tags: [source, data]
spec:
  entry: my_plugin:MyPlugin        # 模块:类名
  runtime: python3.11              # python3.11 | jvm | node
  permissions:
    network: ["api.example.com"]
    filesystem: ["./workspace"]
  parameters:
    - name: api_key
      type: string
      required: true
      secret: true
    - name: timeout
      type: integer
      default: 5000
  dependencies:
    - requests>=2.28
  sandbox:
    mode: strict
    memory_limit_mb: 256
```

### 3.2 字段说明

| 字段 | 必填 | 说明 |
| ---- | ---- | ---- |
| `api_version` | 是 | Manifest 协议版本 |
| `metadata.name` | 是 | 插件唯一标识，小写+连字符 |
| `metadata.version` | 是 | 语义化版本 |
| `spec.entry` | 是 | 入口模块与类 |
| `spec.runtime` | 是 | 运行时类型 |
| `spec.permissions` | 否 | 申请的权限范围 |
| `spec.parameters` | 否 | 用户可配置参数 |
| `spec.sandbox` | 否 | 沙箱策略覆盖 |

## 4. 打包规范

### 4.1 目录结构

```
my-plugin/
├── plugin.yaml          # Manifest
├── my_plugin.py         # 入口模块
├── requirements.txt     # Python 依赖
├── tests/
│   └── test_plugin.py
└── README.md
```

### 4.2 打包命令

```bash
# 生成 .ppkg 包（zip 归档）
pluginctl pack ./my-plugin -o my-plugin-1.2.0.ppkg
```

### 4.3 包校验

- 包内必须包含 `plugin.yaml`。
- `spec.entry` 指向的模块必须存在。
- 依赖列表须可解析。
- 包大小不超过 50MB。

## 5. 本地调试运行器

SDK 内置本地调试运行器，无需部署平台即可测试插件。

### 5.1 启动调试

```bash
# 在插件目录下启动
pluginctl debug ./my-plugin \
  --input '{"payload": "test"}' \
  --config '{"api_key": "dev-key"}' \
  --watch                   # 文件变更自动重载
```

### 5.2 调试能力

- 沙箱策略模拟（strict/permissive）。
- 执行耗时与资源占用报告。
- `setup`/`execute`/`teardown` 各阶段日志。
- 异常堆栈与审计事件输出。

### 5.3 单元测试

```python
from plugin_sdk.testing import PluginTestCase

class TestMyPlugin(PluginTestCase):
    plugin_class = MyPlugin
    config = {"api_key": "test-key"}

    def test_execute(self):
        out = self.run_execute({"payload": "hello"})
        self.assertIsNotNone(out.data)
```

## 6. 故障排查

| 现象 | 可能原因 | 处置 |
| ---- | -------- | ---- |
| 插件加载失败 | `spec.entry` 模块不存在 | 检查模块路径与文件名 |
| setup 抛异常 | 配置缺失或依赖未安装 | 查看运行时日志，补全配置 |
| execute 超时 | 逻辑过慢或外部接口慢 | 调整 `timeout` 或优化逻辑 |
| 权限被拒 | 未在 `permissions` 声明 | 补充权限声明并重新打包 |
| 版本不兼容 | SDK 版本与平台不匹配 | 升级 SDK 或声明兼容范围 |

### 6.1 诊断命令

```bash
# 校验 manifest
pluginctl validate ./my-plugin/plugin.yaml

# 检查包完整性
pluginctl inspect my-plugin-1.2.0.ppkg

# 查看插件运行日志
pluginctl logs my-data-source
```

## 7. 贡献提交流程

1. 按 §4 规范完成开发与测试。
2. 使用 `pluginctl pack` 生成 `.ppkg`。
3. 生成签名（参见 [node_contribution_guide.md](./node_contribution_guide.md)）。
4. 提交至节点市场，进入贡献审核状态机。
5. 审核通过后发布，版本号纳入市场索引。

## 8. 降级行为

| 场景 | 降级策略 |
| ---- | -------- |
| SDK 版本不兼容 | 拒绝加载，提示升级 |
| 插件 `setup` 失败 | 标记插件为 `error`，不影响其他插件 |
| 插件 `execute` 异常 | 节点返回失败状态，工作流按错误策略处理 |
| 沙箱不可用 | 按 sandbox 降级链处理，插件可能被暂停 |
