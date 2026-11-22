# OEM 品牌构建指南（OEM Build Guide）

> 批次：roadmap B5「OEM 白标」（2026-11）。
> 档案：`.codeartsdoer/specs/roadmap_b5_oem_whitelabel/`（spec / design / tasks / drill-record）。

## 1. 这是什么

`scripts/build_oem.py` 读取 `oem/brands/*.yaml` 品牌配置，**单次运行产出全部品牌
部署产物**：品牌色阶、产品名、禁用面板清单经 `VITE_OEM_*` 环境变量在构建期烘焙进
前端 bundle；后端模块开关写入 `backend.env`——「一套源码、N 套品牌」。

默认品牌 `default` 与现状视觉逐字节一致（三模块全开）；演示品牌 `acmeflow`
（紫系、三模块全关）为验收②载体。

## 2. 快速开始

```
python scripts/build_oem.py                  # 构建全部品牌
python scripts/build_oem.py --brand acmeflow # 仅构建指定品牌
```

前置：`web/` 依赖已安装（`npm install`）；`node` 在 PATH；Python 3.10+ 与 PyYAML。

## 3. 品牌配置（oem/brands/*.yaml）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | str | 品牌 id（合法目录名，字母开头；`--brand` 过滤键） |
| `product_name` | str | 顶栏标题 + 产物 `<title>` 改写目标 |
| `colors` | 10 键映射 | 主色 10 色阶（下表），全部 `#rrggbb` 强校验 |
| `modules` | 映射 | module_id → bool；**显式声明**（后端开关默认全 False） |

colors 10 键（与 `web/src/brand/brandConfig.ts` 导出一一对应）：

| 键 | 语义 | default 现状值 |
| --- | --- | --- |
| `primary` | 主色（浅色主按钮背景/dot 渐变起点/激活描边） | `#1976d2` |
| `primary_hover` | 悬停 | `#1565c0` |
| `primary_active` | 按压 | `#0d47a1` |
| `primary_light` | 亮阶（深色主按钮背景/dot 渐变终点/暗激活描边） | `#42a5f5` |
| `primary_light_hover` | 深色主按钮 hover | `#2196f3` |
| `primary_soft` | 柔阶（暗激活文字/亮渐变终点） | `#8ec5fc` |
| `primary_tint` | 浅底（亮激活背景） | `#e8f1fb` |
| `primary_tint_hover` | 浅底悬停 | `#dceaf8` |
| `primary_dark_tint` | 暗底（暗激活背景） | `#1a2a3a` |
| `primary_dark_tint_hover` | 暗底悬停 | `#213045` |

**校验**（`load_brand`，构建期强校验，违规 exit 1）：顶层键白名单；颜色键齐全 +
`#rrggbb` 正则；module_id ⊆ 注册表；modules 值须 bool；未知键/缺失键报错。

**新增品牌**：复制 `oem/brands/default.yaml` 为 `<id>.yaml`，改 id / product_name /
10 色（整阶显式配置，见 §9 边界）/ modules，重跑构建。

## 4. 构建产物布局

```
oem/dist/
  manifest.json                 # 构建清单（brands 数组；无时间戳，可复现）
  <brand>/
    web/                        # 前端静态产物（vite build；index/assets）
    backend.env                 # 后端模块开关（与基础 .env 合并部署）
```

- `<title>` 由脚本改写为 `product_name`（源 `web/index.html` 零触碰）；
- manifest 条目：id / product_name / colors / modules / disabled_modules /
  artifacts 相对路径。

## 5. 前端品牌机制（构建期烘焙）

- `web/src/brand/brandConfig.ts`：模块加载期读 12 个 `VITE_OEM_*`
  （PRODUCT_NAME + 10 色 + DISABLED_PANELS），缺省值=现状逐字节一致——
  dev 模式与既有测试零行为变化；
- `web/src/admin/menuConfig.ts`：`ALL_MENU_ITEMS`（18 项）经
  `parseDisabledPanels(VITE_OEM_DISABLED_PANELS)` 过滤为 `MENU_ITEMS`——
  禁用面板菜单不渲染、深链 not-found；过滤后为空回落全量
  （`DEFAULT_ADMIN_PANEL` 越界保护）；非法 JSON fail-soft（warn + 全显）；
