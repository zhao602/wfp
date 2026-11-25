// =============================================================================
// 节点参数配置提示文案模块（src/canvas/nodeConfigHints.ts）
//
// 职责：节点参数配置窗口提示文案的唯一出处——纯数据 + 纯函数。
// 设计意图（对应需求 add_node_config_hints 的 FR-4 / FR-5 / FR-7 / FR-11）：
//   1. 提示文案与表单组件解耦，集中维护、便于评审；
//   2. 键与配置字段名逐字符对齐（节点类型 × 字段名），机械可核对；
//   3. 查表缺失时安全降级（返回 undefined），不影响既有渲染。
// 约束：零 React、零副作用、仅类型依赖 definitionTypes 的 NodeType。
// =============================================================================

import type { NodeType } from "../workflow-definition/definitionTypes";

/** 字段级提示条目：文案为纯静态数据，不承载业务逻辑 */
export interface FieldHint {
  /** 辅助说明（"如何配置" / "需要什么信息"）；可选字段在文案内以"（可选）"标注 */
  readonly helper: string;
  /** 占位符（示例值或格式示意）；仅文本类字段提供 */
  readonly placeholder?: string;
}

/** 单节点类型的提示集合：节点级说明 + 该类型字段级提示 */
export interface NodeHintSet {
  /** 节点级整体说明（一段） */
  readonly nodeHint: string;
  /** 字段级提示，键为配置字段名（与后端配置模型字段名对齐） */
  readonly fields: Readonly<Record<string, FieldHint>>;
}

/** 节点类型 → 提示集合（全部节点类型全覆盖） */
export type NodeHintMap = Readonly<Record<NodeType, NodeHintSet>>;

/** 面板公共字段（节点名称）提示：独立成表，不混入节点类型键 */
export const PANEL_COMMON_HINTS: Readonly<Record<string, FieldHint>> = {
  name: {
    helper: "节点名称，用于在画布上标识该节点。",
  },
  output_name: {
    helper: "（可选）命名输出标识，声明后可供下游节点在映射字段中引用。仅允许小写字母、数字、下划线，以字母开头。",
    placeholder: "如：dataset",
  },
};

