# =============================================================================
# 领域错误码与异常定义（domain/errors.py）
#
# 职责：集中定义平台错误码常量与领域异常类，供校验、引擎、API 层统一使用。
# 设计意图：
#   1. 错误码语义化（WF_* / NODE_* / STATE_* / TRIGGER_* / EXAMPLE_* / INSTANCE_*），
#      便于前端与调用方按码分支处理；
#   2. 异常类携带错误码与结构化 details，API 层据此映射 HTTP 状态码
#      （校验类 422、状态冲突 409、未找到 404），见 design 2.5.6。
# =============================================================================

from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# 错误码常量
# ---------------------------------------------------------------------------

# 工作流定义与校验类（WF_*）
WF_YAML_SYNTAX_ERROR = "WF_YAML_SYNTAX_ERROR"            # YAML 语法错误（附行列号）
WF_UNSUPPORTED_VERSION = "WF_UNSUPPORTED_VERSION"        # 规范版本不支持
WF_DEFINITION_TOO_LARGE = "WF_DEFINITION_TOO_LARGE"      # 定义节点数超限
WF_CYCLIC_GRAPH = "WF_CYCLIC_GRAPH"                      # 拓扑存在非法环
WF_VALIDATION_FAILED = "WF_VALIDATION_FAILED"            # 校验未通过（聚合报告）
WF_NOT_FOUND = "WF_NOT_FOUND"                            # 工作流定义不存在
WF_STATE_CONFLICT = "WF_STATE_CONFLICT"                  # 发布态版本被改写（409；B11 发布态不可变守卫）
WF_NODE_EXECUTION_FAILED = "WF_NODE_EXECUTION_FAILED"    # 节点执行失败
WF_LOOP_NOT_CONVERGED = "WF_LOOP_NOT_CONVERGED"          # 循环未收敛（达最大迭代）
WF_PARALLEL_BRANCH_FAILED = "WF_PARALLEL_BRANCH_FAILED"  # 并行分支失败
WF_INSTANCE_TIMEOUT = "WF_INSTANCE_TIMEOUT"              # 实例执行超时
WF_STATE_STORE_UNAVAILABLE = "WF_STATE_STORE_UNAVAILABLE"# 状态存储不可用

# 节点执行类（NODE_*）
NODE_AGENT_OUTPUT_INVALID = "NODE_AGENT_OUTPUT_INVALID"          # Agent 输出结构不合规
NODE_LLM_UNAVAILABLE = "NODE_LLM_UNAVAILABLE"                    # 主备模型均不可用
NODE_TOOL_NOT_FOUND = "NODE_TOOL_NOT_FOUND"                      # 工具不存在
NODE_TOOL_ARGUMENT_MISMATCH = "NODE_TOOL_ARGUMENT_MISMATCH"      # 工具入参不匹配
NODE_TOOL_TIMEOUT = "NODE_TOOL_TIMEOUT"                          # 工具调用超时
NODE_DATASOURCE_UNAVAILABLE = "NODE_DATASOURCE_UNAVAILABLE"      # 数据源不可用
NODE_TIMEOUT = "NODE_TIMEOUT"                                    # 节点执行超时
# 统一节点输入引用（unified_node_input DC-05 / INV-D04）：
NODE_INPUT_REFERENCE_MISSING = "NODE_INPUT_REFERENCE_MISSING"    # 节点输入引用目标缺失（上游未执行/被跳过/从未写入；消息须含命名输出名+节点 node_id+字段定位，spec §9.4.1）
NODE_INPUT_TYPE_MISMATCH = "NODE_INPUT_TYPE_MISMATCH"            # 节点输入装配期类型不匹配（路径中间值非 dict / 类别校验失败；消息须含期望类别与实际类别，spec §9.4.2）
# 向量知识库节点（vector_knowledge_base_node CHG-08，design §2.2.5 降级矩阵）：
NODE_VECTOR_RETRIEVAL_UNAVAILABLE = "NODE_VECTOR_RETRIEVAL_UNAVAILABLE"      # 向量检索后端不可用（消息含知识库标识与原因，不含地址/凭据；不得以空结果冒充成功）
NODE_VECTOR_EMBEDDING_UNAVAILABLE = "NODE_VECTOR_EMBEDDING_UNAVAILABLE"      # embedding 向量化服务不可用（消息含模型标识，不含服务地址/密钥）
NODE_VECTOR_LOCAL_RUNTIME_MISSING = "NODE_VECTOR_LOCAL_RUNTIME_MISSING"      # local 模式运行时依赖缺失且未开启确定性兜底降级（消息含模型标识与降级策略提示）

