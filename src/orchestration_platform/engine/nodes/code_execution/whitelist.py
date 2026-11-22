# =============================================================================
# 受限执行内建白名单常量（engine/nodes/code_execution/whitelist.py）
#
# 职责：A1/A2 受限执行通道的内建白名单唯一权威（llm_code_nodes_v2 任务 4.1 /
#      DC-01-2 / design 附录 B.1 —— 恰 44 项）。
# 设计意图：
#   1. 封闭清单：受限命名空间 __builtins__ 仅含本清单，未列入名字一律
#      NameError（B.2 剥离项 open/input/print/exec/eval/compile/__import__/
#      breakpoint/globals/locals/vars/dir/getattr/setattr/delattr/memoryview
#      等缺省不可达）——本清单是白名单而非黑名单，缺省即拒绝；
#   2. A1/A2 同源：A2 bootstrap 清单由本模块常量生成（单一定义、双端引用，
#      design B.3 / DC-02，parity 测试锁定）；
#   3. params/result 两个契约变量名不在白名单内（由执行命名空间单独注入，
#      查找优先级高于内建，design B.3）；
#   4. 清单冻结：不允许运行期动态扩展；变更须同步 design 附录 B.1 与快照
#      单测（任务 4.1 验收：多一项少一项断言均失败）；
#   5. import 期校验：任一名字在内建缺位 → AttributeError 快速失败
#      （防运行期静默漏配）。
# =============================================================================

import builtins
from typing import Dict, Tuple

# --- design 附录 B.1 保留项（44 项，分类冻结）---

# 数值与逻辑（12 项）
NUMERIC_LOGIC_NAMES: Tuple[str, ...] = (
    "abs", "bool", "divmod", "float", "int", "pow", "round",
    "hash", "hex", "oct", "bin", "ord",
)

# 迭代与组合（10 项）
ITERATION_COMPOSITION_NAMES: Tuple[str, ...] = (
    "enumerate", "filter", "iter", "len", "map", "next",
    "range", "reversed", "sorted", "zip",
)

# 序列与集合构造（7 项）
SEQUENCE_SET_NAMES: Tuple[str, ...] = (
    "dict", "frozenset", "list", "set", "slice", "tuple", "chr",
)

# 判定与表示（7 项）
PREDICATE_REPR_NAMES: Tuple[str, ...] = (
    "all", "any", "callable", "isinstance", "issubclass", "repr", "str",
)

# 异常类型（8 项）
EXCEPTION_TYPE_NAMES: Tuple[str, ...] = (
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "ZeroDivisionError", "ArithmeticError", "StopIteration",
)

# 合并清单（44 项；快照测试锁定与 design 附录 B.1 恰等）
WHITELISTED_BUILTIN_NAMES: Tuple[str, ...] = (
    NUMERIC_LOGIC_NAMES
    + ITERATION_COMPOSITION_NAMES
    + SEQUENCE_SET_NAMES
    + PREDICATE_REPR_NAMES
    + EXCEPTION_TYPE_NAMES
)

# 受限执行 __builtins__ 唯一来源（执行期每次取新拷贝，防脚本篡改后跨执行残留）
WHITELISTED_BUILTINS: Dict[str, object] = {
    name: getattr(builtins, name) for name in WHITELISTED_BUILTIN_NAMES
}