- RGB 推导（`PRIMARY_COLOR_RGB` 等）由 hex 计算，无独立 env。

## 6. 模块注册表（scripts/build_oem.py `MODULE_REGISTRY`）

| module_id | menu key | env flag | settings 字段 |
| --- | --- | --- | --- |
| `agent_market` | `agent_market` | `AGENT_MARKET_ENABLED` | `agent_market_enabled` |
| `tool_market` | `tool_market` | `TOOL_MARKET_ENABLED` | `tool_market_enabled` |
| `knowledge_base` | `knowledge_base` | `VECTOR_KB_MANAGEMENT_ENABLED` | `vector_kb_management_enabled` |

防漂移双保险：`tests/unit/test_build_oem.py` 交叉核对（menu key ⊆
menuConfig.ts `MenuKey`；env flag ⊆ `Settings.model_fields` 小写化）；
构建期 `validate_brand_menu` 同校验（注册表 menu key 不在 MenuKey → exit 1）。

## 7. 部署

1. **前端**：`oem/dist/<brand>/web/` 由任意静态服务器承载（hash 路由，无需
   服务端重写）；默认 vite base 为 `/`，子路径部署需自调 `vite.config.ts` 的
   `base`（见 §9）。
2. **后端**：`oem/dist/<brand>/backend.env` 与基础 `.env` **合并**使用——
   settings 为 pydantic BaseSettings（字段名大写即 env 名）；`get_settings`
   带 `@lru_cache`，**改 env 必须重启进程**。
3. 开关语义：模块 false → 对应路由不注册（端点 404，`application_factory.py`
   门控块）+ 前端菜单不渲染（构建期烘焙）。

## 8. 验收映射（实证见 drill-record.md）

| 验收（roadmap §7 B5） | 实证 |
| --- | --- |
| 单次构建出 2 套品牌 | `python scripts/build_oem.py` 双产物 + manifest（16 项产物断言 PASS） |
| 关闭模块端点 404 | 双 uvicorn 对比：品牌 A 三端点 200 / 品牌 B 三端点 404 |
| 关闭模块菜单不渲染 | `menuConfig.oem.test.ts` 6 例 + acmeflow bundle 烘焙断言（`["agent_market","knowledge_base","tool_market"]`） |

## 9. 已知边界

- **禁用面板 chunk 仍在产物中**：面板为静态 import 懒加载分包，构建期不可
  tree-shake；运行时经菜单/深链双口径不可达（菜单不渲染 + not-found）。
- **品牌锁缺失**：部署侧改 env 可破坏固化（演示级边界；移交项）。
- **构建直调 vite**：`npm run build` 的 tsc 步骤因 13 个既有测试文件类型错误
  exit 2（生产代码零错误），脚本绕行直调 `vite build`；typecheck 门禁独立维持
  「13 零新增」口径，不在本批次修。
- **10 色阶为手调值**（Material 蓝系/紫系非算法派生），新品牌须整阶显式配置。
- **产物 base 路径**：vite 默认 `/`，子路径部署需调整 base 重建。
- **浅色令牌深度重构未做**：主题浅色体系中性色不动，品牌仅主色族（用户裁决②）。

## 10. 移交项（不在 B5 范围）

1. Docker/BRAND-arg 镜像构建与 compose 编排（本批产物为本地静态产物）；
2. `wfp` CLI 子命令封装（现为 `scripts/build_oem.py` 直跑惯例）；
3. 许可证 enforcement（「不可去 logo」无技术强制；roadmap §12 #2 未决）；
4. 品牌锁（部署侧 env 固化校验）；
5. B4 计量配额闭环集成（并行会话实施中；OEM 产物暂不含，D-pre1 修订）；
6. 浅色令牌深度重构（如需品牌化中性色体系）；
7. 依赖裁剪/离线镜像（私有化交付形态，roadmap §12 #3 未决）。