# 状态与检查点类（STATE_*）
STATE_CHECKPOINT_NOT_FOUND = "STATE_CHECKPOINT_NOT_FOUND"  # 目标检查点不存在
STATE_INSTANCE_RUNNING = "STATE_INSTANCE_RUNNING"          # 运行中实例禁止回滚/重放
STATE_CHECKPOINT_CORRUPTED = "STATE_CHECKPOINT_CORRUPTED"  # 检查点数据损坏

# 触发器类（TRIGGER_*）
TRIGGER_PAYLOAD_MAPPING_FAILED = "TRIGGER_PAYLOAD_MAPPING_FAILED"  # 事件载荷映射失败

# 实例类（INSTANCE_*）
INSTANCE_NOT_FOUND = "INSTANCE_NOT_FOUND"        # 实例不存在
INSTANCE_NOT_WAITING = "INSTANCE_NOT_WAITING"    # 实例不处于等待审核状态
INSTANCE_CANCEL_CONFLICT = "INSTANCE_CANCEL_CONFLICT"  # 取消请求被拒：实例已终态或无可取消任务（B2 嵌入 SDK 取消端点，HTTP 409）

# 范例库类（EXAMPLE_*）
EXAMPLE_DEPENDENCY_MISSING = "EXAMPLE_DEPENDENCY_MISSING"  # 范例依赖未配置
EXAMPLE_LOAD_FAILED = "EXAMPLE_LOAD_FAILED"                # 范例定义加载失败

# ---------------------------------------------------------------------------
# 第二阶段新增错误码
# ---------------------------------------------------------------------------

# 子工作流类
WF_SUBWORKFLOW_NOT_FOUND = "WF_SUBWORKFLOW_NOT_FOUND"            # 子工作流定义不存在
WF_SUBWORKFLOW_DEPTH_EXCEEDED = "WF_SUBWORKFLOW_DEPTH_EXCEEDED"  # 子工作流嵌套深度超限
WF_SUBWORKFLOW_INPUT_MAPPING_FAILED = "WF_SUBWORKFLOW_INPUT_MAPPING_FAILED"  # 子工作流输入映射失败
WF_COMPILATION_TIMEOUT = "WF_COMPILATION_TIMEOUT"              # 大图编译超时

# 数据源连接器类
DATASOURCE_CONNECTION_FAILED = "DATASOURCE_CONNECTION_FAILED"  # 数据源连接失败
DATASOURCE_READ_TIMEOUT = "DATASOURCE_READ_TIMEOUT"            # 数据源读取超时
DATASOURCE_QUERY_INVALID = "DATASOURCE_QUERY_INVALID"          # 查询语句无效
DATASOURCE_STREAM_CONSUME_FAILED = "DATASOURCE_STREAM_CONSUME_FAILED"  # 流消费失败
DATASOURCE_JOB_SUBMIT_FAILED = "DATASOURCE_JOB_SUBMIT_FAILED"  # 计算作业提交失败
DATASOURCE_PROBE_PARAM_INVALID = "DATASOURCE_PROBE_PARAM_INVALID"      # 连接探测参数错误
DATASOURCE_PROBE_UNAVAILABLE = "DATASOURCE_PROBE_UNAVAILABLE"          # 连接探测不可用（驱动/网络缺失）

# 数据转换类
TRANSFORM_EXPRESSION_INVALID = "TRANSFORM_EXPRESSION_INVALID"  # 转换表达式无效
TRANSFORM_JOIN_KEY_NOT_FOUND = "TRANSFORM_JOIN_KEY_NOT_FOUND"  # 连接键不存在
TRANSFORM_AGGREGATE_NOT_SUPPORTED = "TRANSFORM_AGGREGATE_NOT_SUPPORTED"  # 聚合函数不支持

# MCP 类
MCP_CONNECTION_FAILED = "MCP_CONNECTION_FAILED"                # MCP 连接失败
MCP_TOOL_NOT_FOUND = "MCP_TOOL_NOT_FOUND"                      # MCP 工具不存在
MCP_TOOL_TIMEOUT = "MCP_TOOL_TIMEOUT"                          # MCP 工具调用超时
MCP_TOOL_REGISTRATION_CONFLICT = "MCP_TOOL_REGISTRATION_CONFLICT"  # 工具注册冲突
MCP_TOOL_PERMISSION_DENIED = "MCP_TOOL_PERMISSION_DENIED"      # 工具权限不足
MCP_CONNECTION_POOL_EXHAUSTED = "MCP_CONNECTION_POOL_EXHAUSTED"  # 连接池耗尽

