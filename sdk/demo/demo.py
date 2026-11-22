#!/usr/bin/env python
# -*- coding: utf-8 -*-
# =============================================================================
# wfp-sdk 演示脚本（sdk/demo/demo.py）
#
# 职责：B2 验收演练的端到端 demo——「提交 → 触发 → 订阅 → 取结果 → 取消」
#       全链路走通（roadmap B2 验收标准）。
# 设计意图：
#   1. 免 LLM：内嵌最小 YAML，业务节点为 code 节点（纯计算，白名单内建），
#      不依赖任何模型/工具配置；
#   2. 演练可判定：逐步输出「DEMO STEP n OK」，终局「DEMO PASS」；
#      任一步失败输出「DEMO FAIL」并退出码 1；
#   3. 取消容忍分支（契约 §1-7）：快实例可能在 cancel 到达前已终态，
#      409 INSTANCE_CANCEL_CONFLICT 视为符合预期（竞态容忍，终态以轮询为准）。
# 依赖：仅 wfp_sdk（httpx）；服务端需 CODE_NODE_ENABLED=true（代码节点开关，
#       平台默认关闭——drill 启动命令中设置）。
# 用法：python demo/demo.py --base-url http://127.0.0.1:8310
# =============================================================================

import argparse
import sys

from wfp_sdk import (
    INSTANCE_CANCEL_CONFLICT,
    WorkflowClient,
    WfpStateConflictError,
)

# 演示工作流（免 LLM）：trigger → code（params 全量平铺 = 实例入参 + 上游输出）
DEMO_YAML = """\
spec_version: "1.0"
definition_id: b2_sdk_demo_compute
name: B2 SDK Demo Compute
definition_version: 1

inputs:
  - name: payload
    type: string
    required: true
    description: Demo payload text
  - name: base
    type: number
    required: true
    description: Demo base number

outputs:
  - name: answer
    type: object
    description: Computed answer (echo + doubled)

nodes:
  - node_id: trigger_manual
    name: Manual Trigger
    node_type: trigger
    configuration:
      trigger_type: manual

  - node_id: compute_answer
    name: Compute Answer
    node_type: code
    output_name: answer
    configuration:
      language: python
      code: |
        result = {"echo": params["payload"], "doubled": params["base"] * 2}

edges:
  - edge_id: e1
    source_node_id: trigger_manual
    target_node_id: compute_answer
"""

DEFINITION_ID = "b2_sdk_demo_compute"
START_INPUTS = {"payload": "hello-b2", "base": 21}


def main() -> int:
    parser = argparse.ArgumentParser(description="wfp-sdk B2 demo（免 LLM 端到端）")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8310",
        help="平台地址（默认 http://127.0.0.1:8310）",
    )
    args = parser.parse_args()

    # Windows 控制台 GBK 兜底：强制 UTF-8 输出（演练日志重定向可读）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    with WorkflowClient(args.base_url) as client:
        # ---- STEP 1: 校验（恒返报告，非法定义看 report.valid）----
        report = client.validate_definition(DEMO_YAML)
        if not report.valid:
            print(f"DEMO FAIL: validate_definition invalid: {report.issues}")
            return 1
        print(f"DEMO STEP 1 OK validate_definition (valid={report.valid})")

        # ---- STEP 2: 提交定义 ----
        receipt = client.submit_definition(DEMO_YAML)
        print(
            f"DEMO STEP 2 OK submit_definition "
            f"({receipt.definition_id}, version={receipt.definition_version})"
        )

        # ---- STEP 3: 触发执行 ----
        info = client.start_instance(
            DEFINITION_ID, DEMO_YAML, inputs=START_INPUTS, business_category="api"
        )
        print(f"DEMO STEP 3 OK start_instance ({info.instance_id}, status={info.status})")

        # ---- STEP 4: SSE 订阅（终态收尾帧后迭代自然结束）----
        event_types = [e.event_type for e in client.stream_events(info.instance_id)]
        if "INSTANCE_STARTED" not in event_types:
            print(f"DEMO FAIL: stream_events missing INSTANCE_STARTED: {event_types}")
            return 1
        print(f"DEMO STEP 4 OK stream_events ({len(event_types)}: {','.join(event_types)})")

        # ---- STEP 5: 等待终态 ----
        final = client.wait_for_completion(info.instance_id, timeout=60.0)
        if final.status != "succeeded":
            print(f"DEMO FAIL: instance terminal status={final.status}")
            return 1
        print(f"DEMO STEP 5 OK wait_for_completion (status={final.status})")

        # ---- STEP 6: 取结果（outputs=终态 node_outputs）----
        # code 节点输出契约：output_name 下挂 {"result": <脚本 result>}
        # （code_node_executor 段五：成功输出 {"result": ...}）
        results = client.get_results(info.instance_id)
        answer = (results.outputs or {}).get("answer", {})
        inner = answer.get("result", {}) if isinstance(answer, dict) else {}
        if inner.get("doubled") != 42 or inner.get("echo") != "hello-b2":
            print(f"DEMO FAIL: unexpected answer: {answer}")
            return 1
        print(f"DEMO STEP 6 OK get_results (answer={inner})")

        # ---- STEP 7: 取消容忍分支（快实例竞态：409 亦符合预期）----
        info2 = client.start_instance(
            DEFINITION_ID, DEMO_YAML, inputs=START_INPUTS
        )
        try:
            client.cancel_instance(info2.instance_id)
            print(f"DEMO STEP 7 OK cancel_instance (accepted, {info2.instance_id})")
        except WfpStateConflictError as exc:
            if exc.code != INSTANCE_CANCEL_CONFLICT:
                print(f"DEMO FAIL: cancel_instance unexpected code={exc.code}")
                return 1
            print(
                f"DEMO STEP 7 OK cancel_instance "
                f"(conflict tolerated: {exc.code}, {info2.instance_id})"
            )
        # 第二实例收尾至终态，避免演示进程退出时悬挂后台协程
        try:
            client.wait_for_completion(info2.instance_id, timeout=60.0)
        except Exception:
            pass

    print("DEMO PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
