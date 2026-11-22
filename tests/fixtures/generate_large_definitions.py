# =============================================================================
# 大体积 fixture 生成脚本（tests/fixtures/generate_large_definitions.py）
#
# 用途：生成体积边界样本——满 50 节点（合法上限）与 51 节点（超限非法）。
# 说明：手工维护 50 节点 YAML 易出错，故用脚本生成，保证结构与计数准确。
# 使用：python tests/fixtures/generate_large_definitions.py
# =============================================================================

import os

# fixture 根目录（脚本所在目录）
FIXTURE_ROOT = os.path.dirname(os.path.abspath(__file__))


def build_sequential_yaml(node_count: int, definition_id: str, name: str) -> str:
    """生成「触发器 + (node_count-1) 个 Agent」的顺序流水线 YAML。

    参数：
        node_count    —— 节点总数（含触发器）
        definition_id —— 定义标识
        name          —— 定义名称
    """
    lines = [
        'spec_version: "1.0"',
        f"definition_id: {definition_id}",
        f"name: {name}",
        "definition_version: 1",
        "",
        "inputs: []",
        "outputs: []",
        "",
        "nodes:",
        "  - node_id: trigger_manual",
        "    name: 手动触发",
        "    node_type: trigger",
        "    configuration:",
        "      trigger_type: manual",
    ]
    # 其余节点均为 Agent，顺序连接
    for index in range(1, node_count):
        lines.extend(
            [
                f"  - node_id: agent_{index:02d}",
                f"    name: 节点{index:02d}",
                "    node_type: agent",
                "    configuration:",
                "      model_identifier: primary-model",
            ]
        )

    lines.append("edges:")
    for index in range(node_count - 1):
        source = "trigger_manual" if index == 0 else f"agent_{index:02d}"
        target = f"agent_{index + 1:02d}"
        lines.extend(
            [
                f"  - edge_id: e{index + 1}",
                f"    source_node_id: {source}",
                f"    target_node_id: {target}",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    """生成两个体积边界 fixture 文件。"""
    valid_path = os.path.join(FIXTURE_ROOT, "valid_workflow_definitions", "valid_max_50_nodes.yaml")
    with open(valid_path, "w", encoding="utf-8") as handle:
        handle.write("# 合法样本：满 50 节点（体积上限边界）\n")
        handle.write(build_sequential_yaml(50, "valid_max_50_nodes", "满50节点定义"))

    invalid_path = os.path.join(
        FIXTURE_ROOT, "invalid_workflow_definitions", "invalid_definition_too_large.yaml"
    )
    with open(invalid_path, "w", encoding="utf-8") as handle:
        handle.write("# 非法样本：51 节点（超过第一阶段体积上限）\n")
        handle.write(build_sequential_yaml(51, "invalid_definition_too_large", "超限51节点定义"))

    print("generated:", valid_path)
    print("generated:", invalid_path)


if __name__ == "__main__":
    main()
