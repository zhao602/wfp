# =============================================================================
# 用户自主开发脚手架常量（market/dev_scaffold.py）
#
# 职责（agent_market_user_development T1 / CHG-01 / DC-01）：
#   1. 开发脚手架领域模型：DevScaffoldTemplate / DevFileTemplate（字段对齐 spec §9.1）；
#   2. 模块级不可变常量 DEV_SCAFFOLDS（≥1 条目，五要素：
#      目录结构 / 文件格式 / 接口定义 / 元数据配置 / 依赖与运行环境）；
#   3. required_files 与 L2 判定语义同源：不硬编码第二份清单，
#      由模块常量 _MANIFEST_FILE / _ENTRY_POINT 单一来源派生（T1.3），
#      与权威实现的一致性由 T6 对拍测试保证（AC-DEV-03）；
#   4. 导入期 fail-fast 校验：不合规导入即抛 RuntimeError
#      （对齐 market/agent_templates.py 范式，T1.2）。
# 约束：Python 3.10 兼容；零第三方新增依赖；本期只校验不执行用户代码（INV-DEV-15）。
# =============================================================================

import re
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# L2 判定语义单一来源（AC-DEV-03 / DC-01 / T1.3）
# ---------------------------------------------------------------------------
# L2（NodePackageValidator）判定语义：必需文件 = ["manifest.yaml"] + manifest.entry_point
# （缺省 "node.py"）。脚手架侧不硬编码第二份清单：以下模块常量为唯一派生源，
# 同源用于 required_files、file_templates 路径与 manifest 样例一致性；
# 与权威实现的一致性由 tests/unit/test_dev_scaffold.py 对拍测试保证（T6.1）。
_MANIFEST_FILE = "manifest.yaml"
_ENTRY_POINT = "node.py"

# 版本号语义化格式正则（主.次.修订，与 ManifestValidator 口径一致）
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

# 五要素对应字段（目录结构/文件格式/接口定义/元数据配置/依赖与运行环境，T1.1/T1.2）
_FIVE_ELEMENT_FIELDS = (
    "directory_structure",
    "manifest_spec",
    "interface_contract",
    "dependency_and_runtime",
)


class DevFileTemplate(BaseModel):
    """脚手架文件内容模板（design §2.3.2，文件格式要素）。

    content 内联完整文件内容（无 ZIP 下载端点，DC-01）；
    required 标记是否必需文件（与所属脚手架 required_files 一致）。
    """

    path: str = Field(description="文件相对路径（如 manifest.yaml / node.py）")
    content: str = Field(description="文件内容模板（内联文本，非空）")
    required: bool = Field(default=True, description="是否必需文件")


class DevScaffoldTemplate(BaseModel):
    """开发脚手架模板（spec §9.1 / design §2.3.2，CHG-01）。

    五要素映射：目录结构=directory_structure、文件格式=file_templates[].content、
    接口定义=interface_contract、元数据配置=manifest_spec、
    依赖与运行环境=dependency_and_runtime。
    """

    scaffold_id: str = Field(description="脚手架全局唯一标识（如 agent-node-basic）")
    name: str = Field(description="脚手架名称")
    version: str = Field(description="脚手架版本（语义化格式 主.次.修订）")
    summary: str = Field(description="摘要短文案")
    directory_structure: str = Field(description="目录结构（文本树，五要素之一）")
    required_files: List[str] = Field(
        description="必需文件清单（与 L2 判定语义同源，至少含 manifest.yaml 与入口文件）"
    )
    optional_files: List[str] = Field(
        default_factory=list,
        description="可选文件清单（如 README.md / requirements.txt）",
    )
    manifest_spec: str = Field(
        description="元数据字段规范（与 PluginManifest 字段一致，五要素之一）"
    )
    interface_contract: str = Field(
        description="接口契约说明（PluginNodeBase 生命周期，五要素之一）"
    )
    dependency_and_runtime: str = Field(
        description="依赖声明与运行环境要求说明（五要素之一）"
    )
    file_templates: List[DevFileTemplate] = Field(
        default_factory=list, description="文件内容模板集合（文件格式要素，内联非空）"
    )
    fetch_hint: str = Field(description="获取指引（文本内联说明，无 ZIP 下载端点）")