/** 全部节点类型的节点级说明与字段级提示 */
export const NODE_HINTS: NodeHintMap = {
  trigger: {
    nodeHint: "工作流的起始节点，定义流程以何种方式被触发。",
    fields: {
      trigger_type: {
        helper: "选择该流程的启动方式：手动触发、按计划定时执行或由数据事件驱动。",
      },
      schedule: {
        helper: "选择预设频率并设置参数，系统自动生成定时表达式。仅在触发方式为定时时生效。",
        placeholder: "请选择频率",
      },
      input_template: {
        helper:
          "定义触发时的输入参数映射：键为参数名，值为触发上下文字段引用。手动与数据事件触发下生效（触发上下文初值为实例启动入参）；定时触发暂无运行时入口、模板不生效。任一引用字段缺失将中止启动并报错。",
      },
    },
  },
  agent: {
    nodeHint: "由大模型驱动的智能节点，依据提示词完成推理、生成或决策任务。",
    fields: {
      model_identifier: {
        helper: "从平台已配置的模型中选择该节点使用的模型，或选择「跟随全局主模型」（留空 = 运行时自动使用 .env 的 LLM_PRIMARY_MODEL，更换模型无需逐节点修改）。候选来自运行环境的模型配置；未检测到已配置模型时可手工填写，手工填写的模型可能不可用。",
        placeholder: "例如：通用对话模型",
      },
      system_prompt: {
        helper: "描述该智能体的角色、目标与行为约束，作为其始终遵循的系统指令。",
        placeholder: "例如：你是一名严谨的数据分析助手",
      },
      available_tools: {
        helper: "声明该智能体可调用的工具标识列表，约束其执行期间可使用的工具范围。",
      },
      inputs: {
        helper:
          "（可选）显式声明输入引用选择集：未配置时注入全部上游命名输出（隐式模式）；配置后仅注入所列引用，别名用于组织用户消息上下文（同一节点内别名与缺省名不可重名）。",
      },
      output_schema: {
        helper: "（可选）声明输出的结构约束：键为字段名，值为类型名（string/number/boolean/object/array）。",
      },
    },
  },
  tool: {
    nodeHint: "调用外部工具或本地能力，完成一项具体操作的节点。",
    fields: {
      tool_identifier: {
        helper: "填写要调用的工具名称，需与已注册的工具标识保持一致。",
        placeholder: "例如：天气查询",
      },
      tool_source: {
        helper: "选择工具的来源：本地内置工具或通过 MCP 接入的外部工具。",
      },
      argument_mapping: {
        helper: "定义工具入参到共享状态字段的映射：键为工具参数名，值为上游节点命名输出引用。",
      },
      static_arguments: {
        helper: "以常量值直接传入工具的参数集合，与参数映射互斥（同一参数名不可同时出现在两者中）。",
      },
    },
  },
  data_source: {
    nodeHint: "从外部数据源读取数据，为后续处理提供输入的节点。",
    fields: {
      connection_reference: {
        helper: "填写已配置的数据连接名称，用于定位要访问的数据源。",
        placeholder: "例如：生产数据仓库",
      },
      query: {
        helper: "填写只读查询语句，用于从数据源中读取所需数据。",
        placeholder: "例如：查询近七天的订单",
      },
      source_type: {
        helper: "选择数据源类型以确定连接参数字段。",
        placeholder: "请选择数据源类型",
      },
      file_path: {
        helper: "数据文件的本地路径或远程 URL。",
        placeholder: "例如：/data/orders.csv",
      },
      encoding: {
        helper: "文件编码格式，默认 utf-8。",
        placeholder: "utf-8",
      },
      url: {
        helper: "REST API 端点 URL。",
        placeholder: "https://api.example.com/v1/data",
      },
      method: {
        helper: "HTTP 请求方法，默认 GET。",
        placeholder: "GET",
      },
      headers: {
        helper: "请求头，JSON 格式。",
        placeholder: '{"Content-Type": "application/json"}',
      },
      connection_string: {
        helper: "数据库连接串。",
        placeholder: "postgresql://user:pass@host:5432/db",
      },
      hologres_endpoint: {
        helper: "Hologres 端点地址。",
        placeholder: "xxx.hologres.aliyuncs.com",
      },
      hologres_port: {
        helper: "Hologres 端口，默认 80。",
        placeholder: "80",
      },
      hologres_database: {
        helper: "Hologres 数据库名。",
        placeholder: "my_database",
      },
      hologres_access_key: {
        helper: "Hologres AccessKey（凭据，掩码显示）。",
        placeholder: "请输入 AccessKey",
      },
      kafka_brokers: {
        helper: "Kafka broker 列表，逗号分隔。",
        placeholder: "broker1:9092,broker2:9092",
      },
      kafka_topic: {
        helper: "Kafka topic 名称。",
        placeholder: "my-topic",
      },
      kafka_group_id: {
        helper: "Kafka 消费组 ID。",
        placeholder: "my-group",
      },
      kafka_offset: {
        helper: "消费起始位置：earliest 或 latest。",
        placeholder: "earliest",
      },
      flink_jobmanager_url: {
        helper: "Flink JobManager REST URL。",
        placeholder: "http://localhost:8081",
      },
      flink_jar_id: {
        helper: "Flink JAR ID。",
        placeholder: "my-jar-id",
      },
      flink_program_args: {
        helper: "Flink 作业参数。",
        placeholder: "--input kafka:topic --output print",
      },
      spark_master_url: {
        helper: "Spark Master URL。",
        placeholder: "spark://localhost:7077",
      },
      spark_submit_args: {
        helper: "spark-submit 额外参数。",
        placeholder: "--executor-memory 2g",
      },
      spark_app_name: {
        helper: "Spark 应用名。",
        placeholder: "my-spark-app",
      },
      target_endpoint: {
        helper: "企业系统目标端点 URL。",
        placeholder: "https://crm.example.com/api",
      },
    },
  },
  control_flow: {
    nodeHint: "控制流程走向的节点，可实现条件分支、循环、并行汇聚与人工审核。",
    fields: {
      control_flow_kind: {
        helper: "选择控制流的类型：条件分支、循环、并行汇聚或人工审核。",
      },
      loop_body_entry: {
        helper: "（可选）填写循环体的入口节点，循环将从该节点开始执行。",
        placeholder: "例如：处理单条记录",
      },
      loop_termination_condition: {
        helper: "（可选）填写循环结束条件，满足该条件时退出循环。",
        placeholder: "例如：已处理全部记录",
      },
      max_iterations: {
        helper: "（可选）限制循环的最大执行次数，避免陷入无限循环。",
      },
      conditions: {
        helper: "条件分支的路由规则列表，每条含目标节点与条件表达式，表达式中的变量名映射到共享状态命名输出。",
      },
      default_branch_target: {
        helper: "（可选）默认分支目标节点，所有条件均不匹配时执行该节点。",
      },
      parallel_failure_strategy: {
        helper: "并行分支的失败策略：快速失败（任一分支失败即终止）或等待全部（所有分支完成后才汇总）。",
      },
      review_instructions: {
        helper: "（可选）人工审核的说明文本，告知审核人员需要审查的内容与标准。最多 2000 字符。",
      },
      review_timeout_action: {
        helper: "审核超时时的处理动作：拒绝（按失败处理）或等待（持续等待审核结果）。",
      },
    },
  },
  subworkflow: {
    nodeHint: "在当前流程中嵌套调用另一个已保存的工作流。",
    fields: {
      subworkflow_definition_id: {
        helper: "填写要调用的子工作流标识，需为已存在的工作流。",
        placeholder: "例如：数据清洗流程",
      },
      context_strategy: {
        helper: "选择上下文的传递方式：继承上游上下文，或隔离后独立执行。",
      },
      max_nesting_depth: {
        helper: "限制子工作流的最大嵌套层数，防止调用层级过深。",
      },
      input_mapping: {
        helper: "定义子工作流输入参数到父工作流共享状态的映射：键为子工作流参数名，值为父状态字段引用。",
      },
      output_mapping: {
        helper: "定义子工作流输出到父工作流状态的回写映射：键为父状态字段名，值为子工作流输出参数名。",
      },
    },
  },
  dynamic_injector: {
    nodeHint: "在运行期按数据动态生成并注入节点，用于处理数量不定的任务。",
    fields: {
      injection_condition: {
        helper: "填写注入条件表达式，用于确定何时触发节点注入。",
        placeholder: "例如：上游输出列表非空",
      },
      node_templates: {
        helper: "模板节点列表，新节点将依据模板生成。",
        placeholder: "例如：处理单条记录",
      },
      max_injection_count: {
        helper: "限制单次最多注入的节点数量，用于控制资源占用。",
      },
      target_position: {
        helper: "指定注入节点的目标插入位置（可选）。",
        placeholder: "例如：after_node_x",
      },
    },
  },
  data_transform: {
    nodeHint: "对上游数据进行映射、过滤、聚合或连接等转换处理。",
    fields: {
      operation: {
        helper: "选择数据转换的操作类型：映射、过滤、聚合或连接。",
      },
      input_dataset_reference: {
        helper: "填写输入数据集的引用名称（通常为上游节点的命名输出）。",
        placeholder: "例如：upstream_output",
      },
      params: {
        helper: "操作参数配置，按操作类型展示不同字段。",
      },
      // ---- expand_data_transform_ops 新增（键与配置字段名逐字符对齐，CHG-F08）----
      operations: {
        helper: "数据转换操作链：步骤按顺序依次执行，至少保留一步；旧单操作配置按链长 1 呈现。",
      },
      output_format: {
        helper: "选择节点输出形态：数据集（默认，行为不变）/单对象/文本/键值对。",
      },
      map_expression: {
        helper: "映射表达式：逐行求值，失败置空 + 计数，不中断链；空值以预制表达式补全。",
      },
      filter_condition: {
        helper: "过滤条件（如 value >= 20 或 category == 'a'，字段名直引用本行字段）：真保留、假剔除；字段缺失/类型不可比较的行按不保留处理并计数，不中断链。",
      },
      group_by: {
        helper: "聚合分组字段列表（逗号分隔）。",
      },
      aggregate_function: {
        helper: "聚合函数：sum/count/avg/max/min。",
      },
      aggregate_field: {
        helper: "聚合计算目标字段名。",
      },
      join_type: {
        helper: "连接类型：inner/left/right/full。",
      },
      join_key: {
        helper: "单键连接的连接键字段名（与多键列表二选一）。",
      },
      right_dataset_reference: {
        helper: "右侧数据集引用名称。",
      },
      join_keys: {
        helper: "多键连接的左侧连接键列表（逗号分隔）。",
      },
      right_join_keys: {
        helper: "右侧连接键列表（逗号分隔）；留空表示与左侧同名。",
      },
      sort_keys: {
        helper: "排序键列表（逗号分隔，先到优先）。",
      },
      sort_orders: {
        helper: "排序方向列表，与排序键对齐：asc/desc（缺省全升序）。",
      },
      distinct_keys: {
        helper: "去重键列表（逗号分隔）；留空表示全列去重。",
      },
      keep: {
        helper: "去重保留策略：first=保留首条（缺省）。",
      },
      rename_map: {
        helper: "字段重命名映射（旧字段名 → 新字段名）；目标列缺失忽略 + 告警。",
      },
      select_columns: {
        helper: "保留列清单（逗号分隔）；缺失列忽略 + 告警。",
      },
      drop_columns: {
        helper: "删除列清单（逗号分隔）；缺失列忽略 + 告警。",
      },
      cast_field: {
        helper: "类型强转目标字段名。",
      },
      cast_target: {
        helper: "目标类型：number/date。",
      },
      date_format: {
        helper: "date 目标的解析格式（strptime 显式格式，date 目标必填），如 %Y-%m-%d。",
      },
      window_group_by: {
        helper: "窗口分组字段列表（逗号分隔）；留空表示全局。",
      },
      window_order_by: {
        helper: "窗口排序键列表（逗号分隔）。",
      },
      window_orders: {
        helper: "窗口排序方向，与排序键对齐：asc/desc。",
      },
      window_output_field: {
        helper: "行号写入字段名（缺省 row_number）。",
      },
      flatten_field: {
        helper: "打平目标字段名；留空表示整行打平。",
      },
      max_depth: {
        helper: "打平最大深度（缺省 3）。",
      },
      explode_field: {
        helper: "数组展开目标字段名（值须为 list）。",
      },
      max_rows: {
        helper: "展开生成行数上限（缺省 100000）。",
      },
      null_fields: {
        helper: "空值处理字段列表（逗号分隔）；留空表示全部字段。",
      },
      null_value_scope: {
        helper: "空值判定口径（可多选）：none/empty/missing。",
      },
      strategy: {
        helper: "空值策略：fill_constant/fill_mean/fill_median/drop_row。",
      },
      fill_value: {
        helper: "fill_constant 策略的固定填充值。",
      },
      drop_scope: {
        helper: "drop_row 删行触发范围：field=按指定列空值删行 / row=整行任意字段空值删行（缺省 field）。",
      },
      regex_field: {
        helper: "正则操作目标字段名。",
      },
      pattern: {
        helper: "正则模式（长度 ≤ 512，须通过安全校验）。",
      },
      extract_mode: {
        helper: "提取模式：first=首个匹配 / all=全部匹配（缺省 first）。",
      },
      group: {
        helper: "捕获组序号（缺省 0=整段匹配）。",
      },
      target_field: {
        helper: "结果写入字段名（缺省覆盖目标字段）。",
      },
      replacement: {
        helper: "替换串（支持 \\1 反向引用）。",
      },
      validate_field: {
        helper: "校验目标字段名。",
      },
      validate_rule: {
        helper: "校验规则：email/json。",
      },
      on_invalid: {
        helper: "非法值处置：drop=删行 / mark=标记（缺省 drop）。",
      },
      mask_fields: {
        helper: "脱敏字段清单（逗号分隔）。",
      },
      mask_rules: {
        helper: "内置脱敏规则（可多选）：phone/id_card/bank_card/email/name/address。",
      },
      custom_patterns: {
        helper: "自定义脱敏模板（正则模式 → 掩码模板）；不可逆掩码，非加密级保护。",
      },
      source_field: {
        helper: "JSON Path 来源字段名。",
      },
      path: {
        helper: "JSON Path 表达式（受限语法），如 $.data.items[0].name。",
      },
      sample_mode: {
        helper: "采样模式：head=前 N 行 / random=随机（缺省 head）。",
      },
      sample_n: {
        helper: "采样条数（与比例二选一，条数优先）。",
      },
      sample_percent: {
        helper: "采样比例（0 < 比例 ≤ 100；与条数二选一，条数优先）。",
      },
      random_seed: {
        helper: "random 模式随机种子（可复现）。",
      },
      language: {
        helper: "脚本语言（仅 python，仅登记）。",
      },
      code: {
        helper: "自定义脚本内容（仅登记与静态校验，运行期不执行）。",
      },
      record_index: {
        helper: "single_object 目标记录索引（缺省 0=首条）。",
      },
      text_field: {
        helper: "text 拼接字段名；留空表示全文 JSON 序列化。",
      },
      text_separator: {
        helper: "text 行间分隔符（缺省换行）。",
      },
      kv_key_field: {
        helper: "kv 键字段名；留空取记录首列。",
      },
      kv_value_field: {
        helper: "kv 值字段名；留空取记录第二列。",
      },
    },
  },
  output_display: {
    nodeHint: "将上游节点的命名输出展示到执行结果界面，纯透出无业务动作。",
    fields: {
      input_reference: {
        helper: "勾选要输出的上游命名输出；未勾选时展示上游全部命名输出。",
      },
      output_mode: {
        helper: "选择结果输出方式：屏幕显示 / 输出为文件（可兼选，至少保留一种，缺省屏幕显示）。",
      },
      display_title: {
        helper: "（可选）展示标题，≤100 字符；留空时以节点名称为标题。",
        placeholder: "例如：查询结果",
      },
    },
  },
  vector_knowledge_base: {
    nodeHint: "从向量知识库检索与查询语义相关的文本片段，输出片段集合供下游节点引用。",
    fields: {
      knowledge_base_id: {
        helper: "填写已配置的向量知识库标识，执行时从该知识库检索片段。",
        placeholder: "例如：product_docs",
      },
      mode: {
        helper: "选择节点模式：检索（从知识库召回片段供下游引用）或写入（把手写/上游文本写入知识库）。",
      },
      query_mode: {
        helper: "选择查询条件的录入方式：手动填写固定查询文本，或继承上游命名输出作为查询。",
      },
      query_value: {
        helper: "手动录入模式下填写查询文本，≤2000 字符；仅在该模式下生效。",
        placeholder: "例如：量子计算的基本原理",
      },
      query_reference: {
        helper: "继承录入模式下选择上游可引用的命名输出作为查询来源；仅在该模式下生效。",
        placeholder: "例如：upstream_output",
      },
      top_k: {
        helper: "限制单次召回的片段数量上限，取值 1~100，默认 5。",
      },
      embedding_model: {
        helper: "向量化模型由知识库绑定决定（面板只读展示当前绑定，运行时以绑定优先）；可在后台「向量知识库管理」中调整。",
      },
      rerank_model: {
        helper: "（可选）重排模型：配置后对召回片段二次排序提升相关性；未配置则跳过重排。",
      },
      write_source_mode: {
        helper: "写入模式待写入文本的录入方式：手动填写，或继承上游命名输出。",
      },
      write_text: {
        helper: "手动录入模式下填写待写入文本；仅在该模式下生效（长度上界由运行期配置约束）。",
        placeholder: "例如：待写入知识库的文本内容",
      },
      write_reference: {
        helper: "继承录入模式下选择上游可引用的命名输出作为待写入文本来源；仅在该模式下生效。",
        placeholder: "例如：upstream_output",
      },
      write_chunk_strategy: {
        helper: "写入模式分片策略（可选）：缺省取知识库默认策略。",
      },
      write_chunk_params: {
        helper: "写入模式分片参数（可选）：分片长度/重叠/相似度阈值/hierarchy，缺省取服务默认。",
      },
      write_document_id: {
        helper: "写入幂等键覆盖（可选）：缺省按「知识库:节点」生成稳定键（同键覆盖不膨胀）；显式填写则按该键追加。",
      },
      annotation_origin: {
        helper: "写入模式切片标注-来源信息（可选）：缺省「工作流写入」，可显式覆盖。",
      },
      annotation_published_at: {
        helper: "写入模式切片标注-文档发布时间（可选）：ISO-8601，或留空表示「未知」。",
      },
      annotation_custom: {
        helper: "写入模式切片标注-用户自定义标注（可选）：名称 + 值，JSON 兼容、零凭据。",
      },
      annotation_enabled: {
        helper: "写入模式标注能力开关（可选）：缺省跟随全局开关。",
      },
    },
  },
  // ---- llm_code_nodes_v2 新增（任务 7.2 / CHG-F02）----
  llm: {
    nodeHint:
      "大模型推理节点：单轮「装配 → 请求 → 透出」薄链路，将上游输入与提示词送入模型并原样透出文本结果。",
    fields: {
      model_identifier: {
        helper: "从平台已配置的模型中选择该节点使用的模型，或选择「跟随全局主模型」（留空 = 运行时自动使用 .env 的 LLM_PRIMARY_MODEL）。候选来自运行环境的模型配置。",
        placeholder: "例如：通用对话模型",
      },
      system_prompt: {
        helper: "系统提示词：描述模型角色与行为约束，作为其始终遵循的系统指令（默认空串）。",
        placeholder: "例如：你是一名严谨的翻译助手",
      },
      user_prompt: {
        helper: "用户提示词：与装配产物拼接后作为用户消息发送（纯文本，不做模板引擎解释）。",
      },
      inputs: {
        helper:
          "（可选）显式声明输入引用选择集：未配置时注入全部上游命名输出（隐式模式）；配置后仅注入所列引用，语义与 agent 节点一致。",
      },
      output_schema: {
        helper:
          "（可选）输出结构约束：当前版本不生效（声明保留）——执行层不消费、不校验、不重生成。",
      },
      temperature: {
        helper: "（可选）温度，取值 0.0~2.0；未配置时按运行默认（0.7）发送。",
      },
      top_p: {
        helper: "（可选）Top P，取值 (0.0,1.0]；仅在显式配置时发送。",
      },
      top_k: {
        helper: "（可选）Top K，取值 0 或 1~100；显式配置且大于 0 时发送。",
      },
      max_tokens: {
        helper: "（可选）最大生成长度，取值 1~32768；未配置时按运行默认（4096）发送。注意：推理模型（如 DeepSeek-R 系列）把思考计入生成额度，max_tokens 过小会导致思考耗尽额度、正文输出为空（运行将报可辨识错误）。",
      },
      stop: {
        helper: "（可选）停止序列，至多 4 条且每条 1~128 字符，去重保序；未配置时不发送。",
        placeholder: "多条以逗号分隔",
      },
      seed: {
        helper: "（可选）随机种子，取值 0~2147483647；仅在显式配置时发送。",
      },
    },
  },
  code: {
    nodeHint:
      "代码执行节点：脚本受限进程内执行，非进程级隔离——执行前经静态校验与内建白名单约束，具备超时与输出护栏；CPU 死循环线程不可强杀。",
    fields: {
      language: {
        helper: "脚本语言：本期仅支持 python。",
      },
      code: {
        helper:
          "Python 脚本文本（必备非空）：装配产物以 params 注入（只读语义字典），脚本在顶层定义 result 作为产出（未定义将报错）。",
      },
      entry_function: {
        helper: "入口函数名：展示语义，不参与执行。",
      },
      dependencies: {
        helper: "（可选）依赖名称清单：仅名称不含版本号，去重保序；仅登记展示，不安装。",
        placeholder: "例如：numpy, pandas",
      },
      inputs: {
        helper:
          "（可选）输入引用选择集：未配置时注入全部上游命名输出（隐式模式）；配置后装配产物以 params 注入代码域（键 = 别名优先，缺省为被引用的命名输出名）。",
      },
      output_schema: {
        helper: "（可选）输出结构声明：声明语义为主，执行层不据此校验 result 结构。",
      },
      sandbox_policy_id: {
        helper:
          "（可选）沙箱策略标识：超时与输出上限取策略与系统默认的较小值；未引用时按系统默认护栏。",
      },
    },
  },
};

/**
 * 查字段级提示。
 * @param nodeType 节点类型；面板公共字段传 "common"
 * @param field 字段标识（与配置字段名对齐）
 * @returns 命中返回 FieldHint；缺项返回 undefined（触发安全降级）
 */
export function resolveFieldHint(
  nodeType: NodeType | "common",
  field: string,
): FieldHint | undefined {
  if (nodeType === "common") {
    return PANEL_COMMON_HINTS[field];
  }
  return NODE_HINTS[nodeType].fields[field];
}

/** 查节点级说明；缺项返回 undefined（不渲染说明区） */
export function getNodeHint(nodeType: NodeType): string | undefined {
  return NODE_HINTS[nodeType].nodeHint;
}

/**
 * 生成字段辅助说明元素的稳定唯一 id。
 * 规则：wfp-ncp-hint-{nodeType}-{field}[-{index}]
 * @param index 多实例字段下标（如数据转换的操作项）
 */
export function buildHintId(
  nodeType: NodeType | "common",
  field: string,
  index?: number,
): string {
  const base = `wfp-ncp-hint-${nodeType}-${field}`;
  return index === undefined ? base : `${base}-${index}`;
}