# 上下文引擎类
CONTEXT_CHECKPOINT_CORRUPTED = "CONTEXT_CHECKPOINT_CORRUPTED"  # 上下文检查点损坏

# ---------------------------------------------------------------------------
# 第四阶段：Agent 执行沙箱错误码（偏差修正 T130）
# ---------------------------------------------------------------------------

SANDBOX_POLICY_INVALID = "SANDBOX_POLICY_INVALID"            # 沙箱策略格式非法
SANDBOX_INTERNAL_ERROR = "SANDBOX_INTERNAL_ERROR"            # 沙箱内部错误
SANDBOX_TIMEOUT = "SANDBOX_TIMEOUT"                          # 沙箱执行超时
SANDBOX_LIMIT_EXCEEDED = "SANDBOX_LIMIT_EXCEEDED"            # 沙箱资源超限
SANDBOX_VIOLATION_FILE = "SANDBOX_VIOLATION_FILE"            # 路径穿越违规
SANDBOX_VIOLATION_NETWORK = "SANDBOX_VIOLATION_NETWORK"      # 网络访问违规
SANDBOX_DEGRADED = "SANDBOX_DEGRADED"                        # 沙箱降级
SANDBOX_RESOURCE_METER_DEGRADED = "SANDBOX_RESOURCE_METER_DEGRADED"  # 资源计量降级

# 第四阶段：插件/节点 SDK 错误码（偏差修正 T144）
NODE_MANIFEST_INVALID = "NODE_MANIFEST_INVALID"            # 节点 manifest 格式非法
NODE_PACKAGE_INVALID = "NODE_PACKAGE_INVALID"              # 节点包规范不符
NODE_DEPENDENCY_MISSING = "NODE_DEPENDENCY_MISSING"        # 节点依赖缺失
NODE_LIFECYCLE_ERROR = "NODE_LIFECYCLE_ERROR"              # 节点生命周期错误

# 第四阶段：贡献市场错误码（偏差修正 T153）
CONTRIBUTION_DUPLICATE = "CONTRIBUTION_DUPLICATE"          # 重复提交
CONTRIBUTION_INVALID_TRANSITION = "CONTRIBUTION_INVALID_TRANSITION"  # 非法状态流转
CONTRIBUTION_NOT_FOUND = "CONTRIBUTION_NOT_FOUND"          # 贡献不存在
SIGNATURE_INVALID = "SIGNATURE_INVALID"                    # 签名无效
SCAN_FAILED = "SCAN_FAILED"                                # 扫描失败
SCAN_NOT_PASSED = "SCAN_NOT_PASSED"                        # 扫描不通过

# 第四阶段：SSO 错误码（偏差修正 T162）
SSO_ASSERTION_INVALID = "SSO_ASSERTION_INVALID"            # 断言无效
SSO_REPLAY_DETECTED = "SSO_REPLAY_DETECTED"                # 重放检测
SSO_ATTR_MISSING = "SSO_ATTR_MISSING"                      # 属性缺失
IDP_UNREACHABLE = "IDP_UNREACHABLE"                        # IdP 不可达
SSO_DEGRADED = "SSO_DEGRADED"                              # SSO 降级

# 第四阶段：企业连接器错误码（偏差修正 T174）
CONNECTOR_AUTH_FAILED = "CONNECTOR_AUTH_FAILED"            # 连接器认证失败
CONNECTOR_TIMEOUT = "CONNECTOR_TIMEOUT"                    # 连接器超时
CONNECTOR_SCHEMA_MISMATCH = "CONNECTOR_SCHEMA_MISMATCH"    # 连接器模式不匹配
CONNECTOR_SCOPE_DENIED = "CONNECTOR_SCOPE_DENIED"          # 连接器数据范围拒绝
CONNECTOR_CREDENTIAL_INVALID = "CONNECTOR_CREDENTIAL_INVALID"  # 连接器凭证无效

# 第四阶段：评估框架错误码（偏差修正 T182）
EVAL_SCENARIO_INVALID = "EVAL_SCENARIO_INVALID"                # 评估场景无效
EVAL_BASELINE_MISSING = "EVAL_BASELINE_MISSING"                # 评估基线缺失
EVAL_TASK_INTERRUPTED = "EVAL_TASK_INTERRUPTED"                # 评估任务中断
EVAL_CASE_FAILED = "EVAL_CASE_FAILED"                          # 评估用例失败

# 第四阶段：语义发现错误码（偏差修正 T192）
INTENT_QUERY_INVALID = "INTENT_QUERY_INVALID"                  # 意图查询无效