# ---------------------------------------------------------------------------
# 五要素文本（agent-node-basic，DC-01）
# ---------------------------------------------------------------------------

_DIRECTORY_STRUCTURE = """my-node/
├── manifest.yaml      # 必需：节点元数据声明（与 PluginManifest 字段一致）
├── node.py            # 必需：实现入口（须与 manifest.entry_point 一致）
├── README.md          # 可选：节点说明
└── requirements.txt   # 可选：依赖清单（manifest.dependencies 非空时须提供）
"""

_MANIFEST_SPEC = """manifest.yaml 与既有 PluginManifest 字段一一对应（9 字段）：
- node_name：节点名称（^[a-z0-9-]+$，全局唯一）
- version：版本号（语义化格式 主.次.修订）
- description：节点说明（可空）
- input_schema：输入模式（字段名 → 类型；为空时 L1 校验报 warning）
- output_schema：输出模式（字段名 → 类型；为空时 L1 校验报 warning）
- dependencies：依赖清单（Python 包名；声明依赖时须在 requirements.txt 中列明）
- declared_file_paths：文件路径访问范围声明
- declared_network_targets：网络访问目标声明
- entry_point：实现入口文件（缺省 node.py）
约束：declared_file_paths 与 declared_network_targets 至少一项非空，
否则 L1 校验报「权限声明缺失」error。
"""

_INTERFACE_CONTRACT = """节点实现须继承既有 PluginNodeBase（plugin_sdk/plugin_node_base.py，契约冻结），
实现生命周期钩子：
- setup(config: dict) -> None：初始化钩子（每实例调用一次）；
- execute(inputs: dict) -> dict：执行钩子（可多次调用，须无跨调用可变副作用依赖）；
- teardown() -> None：清理钩子（默认空实现，execute 异常时仍保证调用）。
输入/输出模式在 manifest.yaml 的 input_schema / output_schema 中声明。
"""

_DEPENDENCY_AND_RUNTIME = """依赖声明：manifest.yaml 的 dependencies（Python 包名清单）；
声明依赖时须提供 requirements.txt 明文清单，且二者保持一致。
运行环境：Python 3.10+；入口文件为 UTF-8 编码的 Python 源码。
安全边界：平台仅对提交包做静态校验与人工审核
（L1 元数据 / L2 目录结构 / L3 静态安全三层），不执行用户代码；
入口文件中的危险 API 调用与未声明网络外联会被 L3 校验标记为 error。
"""

_FETCH_HINT = """脚手架文件内容经 DV-1/DV-2 接口内联下发（file_templates[].content），
无 ZIP 下载端点（DC-01）：获取详情后按 directory_structure 将各文件模板
内容复制/保存为本地目录，修改 node_name / version 等元数据后，
将目录打包为 ZIP（须包含 manifest.yaml 与入口文件）再经「开发包提交向导」提交。
"""

# ---------------------------------------------------------------------------
# 文件内容模板（manifest 样例须可通过 L1/L3 校验：权限声明非空、无危险调用）
# ---------------------------------------------------------------------------

_MANIFEST_TEMPLATE_CONTENT = """# 节点元数据声明（字段与 PluginManifest 一致；本样例可直接通过 L1 校验）
node_name: my-first-node          # 节点名称（小写字母/数字/连字符，全局唯一）
version: "1.0.0"                  # 版本号（语义化格式 主.次.修订）
description: 我的第一个自定义 Agent 节点（脚手架示例）
input_schema:                     # 输入模式（字段名 → 类型）
  query: string
output_schema:                    # 输出模式（字段名 → 类型）
  result: string
dependencies: []                  # 依赖清单（Python 包名；声明时须提供 requirements.txt）
declared_file_paths:              # 文件路径访问范围声明（与网络目标至少声明一项）
  - ./data/
declared_network_targets: []      # 网络访问目标声明
entry_point: node.py              # 实现入口文件（缺省 node.py）
"""

