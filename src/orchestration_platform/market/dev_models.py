# =============================================================================
# 用户自主开发统一模型（market/dev_models.py）
#
# 职责（agent_market_user_development T2 / CHG-02 / DC-02 / DC-03）：
#   1. 三层校验统一报告模型：DevFinding / DevValidationReport（层次标注 + 定位 + 版本）；
#   2. 开发提交领域模型：DevSubmission（state 复用 ContributionState 七态，不新增并列状态机）；
#   3. 显式响应模型（DV-1~DV-8）：字段白名单显式构造，禁止 **dict 透传（INV-DEV-13 / INV-D-13），
#      响应不含 api_key / base_url / 超时类凭据字段。
# 约束：Python 3.10 兼容（禁 StrEnum/Self/TaskGroup/PEP695）；零第三方新增依赖。
# =============================================================================

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.market.dev_scaffold import DevFileTemplate
from orchestration_platform.mcp_tool_market.contribution.models import (
    StateTransitionRecord,
)


class DevFinding(BaseModel):
    """三层校验发现项（design §5.4.1 / DC-02）。

    统一 L1（元数据）/ L2（目录结构）/ L3（静态安全）三层发现项结构，
    映射自既有 ValidationResult.errors / ScanFinding，不改两既有模型。
    """

    layer: Literal["L1", "L2", "L3"] = Field(
        description="校验层次：L1 元数据 schema / L2 目录结构 / L3 静态安全"
    )
    rule_id: str = Field(description="规则标识")
    severity: Literal["error", "warning", "info"] = Field(
        description="严重级别（error 阻断；warning 不阻断）"
    )
    message: str = Field(description="发现说明")
    location: Optional[str] = Field(
        default=None, description="定位（文件:行号，可选）"
    )


class DevValidationReport(BaseModel):
    """统一校验报告（design §5.4.1）。

    valid 语义 = severity 为 error 的 findings 数为 0（warning 不阻断，FR-8）；
    error 字段为校验器自身异常说明（非「不安全」结论）。
    """

    report_id: str = Field(description="报告标识")
    valid: bool = Field(description="是否通过（error 数为 0 时 True）")
    layers: List[str] = Field(
        description="参与的校验层次（如 ['L1','L2','L3']）"
    )
    findings: List[DevFinding] = Field(
        default_factory=list, description="逐项发现（三层合并）"
    )
    scanner_version: str = Field(description="校验器/扫描器版本标识")
    checked_at: datetime = Field(description="校验时间")
    error: Optional[str] = Field(
        default=None, description="校验器异常说明（非「不安全」结论）"
    )


class DevSubmission(BaseModel):
    """开发提交主体（spec §9.2；state 取值复用 ContributionState 七态）。

    state_history 元素复用既有 StateTransitionRecord（不改）；
    validation_reports 追加留档、不覆盖（INV-D-18）。
    """

    submission_id: str = Field(description="提交全局唯一标识")
    developer_id: str = Field(description="开发者标识")
    node_name: str = Field(description="节点名称（小写字母/数字/连字符）")
    version: str = Field(description="版本号（语义化格式）")
    package_digest: str = Field(description="包内容 SHA-256 摘要")
    package_size: int = Field(description="包大小（字节，须在上限内）")
    file_count: int = Field(description="包文件数（须在上限内）")
    state: ContributionState = Field(description="当前状态（七态）")
    validation_reports: List[DevValidationReport] = Field(
        default_factory=list, description="校验报告列表（追加留档，不可篡改）"
    )
    rejection_reason: str = Field(
        default="", description="驳回/拒绝原因（驳回或校验失败时非空）"
    )
    state_history: List[StateTransitionRecord] = Field(
        default_factory=list, description="流转历史（按时间有序）"
    )
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="最后更新时间")


# ---------------------------------------------------------------------------
# 显式响应模型（T2.3；字段白名单构造，禁 **dict 透传，INV-D-13）
# ---------------------------------------------------------------------------


