# =============================================================================
# 插件节点管理 REST API（api/routers/plugin_node_router.py）
#
# 职责：插件节点安装、列表、卸载 REST 端点。
# 设计意图（对应偏差修正 design 2.4）：
#   - GET    /api/v1/plugin/nodes           —— 已安装节点列表（含健康标记与版本）
#   - POST   /api/v1/plugin/nodes/install   —— 上传节点包（multipart），返回校验结果
#   - DELETE /api/v1/plugin/nodes/{node_name} —— 卸载确认
#   - plugin_sdk_enabled=False 时路由不注册（降级形态）
# =============================================================================

import logging
import os
import tempfile
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import DomainError
from orchestration_platform.engine.plugin_nodes.plugin_node_loader import (
    PluginNodeLoader,
)
from orchestration_platform.plugin_sdk.models import (
    PluginNodeRegistration,
    ValidationResult,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["插件节点管理"])

# 单例加载器（进程内）
_loader = PluginNodeLoader()


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------

class InstallResponse(BaseModel):
    """安装响应。"""

    registration: Optional[PluginNodeRegistration] = Field(
        default=None, description="注册信息（成功时有效）"
    )
    validation: ValidationResult = Field(description="校验结果")
    error_code: Optional[str] = Field(default=None, description="错误码（失败时有效）")
    error_message: Optional[str] = Field(default=None, description="错误说明")
    error_detail: Optional[dict] = Field(default=None, description="错误详情")


class UninstallResponse(BaseModel):
    """卸载响应。"""

    node_name: str = Field(description="节点名称")
    success: bool = Field(description="是否成功")


class PluginNodeListResponse(BaseModel):
    """节点列表响应。"""

    nodes: List[PluginNodeRegistration] = Field(description="已安装节点列表")
    total: int = Field(description="总数")


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.get("/plugin/nodes", response_model=PluginNodeListResponse)
async def list_plugin_nodes() -> PluginNodeListResponse:
    """列出已安装插件节点（含健康标记与版本）。"""
    nodes = _loader.list_installed()
    return PluginNodeListResponse(nodes=nodes, total=len(nodes))


@router.post("/plugin/nodes/install", response_model=InstallResponse)
async def install_plugin_node(
    package: UploadFile = File(..., description="节点包 ZIP 文件"),
) -> InstallResponse:
    """上传并安装节点包。

    接受 multipart 上传，将包解压到临时目录后加载。
    返回 manifest 校验结果与规范差异说明。
    """
    settings = get_settings()

    # 保存上传文件到临时目录
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = os.path.join(tmp_dir, "package.zip")
        with open(zip_path, "wb") as f:
            content = await package.read()
            f.write(content)

        # 解压
        package_dir = os.path.join(tmp_dir, "package")
        try:
            import zipfile
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(package_dir)
        except Exception as exc:
            return InstallResponse(
                validation=ValidationResult(
                    valid=False, errors=[f"解压失败: {exc}"]
                ),
                error_code="NODE_PACKAGE_INVALID",
                error_message="节点包解压失败",
            )

        # 加载节点包
        try:
            registration = _loader.load_package(package_dir)
            return InstallResponse(
                registration=registration,
                validation=ValidationResult(valid=True),
            )
        except DomainError as exc:
            _logger.warning("节点包安装失败: %s", exc.message)
            return InstallResponse(
                validation=ValidationResult(
                    valid=False, errors=[exc.message]
                ),
                error_code=exc.code,
                error_message=exc.message,
                error_detail=exc.details,
            )


@router.delete("/plugin/nodes/{node_name}", response_model=UninstallResponse)
async def uninstall_plugin_node(node_name: str) -> UninstallResponse:
    """卸载插件节点（逐实例 teardown 后移除注册）。"""
    registration = _loader.get_registration(node_name)
    if registration is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "NODE_NOT_FOUND", "error_message": f"节点 {node_name} 未安装"},
        )

    _loader.unload_package(node_name)
    return UninstallResponse(node_name=node_name, success=True)