# ---------------------------------------------------------------------------
# 第五阶段：计划书符合性偏差修正错误码（phase5_plan_alignment）
# ---------------------------------------------------------------------------

# G5 审计字段级内容哈希（T208）
AUDIT_CONTENT_HASH_MISMATCH = "AUDIT_CONTENT_HASH_MISMATCH"    # 审计条目字段级内容哈希不一致（篡改嫌疑）

# G1 监督者编排器（T216）
SUP_ROUTE_DECISION_FAILED = "SUP_ROUTE_DECISION_FAILED"        # 监督者路由决策失败（决策表缺失/匹配异常）
SUP_SUB_ORCHESTRATION_FAILED = "SUP_SUB_ORCHESTRATION_FAILED"  # 子编排整体失败
SUP_STATE_RESTORE_FAILED = "SUP_STATE_RESTORE_FAILED"          # 编排状态恢复失败（快照缺失/损坏）

# G6 触发器模板（T223）
TRIGGER_TEMPLATE_UNKNOWN = "TRIGGER_TEMPLATE_UNKNOWN"          # 触发器模板类型未知
TRIGGER_TEMPLATE_PARAM_INVALID = "TRIGGER_TEMPLATE_PARAM_INVALID"  # 触发器模板参数非法

# G3 Agent 绩效查询（T229）
PERF_TIME_RANGE_INVALID = "PERF_TIME_RANGE_INVALID"            # 绩效查询时间范围非法（起始晚于结束/格式错误）

# ---------------------------------------------------------------------------
# Agent 市场与 Tool 市场（agent_tool_marketplace T11.2 / design §2.2.4，仅追加）
# ---------------------------------------------------------------------------

MARKET_ENTRY_INVALID = "MARKET_ENTRY_INVALID"                      # 市场条目注册元数据不完整或非法（422）
MARKET_ENTRY_NOT_FOUND = "MARKET_ENTRY_NOT_FOUND"                  # 市场条目不存在（404）
MARKET_ENTRY_BUILTIN_IMMUTABLE = "MARKET_ENTRY_BUILTIN_IMMUTABLE"  # 内置模板派生条目不可覆盖（409）
MARKET_TEMPLATE_NOT_FOUND = "MARKET_TEMPLATE_NOT_FOUND"            # 内置模板不存在（404）
MARKET_TOOL_NOT_FOUND = "MARKET_TOOL_NOT_FOUND"                    # Tool 市场条目不存在（404）
MARKET_STORAGE_UNAVAILABLE = "MARKET_STORAGE_UNAVAILABLE"          # 市场存储不可用（500）

# --- Agent 市场用户自主开发（agent_market_user_development T18.1 / design §5.4.2，仅追加） ---
# 注：不新增 MARKET_DEV_VALIDATION_FAILED（校验不通过走 200 + valid=false 业务性结果，FR-9 规则 3）
MARKET_DEV_SUBMISSION_INVALID = "MARKET_DEV_SUBMISSION_INVALID"        # 用户开发提交关键字段缺失/非法（422）
MARKET_DEV_LIMIT_EXCEEDED = "MARKET_DEV_LIMIT_EXCEEDED"                # 用户开发提交包超限（422）
MARKET_DEV_SUBMISSION_DUPLICATE = "MARKET_DEV_SUBMISSION_DUPLICATE"    # 用户开发提交重复（同节点名+版本，409）
MARKET_DEV_INVALID_TRANSITION = "MARKET_DEV_INVALID_TRANSITION"        # 用户开发提交非法流转/终态后再流转（409）
MARKET_DEV_SUBMISSION_NOT_FOUND = "MARKET_DEV_SUBMISSION_NOT_FOUND"    # 用户开发提交不存在（404）
MARKET_DEV_SCAFFOLD_NOT_FOUND = "MARKET_DEV_SCAFFOLD_NOT_FOUND"        # 用户开发脚手架不存在（404）
MARKET_DEV_PUBLISH_FAILED = "MARKET_DEV_PUBLISH_FAILED"                # 用户开发提交上架失败（500）