_NODE_TEMPLATE_CONTENT = '''"""节点实现入口：继承 PluginNodeBase 实现生命周期钩子。"""

from typing import Any, Dict

from orchestration_platform.plugin_sdk.plugin_node_base import PluginNodeBase


class MyFirstNode(PluginNodeBase):
    """示例节点：setup 初始化、execute 实现业务逻辑。"""

    def setup(self, config: Dict[str, Any]) -> None:
        self._query_prefix = str(config.get("query_prefix", ""))

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        query = str(inputs.get("query", ""))
        return {"result": f"{self._query_prefix}{query}"}
'''

_README_TEMPLATE_CONTENT = """# my-first-node

自定义 Agent 节点示例（由 agent-node-basic 脚手架生成）。

## 文件说明
- manifest.yaml：节点元数据声明（与 PluginManifest 字段一致）
- node.py：实现入口（继承 PluginNodeBase）
- requirements.txt：依赖清单（声明 dependencies 时须提供）

## 提交流程
1. 修改 manifest.yaml 中的 node_name / version 等元数据；
2. 在 node.py 中实现业务逻辑；
3. 将本目录打包为 ZIP（须包含 manifest.yaml 与 node.py）；
4. 经「开发包提交向导」上传并查看三层校验报告。
"""

_REQUIREMENTS_TEMPLATE_CONTENT = """# 依赖清单（与 manifest.yaml 的 dependencies 保持一致；无依赖可保留本注释）
"""

# ---------------------------------------------------------------------------
# 模块级不可变常量（T1.1；required_files 由 _MANIFEST_FILE/_ENTRY_POINT 派生，T1.3）
# ---------------------------------------------------------------------------

DEV_SCAFFOLDS: Tuple[DevScaffoldTemplate, ...] = (
    DevScaffoldTemplate(
        scaffold_id="agent-node-basic",
        name="Agent 节点基础工程脚手架",
        version="1.0.0",
        summary="面向第三方开发者的最小可提交 Agent 节点工程脚手架（manifest + 入口模板）",
        directory_structure=_DIRECTORY_STRUCTURE,
        required_files=[_MANIFEST_FILE, _ENTRY_POINT],
        optional_files=["README.md", "requirements.txt"],
        manifest_spec=_MANIFEST_SPEC,
        interface_contract=_INTERFACE_CONTRACT,
        dependency_and_runtime=_DEPENDENCY_AND_RUNTIME,
        file_templates=[
            DevFileTemplate(
                path=_MANIFEST_FILE,
                content=_MANIFEST_TEMPLATE_CONTENT,
                required=True,
            ),
            DevFileTemplate(
                path=_ENTRY_POINT,
                content=_NODE_TEMPLATE_CONTENT,
                required=True,
            ),
            DevFileTemplate(
                path="README.md",
                content=_README_TEMPLATE_CONTENT,
                required=False,
            ),
            DevFileTemplate(
                path="requirements.txt",
                content=_REQUIREMENTS_TEMPLATE_CONTENT,
                required=False,
            ),
        ],
        fetch_hint=_FETCH_HINT,
    ),
)