class DevScaffoldSummary(BaseModel):
    """DV-1 清单条目（scaffold_id/name/version/summary/required_files）。"""

    scaffold_id: str = Field(description="脚手架全局唯一标识")
    name: str = Field(description="脚手架名称")
    version: str = Field(description="脚手架版本（语义化格式）")
    summary: str = Field(description="摘要短文案")
    required_files: List[str] = Field(description="必需文件清单")


class DevScaffoldListResponse(BaseModel):
    """DV-1 响应：脚手架清单。"""

    scaffolds: List[DevScaffoldSummary] = Field(description="脚手架摘要列表")
    total: int = Field(description="脚手架总数")


class DevScaffoldDetailResponse(BaseModel):
    """DV-2 响应：脚手架详情（五要素全覆盖 + 文件模板内联 + 获取指引）。

    五要素映射：目录结构=directory_structure（含 required/optional files）、
    文件格式=file_templates[].content、接口定义=interface_contract、
    元数据配置=manifest_spec、依赖与运行环境=dependency_and_runtime。
    """

    scaffold_id: str = Field(description="脚手架全局唯一标识")
    name: str = Field(description="脚手架名称")
    version: str = Field(description="脚手架版本")
    summary: str = Field(description="摘要短文案")
    directory_structure: str = Field(description="目录结构（文本树）")
    required_files: List[str] = Field(description="必需文件清单")
    optional_files: List[str] = Field(description="可选文件清单")
    manifest_spec: str = Field(description="元数据字段规范（与 PluginManifest 一致）")
    interface_contract: str = Field(description="接口契约说明")
    dependency_and_runtime: str = Field(description="依赖声明与运行环境要求")
    file_templates: List[DevFileTemplate] = Field(
        description="文件内容模板（content 内联，无 ZIP 下载端点）"
    )
    fetch_hint: str = Field(description="获取指引（文本内联说明）")


class DevSubmissionResponse(BaseModel):
    """DV-3 响应：提交受理结果（提交标识 + 状态 + 结构化报告 + 摘要 + 规模）。"""

    submission_id: str = Field(description="提交标识")
    state: ContributionState = Field(description="提交后状态")
    accepted: bool = Field(description="是否受理通过（校验 error 数为 0）")
    validation_report: DevValidationReport = Field(description="本次校验报告")
    rejection_reason: str = Field(default="", description="驳回/拒绝原因")
    package_digest: str = Field(description="包内容 SHA-256 摘要")
    package_size: int = Field(description="包大小（字节）")
    file_count: int = Field(description="包文件数")


class DevSubmissionListResponse(BaseModel):
    """DV-4 响应：提交清单（按状态/节点名筛选；仓储读取异常降级空清单仍 2xx）。"""

    submissions: List[DevSubmission] = Field(description="提交列表")
    total: int = Field(description="提交总数（筛选后）")


class DevSubmissionDetailResponse(BaseModel):
    """DV-5 响应：提交详情（主体 + 最新报告 + 来源标注）。"""

    submission: DevSubmission = Field(description="提交主体（含全部报告与流转历史）")
    latest_report: Optional[DevValidationReport] = Field(
        default=None, description="最新校验报告（便捷访问）"
    )
    source_kind: str = Field(
        default="user_dev", description="来源标注（用户开发）"
    )


class DevTransitionResponse(BaseModel):
    """DV-6 响应：审核流转结果。"""

    submission_id: str = Field(description="提交标识")
    from_state: ContributionState = Field(description="源状态")
    to_state: ContributionState = Field(description="目标状态")
    success: bool = Field(description="是否成功")
    message: str = Field(default="", description="说明（如触发上架结果）")


class DevHistoryResponse(BaseModel):
    """DV-8 响应：流转历史（from/to/操作者/原因/时间，按时间有序）。"""

    submission_id: str = Field(description="提交标识")
    history: List[StateTransitionRecord] = Field(
        description="流转历史（按时间有序）"
    )