# --- 本地向量数据库（local_vector_database）---
VECTOR_DB_INVALID_COLLECTION_ID = "VECTOR_DB_INVALID_COLLECTION_ID"            # 集合标识为空/非法/含路径穿越（确定性拒绝，禁越权写出根目录，INV-L10）
VECTOR_DB_INVALID_VECTOR = "VECTOR_DB_INVALID_VECTOR"                          # 空向量/含 NaN·Inf/非数值分量（确定性拒绝，不污染集合）
VECTOR_DB_DIMENSION_MISMATCH = "VECTOR_DB_DIMENSION_MISMATCH"                  # 写入或查询向量维度与集合声明维度不一致（AC-20，INV-L07）
VECTOR_DB_RECORD_LIMIT_EXCEEDED = "VECTOR_DB_RECORD_LIMIT_EXCEEDED"            # 单集合条目数超上限（FR-29/AC-38）
VECTOR_DB_STORAGE_UNAVAILABLE = "VECTOR_DB_STORAGE_UNAVAILABLE"                # 存储根不可写/磁盘满/存储打开失败（明确报错，不产生半初始化）
VECTOR_DB_FORMAT_VERSION_INCOMPATIBLE = "VECTOR_DB_FORMAT_VERSION_INCOMPATIBLE"  # 持久化格式版本不兼容且无迁移（明确拒绝，禁静默错读，AC-36）
VECTOR_DB_BACKEND_UNAVAILABLE = "VECTOR_DB_BACKEND_UNAVAILABLE"                # 声明启用增强后端但缺失且不允许降级（不伪装成功，INV-L15）
VECTOR_DB_IMPORT_FILE_UNREADABLE = "VECTOR_DB_IMPORT_FILE_UNREADABLE"          # 导入文件不存在/不可解析为逐行 JSON（整体失败，无部分写入，AC-13）

# --- 向量知识库管理（vector_kb_management）---
VECTOR_KB_MANAGEMENT_DISABLED = "VECTOR_KB_MANAGEMENT_DISABLED"                # 管理开关未启用（内部守卫；对外表现为路由未注册 404）
VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID = "VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID"    # 知识库标识非法/含路径穿越（确定性拒绝，INV-D11）
VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND = "VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND"      # 知识库不存在（404）
VECTOR_KB_KNOWLEDGE_BASE_ALREADY_EXISTS = "VECTOR_KB_KNOWLEDGE_BASE_ALREADY_EXISTS"  # 知识库标识已被占用（409）
VECTOR_KB_EMBEDDING_BINDING_REQUIRED = "VECTOR_KB_EMBEDDING_BINDING_REQUIRED"  # 未绑定唯一 Embedding 模型（单值必填，AC-40）
VECTOR_KB_DIMENSION_BINDING_CONFLICT = "VECTOR_KB_DIMENSION_BINDING_CONFLICT"  # 改绑模型维度与集合固化维度不符（前置拒绝，INV-D24）
VECTOR_KB_UNSUPPORTED_FORMAT = "VECTOR_KB_UNSUPPORTED_FORMAT"                  # 文件格式不支持（附支持清单；.doc 本期明确不支持）
VECTOR_KB_PARSER_UNAVAILABLE = "VECTOR_KB_PARSER_UNAVAILABLE"                  # 重格式解析器缺失（附可执行安装提示，不静默空结果，INV-D09）
VECTOR_KB_FILE_TOO_LARGE = "VECTOR_KB_FILE_TOO_LARGE"                          # 文件超大小上界（确定性拒绝，不产生半写入）
VECTOR_KB_EMPTY_CONTENT = "VECTOR_KB_EMPTY_CONTENT"                            # 解析后无有效内容（确定性结果，非静默空成功）
VECTOR_KB_INVALID_CHUNK_PARAMS = "VECTOR_KB_INVALID_CHUNK_PARAMS"              # 分片参数非法（附取值域，如 overlap ≥ max 拒绝）
VECTOR_KB_EMBEDDING_SOURCE_MISSING = "VECTOR_KB_EMBEDDING_SOURCE_MISSING"      # 无真实 embedding 来源（semantic 分块/确认导入前置拒绝）
VECTOR_KB_EMBEDDING_UNAVAILABLE = "VECTOR_KB_EMBEDDING_UNAVAILABLE"            # 向量化失败（来源不可达/运行时缺失）
VECTOR_KB_REAL_EMBEDDING_REQUIRED = "VECTOR_KB_REAL_EMBEDDING_REQUIRED"        # 真实 embedding 强制模式下仅 hash_fallback 可用的新写入（可辨识拒绝，不伪造语义，DC-B2-05 ③）
VECTOR_KB_STORAGE_UNAVAILABLE = "VECTOR_KB_STORAGE_UNAVAILABLE"                # 元数据落点不可用（500）
VECTOR_KB_DOCUMENT_NOT_FOUND = "VECTOR_KB_DOCUMENT_NOT_FOUND"                  # 文档不存在（404）
VECTOR_KB_CHUNK_NOT_FOUND = "VECTOR_KB_CHUNK_NOT_FOUND"                        # 分片不存在（404）
VECTOR_KB_UNKNOWN_INGESTION_JOB = "VECTOR_KB_UNKNOWN_INGESTION_JOB"            # 摄取作业不存在（404）
VECTOR_KB_INVALID_CHUNK_OPERATION = "VECTOR_KB_INVALID_CHUNK_OPERATION"        # 合并/拆分/编辑操作非法（空文本/超长/非相邻/非法拆分点）
VECTOR_KB_DRAFT_VERSION_CONFLICT = "VECTOR_KB_DRAFT_VERSION_CONFLICT"          # 草稿版本冲突（乐观并发，409）
VECTOR_KB_INVALID_PAGINATION = "VECTOR_KB_INVALID_PAGINATION"                  # 分页参数非法（page ≥ 1，1 ≤ page_size ≤ 200）
VECTOR_KB_CHUNK_LIMIT_EXCEEDED = "VECTOR_KB_CHUNK_LIMIT_EXCEEDED"              # 单知识库分片总量超上界（预检 + 写入兜底拒绝）
VECTOR_KB_WRITE_TEXT_TOO_LARGE = "VECTOR_KB_WRITE_TEXT_TOO_LARGE"              # 待写入文本长度超上界（写入模式前置拒绝，不半写；vkb_write_mode）
# ---- 文档标注能力（vkb_doc_annotation / CHG-11 / DC-11）----
VECTOR_KB_ANNOTATION_ORIGIN_REQUIRED = "VECTOR_KB_ANNOTATION_ORIGIN_REQUIRED"  # 后台导入来源信息必填缺失（受理期确定性拒绝，零草稿/零写入）
VECTOR_KB_ANNOTATION_NAME_CONFLICT = "VECTOR_KB_ANNOTATION_NAME_CONFLICT"      # 自定义标注名称命中保留键（顶层键/保留子键）
VECTOR_KB_ANNOTATION_INVALID = "VECTOR_KB_ANNOTATION_INVALID"                  # 标注取值非法（发布时间非 ISO-8601、自定义值非 JSON 兼容、未二次确认等）
VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED = "VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED"    # 标注项数/名称长度/值长度超上界
VECTOR_KB_ANNOTATION_DISABLED = "VECTOR_KB_ANNOTATION_DISABLED"                # 标注能力未启用（开关默认 False；标注路由未注册）

