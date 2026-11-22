# =============================================================================
# 向量知识库节点执行器（engine/nodes/vector_knowledge_base_node_executor.py）
#
# 职责：实现 VECTOR_KNOWLEDGE_BASE 节点类型——在知识库集合上执行向量检索，
# 产出文本片段集合（CHG-03，design §2.2.2）。
# 设计意图（对应 vector_knowledge_base_node spec FR-17~FR-23 / design §2.2.2）：
#   1. query 解析互斥分支（design §2.2.1）：
#      manual → query_value 原文逐字符透传（AC-13）；
#      reference → 装配 fail-fast（missing_is_none=False），缺失/未配置引用
#      判 NODE_INPUT_REFERENCE_MISSING 失败（AC-20），非字符串值确定性
#      序列化为 JSON 文本（AC-15）；
#   2. 检索委托集成层抽象（integrations.vector_knowledge_base），
#      api/local 模式按生效 embedding 模式选择（AC-25/AC-26）；
#      生效模型解析绑定优先（知识库绑定 > 节点 embedding_model 兼容
#      占位，AC-51/AC-52）：绑定缺失（管理开关未启用/知识库未建/解析
#      异常）→ 确定性回退节点字段；均缺失 → 确定性失败
#      （VECTOR_KB_EMBEDDING_BINDING_REQUIRED，AC-40）；
#   3. 检索链路不可用 → 节点失败（NODE_VECTOR_*，INV-D07：检索失败 ≠ 空结果）；
#      空 chunks → 合法成功（FR-22/AC-39）；降级信息入 metadata
#      （rerank_degraded/local_degraded + reason，AC-27/AC-31）；
#   4. 输出为片段列表（逐条 dict），执行器不做二次加工，仅按 top_k 截断
#      （AC-21/AC-24/AC-40）；
#   5. 命名输出写回由调度器统一执行，执行器自身不写共享状态/检查点
#      （INV-D08）。
# =============================================================================

import json
import time
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from orchestration_platform.domain.errors import (
    NODE_INPUT_REFERENCE_MISSING,
    VECTOR_KB_EMBEDDING_BINDING_REQUIRED,
    VECTOR_KB_STORAGE_UNAVAILABLE,
    DomainError,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    ModelLoadingConfig,
    VectorKnowledgeBaseNodeConfiguration,
)
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    AssemblyOutcome,
    assemble_single_reference,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.integrations.vector_knowledge_base.local_backend import (
    LocalKnowledgeBaseRetriever,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    RetrievalOutcome,
    VectorRetrievalError,
)
from orchestration_platform.integrations.vector_knowledge_base.retriever import (
    HttpKnowledgeBaseRetriever,
    VectorKnowledgeBaseRetriever,
)

if TYPE_CHECKING:
    from orchestration_platform.integrations.vector_kb_management.models import (
        AnnotationInput,
        KBEmbeddingBinding,
        KnowledgeBase,
        TextWriteOutcome,
    )