def _validate_scaffolds() -> None:
    """导入期校验（T1.2，fail-fast，DC-01）。

    校验项：DEV_SCAFFOLDS 非空、scaffold_id 唯一且非空、name/summary/fetch_hint 非空、
    version 语义化、required_files 必含 manifest.yaml 与入口文件（L2 判定语义，AC-DEV-03）、
    五要素非空、文件模板路径唯一/内容非空/required 标记与清单一致、
    每个必需文件均有内容模板。
    任何违规 → 抛 RuntimeError（fail-fast，进程启动即失败）。
    """
    if not DEV_SCAFFOLDS:
        raise RuntimeError("DEV_SCAFFOLDS 不能为空（至少一条开发脚手架）")

    scaffold_ids = [scaffold.scaffold_id for scaffold in DEV_SCAFFOLDS]
    if len(scaffold_ids) != len(set(scaffold_ids)):
        raise RuntimeError(f"开发脚手架 scaffold_id 必须唯一，实际为 {scaffold_ids}")

    for scaffold in DEV_SCAFFOLDS:
        if not scaffold.scaffold_id:
            raise RuntimeError("开发脚手架 scaffold_id 不能为空")
        if not scaffold.name:
            raise RuntimeError(f"开发脚手架 {scaffold.scaffold_id} name 不能为空")
        if not scaffold.summary:
            raise RuntimeError(f"开发脚手架 {scaffold.scaffold_id} summary 不能为空")
        if not _SEMVER_PATTERN.match(scaffold.version):
            raise RuntimeError(
                f"开发脚手架 {scaffold.scaffold_id} version 须语义化格式（主.次.修订），"
                f"实际为 {scaffold.version}"
            )
        # required_files 与 L2 判定语义同源（T1.3 / AC-DEV-03）
        if _MANIFEST_FILE not in scaffold.required_files:
            raise RuntimeError(
                f"开发脚手架 {scaffold.scaffold_id} required_files 必含 {_MANIFEST_FILE}"
            )
        if _ENTRY_POINT not in scaffold.required_files:
            raise RuntimeError(
                f"开发脚手架 {scaffold.scaffold_id} required_files 必含入口文件 {_ENTRY_POINT}"
            )
        # 五要素非空（T1.1 验收 / AC-DEV-01/02）
        for field_name in _FIVE_ELEMENT_FIELDS:
            if not getattr(scaffold, field_name):
                raise RuntimeError(
                    f"开发脚手架 {scaffold.scaffold_id} 五要素字段 {field_name} 不能为空"
                )
        if not scaffold.file_templates:
            raise RuntimeError(
                f"开发脚手架 {scaffold.scaffold_id} file_templates 不能为空（文件格式要素）"
            )
        if not scaffold.fetch_hint:
            raise RuntimeError(f"开发脚手架 {scaffold.scaffold_id} fetch_hint 不能为空")
        # 文件模板一致性与 required 标记对齐
        template_paths = [template.path for template in scaffold.file_templates]
        if len(template_paths) != len(set(template_paths)):
            raise RuntimeError(
                f"开发脚手架 {scaffold.scaffold_id} file_templates 路径必须唯一"
            )
        declared_files = set(scaffold.required_files) | set(scaffold.optional_files)
        for template in scaffold.file_templates:
            if not template.path:
                raise RuntimeError(
                    f"开发脚手架 {scaffold.scaffold_id} 存在空路径文件模板"
                )
            if not template.content:
                raise RuntimeError(
                    f"开发脚手架 {scaffold.scaffold_id} 文件模板 {template.path} "
                    f"content 不能为空"
                )
            if template.path not in declared_files:
                raise RuntimeError(
                    f"开发脚手架 {scaffold.scaffold_id} 文件模板 {template.path} "
                    f"未在 required_files/optional_files 中声明"
                )
            if template.required != (template.path in set(scaffold.required_files)):
                raise RuntimeError(
                    f"开发脚手架 {scaffold.scaffold_id} 文件模板 {template.path} "
                    f"required 标记与文件清单不一致"
                )
        for required_file in scaffold.required_files:
            if required_file not in set(template_paths):
                raise RuntimeError(
                    f"开发脚手架 {scaffold.scaffold_id} 必需文件 {required_file} "
                    f"缺少内容模板"
                )


def list_scaffolds() -> List[DevScaffoldTemplate]:
    """按预置顺序返回全部开发脚手架（DV-1 数据源）。"""
    return list(DEV_SCAFFOLDS)


def get_scaffold(scaffold_id: str) -> Optional[DevScaffoldTemplate]:
    """按 scaffold_id 查找开发脚手架；不存在返回 None（DV-2 数据源）。"""
    for scaffold in DEV_SCAFFOLDS:
        if scaffold.scaffold_id == scaffold_id:
            return scaffold
    return None


_validate_scaffolds()  # 模块导入即校验（fail-fast，DC-01 / T1.2）