# --- 数据转换操作链（expand_data_transform_ops，CHG-B14 / design §2.7）---
DATA_TRANSFORM_UNKNOWN_OPERATION = "DATA_TRANSFORM_UNKNOWN_OPERATION"          # 操作链引用未注册操作（fail-fast，无静默降级，INV-08）
DATA_TRANSFORM_INVALID_STEP_PARAMS = "DATA_TRANSFORM_INVALID_STEP_PARAMS"      # 步骤参数缺失/取值非法（附操作名与期望说明）
DATA_TRANSFORM_STEP_FAILED = "DATA_TRANSFORM_STEP_FAILED"                      # 步骤执行失败（附步骤序号/操作名/原因；不吞原始异常语义）
DATA_TRANSFORM_CHAIN_TOO_LONG = "DATA_TRANSFORM_CHAIN_TOO_LONG"                # 操作链步数超上限（≤ 20，INV-15）
DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE = "DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE"  # 自定义脚本不可执行（仅登记与静态校验；不落共享状态、不改数据，OP-10/CHG-B17）
DATA_TRANSFORM_REGEX_UNSAFE_PATTERN = "DATA_TRANSFORM_REGEX_UNSAFE_PATTERN"    # 正则模式未通过安全校验（回溯灾难/超长；附模式定位，CHG-B12）
DATA_TRANSFORM_REGEX_INPUT_TOO_LONG = "DATA_TRANSFORM_REGEX_INPUT_TOO_LONG"    # 正则输入文本超长上限（INV-15）
DATA_TRANSFORM_ROW_LIMIT_EXCEEDED = "DATA_TRANSFORM_ROW_LIMIT_EXCEEDED"        # 展开生成行数超上限（附上限值与超出规模，CHG-B13/INV-15）