class VectorKnowledgeBaseNodeExecutor:
    """向量知识库节点执行器：解析 query → 委托检索 → 透出片段集合。"""

    @property
    def node_type(self) -> NodeType:
        return NodeType.VECTOR_KNOWLEDGE_BASE

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行向量检索并透出片段集合（无直写状态，INV-D08）。"""
        start_time = time.perf_counter()
        config: VectorKnowledgeBaseNodeConfiguration = (
            context.node_definition.configuration  # type: ignore[assignment]
        )

        # 0. 模式分派（vkb_write_mode / design §2.1.3(a)）：mode != "write"
        #    （缺省/retrieve）→ 既有检索路径逐字不变；mode == "write" →
        #    写入分支编排（薄编排，INV-17）。
        if config.mode == "write":
            return await self._execute_write(config, context)

        def elapsed_ms() -> int:
            return int((time.perf_counter() - start_time) * 1000)

        # 1. query 解析（互斥分支，AC-13/AC-14/AC-15/AC-20）
        query = self._resolve_query(config, context)
        if isinstance(query, AssemblyFailure):
            return NodeExecutionResult(
                success=False,
                error_code=query.error_code,
                error_message=query.error_message,
                duration_ms=elapsed_ms(),
            )

        # 2. 生效模型解析（绑定优先 AC-51；字段回退 AC-52；均缺失 →
        #    确定性失败，AC-40）
        effective_model = self._resolve_effective_model(config)
        if effective_model is None:
            return NodeExecutionResult(
                success=False,
                error_code=VECTOR_KB_EMBEDDING_BINDING_REQUIRED,
                error_message=(
                    f"知识库 {config.knowledge_base_id} 未绑定 Embedding 模型"
                    "且节点 embedding_model 未配置：请先在知识库管理后台绑定"
                    "模型（AC-40/AC-52）"
                ),
                duration_ms=elapsed_ms(),
            )

        # 3. 检索委托（api/local 模式分发，AC-25/AC-26）
        retriever = self._resolve_retriever(config)
        try:
            outcome = retriever.retrieve(
                knowledge_base_id=config.knowledge_base_id,
                query=query,
                top_k=config.top_k,
                embedding_model=effective_model,
                rerank_model=config.rerank_model,
            )
        except VectorRetrievalError as error:
            # 检索链路不可用 → 节点失败（INV-D07：不得以空结果冒充成功）
            return NodeExecutionResult(
                success=False,
                error_code=error.error_code,
                error_message=error.error_message,
                duration_ms=elapsed_ms(),
            )

        # 4. 结果透出：不做二次加工，仅按 top_k 截断（AC-21/AC-24/AC-40）
        chunks: List[JsonValue] = [
            chunk.model_dump(mode="json") for chunk in outcome.chunks[: config.top_k]
        ]
        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.output_name,
            output_value=chunks,
            metadata=self._build_metadata(outcome),
            duration_ms=elapsed_ms(),
        )

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------

    def _resolve_query(
        self,
        config: VectorKnowledgeBaseNodeConfiguration,
        context: NodeExecutionContext,
    ) -> Union[str, AssemblyFailure]:
        """按 query_mode 互斥分支解析查询文本。

        - manual：query_value 原文逐字符透传（None/空串 → 空串，AC-13）；
        - reference：装配 fail-fast（missing_is_none=False）——
          未配置引用 / 引用目标缺失 → NODE_INPUT_REFERENCE_MISSING（AC-20）；
          装配值 None → 空串；非字符串 → JSON 确定性序列化（AC-15）。
        """
        if config.query_mode == "reference":
            if not config.query_reference:
                return AssemblyFailure(
                    error_code=NODE_INPUT_REFERENCE_MISSING,
                    error_message=(
                        "query_reference 未配置：reference 模式必须提供"
                        "继承录入引用表达式"
                    ),
                )
            snapshot = dict(context.shared_state.node_outputs)
            outcome: AssemblyOutcome = assemble_single_reference(
                config.query_reference, snapshot, missing_is_none=False
            )
            if isinstance(outcome, AssemblyFailure):
                return outcome
            value = outcome.value
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return config.query_value or ""

    def _resolve_effective_model(
        self, config: VectorKnowledgeBaseNodeConfiguration
    ) -> Optional[ModelLoadingConfig]:
        """生效 embedding 模型解析（绑定优先 → 字段回退，AC-51/AC-52）。

        - 知识库有绑定 → 绑定转换后生效（节点字段为兼容占位，不覆盖）；
        - 绑定缺失（异常数据/开关未启用）→ 确定性回退节点字段；
        - 均缺失 → None（调用方确定性失败，AC-40）。
        """
        binding = self._try_resolve_binding(config.knowledge_base_id)
        if binding is not None:
            from orchestration_platform.integrations.vector_kb_management.service import (
                binding_to_model_config,
            )

            return binding_to_model_config(binding)
        return config.embedding_model

    def _resolve_effective_mode(
        self, config: VectorKnowledgeBaseNodeConfiguration
    ) -> str:
        """生效 embedding 模式派生（绑定优先 → 字段回退；缺省 local）。"""
        binding = self._try_resolve_binding(config.knowledge_base_id)
        if binding is not None:
            return binding.mode
        field_model = config.embedding_model
        if field_model is not None:
            return field_model.mode
        return "local"

    def _try_resolve_binding(self, knowledge_base_id: str) -> Optional["KBEmbeddingBinding"]:
        """知识库绑定解析 seam（只读零写入，AC-51/AC-53 三路径一致）。

        - 管理开关未启用（settings.vector_kb_management_enabled 缺省
          False）/ 知识库未建 / 解析链路异常 → None（确定性回退节点
          字段，AC-52；fail-safe 不阻断既有检索路径）；
        - 只读：repository.get 零写入（绑定解析零写入不变式）；
        - 测试 seam：monkeypatch 本方法注入绑定（对齐既有执行器测试
          monkeypatch 范式）。
        """
        try:
            from orchestration_platform.integrations.vector_kb_management.config import (
                from_settings,
            )

            management_config = from_settings()
            if not management_config.enabled:
                return None
            from orchestration_platform.integrations.vector_kb_management.repository import (
                SqliteKnowledgeBaseRepository,
            )

            knowledge_base = SqliteKnowledgeBaseRepository(
                management_config.storage_root
            ).get(knowledge_base_id)
            if knowledge_base is None:
                return None
            return knowledge_base.embedding_model
        except Exception:
            return None

    def _resolve_retriever(
        self, config: VectorKnowledgeBaseNodeConfiguration
    ) -> VectorKnowledgeBaseRetriever:
        """按生效 embedding 模式选择检索器（绑定优先，AC-25/AC-26/AC-51）。"""
        if self._resolve_effective_mode(config) == "local":
            return LocalKnowledgeBaseRetriever()
        return HttpKnowledgeBaseRetriever()

    @staticmethod
    def _build_metadata(outcome: RetrievalOutcome) -> Dict[str, JsonValue]:
        """降级信息入 metadata（AC-27/AC-31：降级必须可见，禁止静默）。"""
        metadata: Dict[str, JsonValue] = {}
        if outcome.rerank_degraded:
            metadata["rerank_degraded"] = True
            if outcome.rerank_degraded_reason:
                metadata["rerank_degraded_reason"] = outcome.rerank_degraded_reason
        if outcome.local_degraded:
            metadata["local_degraded"] = True
            if outcome.local_degraded_reason:
                metadata["local_degraded_reason"] = outcome.local_degraded_reason
        return metadata

    # ------------------------------------------------------------------
    # 写入模式（vkb_write_mode / design §2.2.2(6)、§1.1.3(A)）
    # ------------------------------------------------------------------

    async def _execute_write(
        self,
        config: VectorKnowledgeBaseNodeConfiguration,
        context: NodeExecutionContext,
    ) -> NodeExecutionResult:
        """写入分支编排（薄编排：分派/装配/解析/报告组装/成败裁决）。

        - **不**自行分片/向量化/写存储（INV-17），全部委托 ``TextWriter`` 门面
          （节点侧唯一写入调用点，OP-11）；
        - 失败（装配/绑定缺失/门面异常）→ **集合零写入**（不半写）；
        - 裁决（OP-09）：可写且存在成功条目 → ``success=True``（报告为命名输出
          值，status=partial|succeeded）；整体不可写/全部失败 → ``success=False``
          （失败清单经门面异常或报告承载）；
        - 幂等键：缺省 ``f"{knowledge_base_id}:{node_id}"``（同键覆盖，AC-34/35），
          可经 ``write_document_id`` 显式覆盖（追加语义）；
        - 不直写 ``node_outputs``/检查点（AC-57）；副作用（集合写入）与命名输出
          （``NodeExecutionResult``）严格分离（OP-09）。
        """
        start_time = time.perf_counter()

        def elapsed_ms() -> int:
            return int((time.perf_counter() - start_time) * 1000)

        # 1. 待写入文本装配（与 _resolve_query 同构，AC-13/AC-14/AC-19/AC-21）
        text = self._resolve_write_text(config, context)
        if isinstance(text, AssemblyFailure):
            return NodeExecutionResult(
                success=False,
                error_code=text.error_code,
                error_message=text.error_message,
                duration_ms=elapsed_ms(),
            )

        # 2. 生效向量化来源解析（绑定优先 AC-36 → 字段回退 AC-37；均缺失 AC-38）
        binding = self._resolve_write_binding(config)
        if binding is None:
            return NodeExecutionResult(
                success=False,
                error_code=VECTOR_KB_EMBEDDING_BINDING_REQUIRED,
                error_message=(
                    f"知识库 {config.knowledge_base_id} 未绑定 Embedding 模型"
                    "且节点 embedding_model 未配置：请先在知识库管理后台绑定"
                    "模型（AC-38/AC-52）"
                ),
                duration_ms=elapsed_ms(),
            )

        # 3. 生效分片策略解析（节点显式 ?? 知识库默认 ?? 服务默认，AC-27~AC-29）
        knowledge_base = self._try_resolve_knowledge_base(config.knowledge_base_id)
        kb_default_strategy = (
            knowledge_base.default_chunk_strategy
            if knowledge_base is not None
            else None
        )
        effective_strategy = (
            config.write_chunk_strategy
            if config.write_chunk_strategy is not None
            else kb_default_strategy
        )

        # 4. 幂等键派生（缺省 kb:node_id → 同键覆盖；显式覆盖 → 追加语义）
        document_id = (
            config.write_document_id
            if config.write_document_id
            else f"{config.knowledge_base_id}:{context.node_definition.node_id}"
        )

        # 5. 委托门面写入（唯一写入调用点；延迟导入保最小顶层依赖）
        from orchestration_platform.integrations.vector_kb_management.ingestion import (
            TextWriter,
        )

        chunk_params = config.write_chunk_params
        annotations = self._resolve_annotations(config)
        writer = TextWriter()
        try:
            outcome: "TextWriteOutcome" = writer.write_text(
                knowledge_base_id=config.knowledge_base_id,
                binding=binding,
                text=text,
                document_id=document_id,
                strategy=effective_strategy,
                max_chunk_length=(
                    chunk_params.max_chunk_length
                    if chunk_params is not None
                    else None
                ),
                overlap=(
                    chunk_params.overlap if chunk_params is not None else None
                ),
                similarity_threshold=(
                    chunk_params.similarity_threshold
                    if chunk_params is not None
                    else None
                ),
                hierarchy=(
                    chunk_params.hierarchy if chunk_params is not None else None
                ),
                annotation_origin=(
                    annotations.origin if annotations is not None else None
                ),
                annotation_published_at=(
                    annotations.published_at if annotations is not None else None
                ),
                annotation_custom=(
                    annotations.custom if annotations is not None else None
                ),
            )
        except DomainError as error:
            # 整体不可写（超上界/空文本/无来源未兜底/委托失败）→ 集合零写入
            return NodeExecutionResult(
                success=False,
                error_code=error.code,
                error_message=error.message,
                duration_ms=elapsed_ms(),
            )

        # 6. 成败裁决（OP-09）：有成功条目 → 成功（报告为命名输出值）；
        #    全部失败（无成功条目）→ 节点失败（失败清单已在上游逐条登记）。
        if outcome.upserted_count <= 0:
            first_reason = outcome.failed[0].reason if outcome.failed else ""
            return NodeExecutionResult(
                success=False,
                error_code=VECTOR_KB_STORAGE_UNAVAILABLE,
                error_message=(
                    f"写入全部失败（0/{outcome.chunk_count} 条成功）：{first_reason}"
                ),
                duration_ms=elapsed_ms(),
            )
        report: Dict[str, JsonValue] = outcome.model_dump(mode="json")
        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.output_name,
            output_value=report,
            metadata=self._build_write_metadata(outcome),
            duration_ms=elapsed_ms(),
        )

    def _resolve_write_text(
        self,
        config: VectorKnowledgeBaseNodeConfiguration,
        context: NodeExecutionContext,
    ) -> Union[str, AssemblyFailure]:
        """按 write_source_mode 互斥分支解析待写入文本（与 ``_resolve_query`` 同构）。

        - manual：write_text 原文逐字符透传（无模板插值，AC-21）；
        - reference：装配 fail-fast（missing_is_none=False）——未配置引用 /
          引用目标缺失 → NODE_INPUT_REFERENCE_MISSING（AC-19）；
          装配值 None → 空串；非字符串 → JSON 确定性序列化。
        """
        if config.write_source_mode == "reference":
            if not config.write_reference:
                return AssemblyFailure(
                    error_code=NODE_INPUT_REFERENCE_MISSING,
                    error_message=(
                        "write_reference 未配置：write reference 模式必须提供"
                        "继承录入引用表达式"
                    ),
                )
            snapshot = dict(context.shared_state.node_outputs)
            outcome: AssemblyOutcome = assemble_single_reference(
                config.write_reference, snapshot, missing_is_none=False
            )
            if isinstance(outcome, AssemblyFailure):
                return outcome
            value = outcome.value
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return config.write_text or ""

    def _resolve_write_binding(
        self, config: VectorKnowledgeBaseNodeConfiguration
    ) -> Optional["KBEmbeddingBinding"]:
        """生效向量化来源解析（绑定优先 → 字段回退，AC-36/AC-37/AC-38）。

        - 知识库有绑定 → 用绑定（节点字段不覆盖，AC-36）；
        - 绑定缺失 → 节点 embedding_model 经 ``model_config_to_binding`` 转绑定
          形态（AC-37）；
        - 均缺失 → None（调用方确定性失败 VECTOR_KB_EMBEDDING_BINDING_REQUIRED）；
        - 与检索路径同一绑定解析（AC-61 双模式协同一致）。
        """
        binding = self._try_resolve_binding(config.knowledge_base_id)
        if binding is not None:
            return binding
        if config.embedding_model is not None:
            from orchestration_platform.integrations.vector_kb_management.service import (
                model_config_to_binding,
            )

            return model_config_to_binding(config.embedding_model)
        return None

    def _resolve_annotations(
        self, config: VectorKnowledgeBaseNodeConfiguration
    ) -> Optional["AnnotationInput"]:
        """解析节点标注配置（vkb_doc_annotation / CHG-09 / DC-15）。

        - ``annotation_enabled`` 显式为 False → None（节点级不传标注）；
        - 无任何标注入参且 ``annotation_enabled`` 非 True → None（零行为变化）；
        - 否则返回 ``AnnotationInput``（字段可为 None，"工作流写入"缺省由门面裁决）；
        - 实际是否产出 ``annotations`` 由全局开关（门面内 `_annotations_enabled`）裁决
          （INV-07 / AC-61）。
        """
        if config.annotation_enabled is False:
            return None
        has_input = any(
            value is not None
            for value in (
                config.annotation_origin,
                config.annotation_published_at,
                config.annotation_custom,
            )
        )
        if not has_input and config.annotation_enabled is not True:
            return None
        from orchestration_platform.integrations.vector_kb_management.models import (
            AnnotationInput,
        )

        return AnnotationInput(
            origin=config.annotation_origin,
            published_at=config.annotation_published_at,
            custom=config.annotation_custom,
        )

    def _try_resolve_knowledge_base(
        self, knowledge_base_id: str
    ) -> Optional["KnowledgeBase"]:
        """知识库实体解析 seam（只读零写入；复用管理开关 + 仓储范式）。

        - 管理开关未启用 / 知识库未建 / 异常 → None（fail-safe 不阻断，
          缺省分片策略回退服务默认，AC-28）；
        - 只读：repository.get 零写入。
        """
        try:
            from orchestration_platform.integrations.vector_kb_management.config import (
                from_settings,
            )

            management_config = from_settings()
            if not management_config.enabled:
                return None
            from orchestration_platform.integrations.vector_kb_management.repository import (
                SqliteKnowledgeBaseRepository,
            )

            return SqliteKnowledgeBaseRepository(
                management_config.storage_root
            ).get(knowledge_base_id)
        except Exception:
            return None

    @staticmethod
    def _build_write_metadata(outcome: "TextWriteOutcome") -> Dict[str, JsonValue]:
        """写入降级信息入 metadata（AC-41/AC-48：降级必须可见，禁止静默）。"""
        metadata: Dict[str, JsonValue] = {}
        if outcome.local_degraded:
            metadata["local_degraded"] = True
            if outcome.local_degraded_reason:
                metadata["local_degraded_reason"] = outcome.local_degraded_reason
        return metadata
