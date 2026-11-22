# =============================================================================
# 向量知识库检索集成包（integrations/vector_knowledge_base/）
#
# 职责：封装向量知识库检索的外部依赖调用（CHG-03，design §2.2.4）：
#   - models        —— 出参模型与统一检索异常（纯数据层，无 I/O）；
#   - retriever     —— 检索器抽象与 API（HTTP）模式实现；
#   - local_backend —— local 模式实现（运行时探测 + 确定性降级，OP-10）。
# 引擎层仅通过本包抽象（VectorKnowledgeBaseRetriever）依赖检索能力，
# 不直接接触 httpx/本地 SDK（架构分层：engine → integrations）。
# =============================================================================