# --- 大模型节点与代码节点（llm_and_code_nodes，spec §6.5 / design §2.3.2(4)）---
LLM_NODE_DISABLED = "LLM_NODE_DISABLED"                                      # 大模型节点能力开关关闭（执行期，映射 NodeExecutionResult.error_code）
LLM_PARAM_INVALID = "LLM_PARAM_INVALID"                                      # 采样参数越界/类型非法（加载期校验提示消息前缀，D-04）
LLM_PARAM_UNSUPPORTED = "LLM_PARAM_UNSUPPORTED"                              # 上游模型服务不支持该采样参数（OP-07）
LLM_INFERENCE_FAILED = "LLM_INFERENCE_FAILED"                                # 推理失败（含主备均失败；消息脱敏）
LLM_OUTPUT_INVALID = "LLM_OUTPUT_INVALID"                                    # 结构化输出不合规
CODE_NODE_DISABLED = "CODE_NODE_DISABLED"                                     # 代码节点能力开关关闭（执行期）
CODE_SCRIPT_INVALID = "CODE_SCRIPT_INVALID"                                   # 脚本静态校验不合规（含语法错误；消息为命中项前缀）
CODE_SCRIPT_TOO_LARGE = "CODE_SCRIPT_TOO_LARGE"                              # 脚本超长（消息含上界与实际字节数）
CODE_SCRIPT_NOT_EXECUTABLE = "CODE_SCRIPT_NOT_EXECUTABLE"                    # 本期不执行脚本（与 DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE 并列不复用，OP-02）
# llm_code_nodes_v2 执行期错误码（CHG-01 / design §4.3；只增不删，INV-10）：
CODE_BANNED_CONSTRUCT = "CODE_BANNED_CONSTRUCT"            # 代码节点静态合规校验命中禁项（消息含命中项摘要，不含脚本全文）
CODE_SYNTAX_ERROR = "CODE_SYNTAX_ERROR"                    # 代码节点脚本语法错误（消息含行号）
CODE_RUNTIME_ERROR = "CODE_RUNTIME_ERROR"                  # 代码节点运行期异常/输出不可序列化/沙箱策略不存在（消息含异常类型名，截断脱敏 ≤512）
CODE_TIMEOUT = "CODE_TIMEOUT"                              # 代码节点执行超时（受限进程内放弃线程/子进程强杀；附违规记录与执行时长）
CODE_OUTPUT_MISSING = "CODE_OUTPUT_MISSING"                # 代码节点脚本未定义 result（显式置 None 与未定义不区分，消息统一提示）
CODE_OUTPUT_TOO_LARGE = "CODE_OUTPUT_TOO_LARGE"            # 代码节点输出超尺寸上限（消息含上界与实际字节数；无截断部分成功）

# --- 令牌与鉴权（arch_review_v2_tasks G2，design §4.2/§4.3 / DC-03/DC-04）---
# 治理层 deny 理由，经 GovernanceDecision.deny_reason 通道传递（不新开 HTTP 异常类，HC-01）
AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"                                    # JWT 结构/签名/alg 白名单/aud/iss/claims 校验失败（含篡改与缺 claim）
AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"                                    # JWT 已过期（now > exp + token_clock_skew_seconds 偏移容忍）
AUTH_TOKEN_FORMAT_UNSUPPORTED = "AUTH_TOKEN_FORMAT_UNSUPPORTED"              # 令牌形态与 token_verify_mode 不匹配（jwt 模式收明文令牌 / legacy 模式收 JWT 形态令牌）

# --- G6 实例级步数护栏（arch_review_v2_tasks G6，design §4.4/卡 A2 / DC-10）---
# 实例级轮次全局限额触发（workflow_max_steps）；与 loop 节点 max_iterations 的
# 节点级局部限额（WF_LOOP_NOT_CONVERGED）解耦、先触发先报告，互不合并
WF_BUDGET_EXCEEDED = "WF_BUDGET_EXCEEDED"                                    # 实例步数护栏阻断（消息含节点与轮次定位）

# --- G2 HITL 人工审批挂起（arch_review_v2_tasks_wave2 G2，design §4.4 / DC-26/DC-27）---
# 信号标识码：WorkflowSuspendedSignal 携带（挂起非错误，继承 Exception 不进
# DomainError 处理器，不映射 HTTP；仅作信号标识与日志定位）；决策前置校验错误码：
# review 决策提交时实例非 WAITING_REVIEW（映射 HTTP 409）
WF_INSTANCE_SUSPENDED = "WF_INSTANCE_SUSPENDED"                              # 挂起信号标识码（异常信号，不映射 HTTP，DC-26）
INSTANCE_NOT_WAITING_REVIEW = "INSTANCE_NOT_WAITING_REVIEW"                  # review 决策提交时实例非等待审核（HTTP 409，DC-27）

# --- G3 定时触发（arch_review_v2_tasks_wave2 G3，design §4.4 / DC-28）---
SCHEDULE_INVALID_CRON = "SCHEDULE_INVALID_CRON"                              # cron 解析失败（5 段式语法/字段越界；注册期 HTTP 422，扫描期仅告警跳过）
SCHEDULE_TRIGGER_NOT_FOUND = "SCHEDULE_TRIGGER_NOT_FOUND"                    # 管理 API 操作未注册定时触发器（HTTP 404）

# --- G4 重放幂等护栏（arch_review_v2_tasks_wave2 G4，design §4.4 / DC-30 规则 4）---
# 同（mode, checkpoint_id）REPLAY 留痕且重放实例活跃在途时拒绝重复发起；
# strict 无重放实例永不触发；重放完成后（终态）允许再次发起（序号递增）；
# rollback 路径不经此码（void_after 作废语义与在途拒绝互斥）
REPLAY_ALREADY_IN_PROGRESS = "REPLAY_ALREADY_IN_PROGRESS"                    # 同幂等键重放在途（HTTP 409，任务 5.3/CHG-60）

# --- B3 事件出口（roadmap_b3_event_export，design §5 API 契约）---
EVENT_SUBSCRIPTION_NOT_FOUND = "EVENT_SUBSCRIPTION_NOT_FOUND"                # 管理 API 操作未注册订阅（HTTP 404）
EVENT_SUBSCRIPTION_INVALID = "EVENT_SUBSCRIPTION_INVALID"                    # 订阅注册校验失败（url/secret/event_types，HTTP 422）
EVENT_DEAD_LETTER_NOT_FOUND = "EVENT_DEAD_LETTER_NOT_FOUND"                  # 死信条目不存在（HTTP 404）

# --- B4 计量配额闭环（roadmap_b4_metering_quota，design §2 DC-B4-01）---
TENANT_QUOTA_EXCEEDED = "TENANT_QUOTA_EXCEEDED"                              # 租户并发实例配额超限（HTTP 429，POST /instances 闸门拒绝，实例未注册未执行）

# --- B6 并行扇出（roadmap_b6_parallel_fanout，design §4.3 失败语义）---
# join 等待不可满足：wait_any / wait_n_of_m 下「已成功 + 剩余可能」仍低于
# 等待数（全部失败或成功数不足）；分支节点失败沿用既有失败码
# （WF_NODE_EXECUTION_FAILED / WF_PARALLEL_BRANCH_FAILED），不另新增。
WF_PARALLEL_JOIN_NOT_SATISFIED = "WF_PARALLEL_JOIN_NOT_SATISFIED"            # 并行汇聚等待不可满足（消息含 join/等待数/成功数定位）


# ---------------------------------------------------------------------------
# 领域异常类
# ---------------------------------------------------------------------------


class DomainError(Exception):
    """领域异常基类。

    属性：
        code    —— 语义化错误码（见上）
        message —— 人类可读说明
        details —— 结构化附加信息（如 node_id、edge_id、行列号、缺失字段清单）
    """

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Dict[str, Any] = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 API 统一错误结构 {code, message, details}。"""
        return {"code": self.code, "message": self.message, "details": self.details}


class WorkflowValidationError(DomainError):
    """工作流定义校验类异常：映射 HTTP 422。"""


class InstanceStateConflictError(DomainError):
    """实例状态冲突类异常：映射 HTTP 409。"""


class ResourceNotFoundError(DomainError):
    """资源未找到类异常：映射 HTTP 404。"""


class NodeExecutionError(DomainError):
    """节点执行失败类异常。"""


class TriggerError(DomainError):
    """触发器相关异常。"""


class ExampleLibraryError(DomainError):
    """范例库相关异常。"""


class ExampleDependencyMissingError(ExampleLibraryError):
    """范例依赖缺失异常：所需环境变量未配置。

    映射 HTTP 422，details 中携带缺失变量清单。
    """

    def __init__(self, example_id: str, missing_variables: list) -> None:
        super().__init__(
            code=EXAMPLE_DEPENDENCY_MISSING,
            message=f"范例 {example_id} 缺少必要环境变量配置",
            details={
                "example_id": example_id,
                "missing_variables": missing_variables,
            },
        )


class TenantQuotaExceededError(DomainError):
    """租户并发实例配额超限：映射 HTTP 429（roadmap B4，DC-B4-01）。

    POST /instances 闸门拒绝时抛出；details 携带租户标识、当前并发活跃
    实例数与配额上限。拒绝路径零副作用：实例不注册、不编译、不执行。
    """

    def __init__(self, tenant_id: str, active_instances: int, max_instances: int) -> None:
        super().__init__(
            code=TENANT_QUOTA_EXCEEDED,
            message=f"租户并发实例配额超限: {active_instances}/{max_instances}",
            details={
                "tenant_id": tenant_id,
                "active_instances": active_instances,
                "max_instances": max_instances,
            },
        )
