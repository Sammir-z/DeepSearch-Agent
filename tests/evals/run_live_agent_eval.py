"""
DeepSearch Agent 端到端真实运行评测器 (Live Agent E2E Evaluator)

与离线静态契约测试不同，本脚本会：
1. 真正组装项目的 DeepAgent (加载主提示词、三个专家子智能体、文件工具与内存架构)
2. 真正向底层配置的模型 (DeepSeek / Qwen) 发起网络推理
3. 动态捕获并打印真实流式执行中的工具调用 (Tool Call) 与子智能体委派 (Task Delegation)
4. 真实检验大模型是否遵守契约 (如第一步严禁抢跑、是否选对子智能体、是否正确输出)
5. 记录真实端到端耗时与结果

运行命令：
uv run python tests/evals/run_live_agent_eval.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# Windows 控制台编码安全配置
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 确保项目根目录加入模块搜索路径
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from langgraph.checkpoint.memory import MemorySaver

from app.agent.llm import model
from app.agent.prompts import main_agent_content
from app.agent.subagents.database_query_agent import database_query_agent
from app.agent.subagents.knowledge_base_agent import knowledge_base_agent
from app.agent.subagents.network_search_agent import network_search_agent
from app.tools.markdown_tools import generate_markdown
from app.tools.pdf_tools import convert_md_to_pdf
from deepagents import create_deep_agent
from tests.evals.evaluator import validate_trajectory_contracts

import logging

# 日志输出配置：根目录下的 log/evals/agent_live_eval.log
log_dir = project_root / "log" / "evals"
log_dir.mkdir(parents=True, exist_ok=True)
log_file_path = log_dir / "agent_live_eval.log"

logger = logging.getLogger("Live_Agent_Eval")
logger.setLevel(logging.INFO)
if logger.hasHandlers():
    logger.handlers.clear()

file_handler = logging.FileHandler(str(log_file_path), mode="a", encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)


def log_print(msg: str = ""):
    print(msg, flush=True)
    logger.info(msg)


# 子智能体类型别名双向映射
SUBAGENT_ALIAS_MAP = {
    "tavily": ["network_search_agent", "tavily", "网络搜索助手"],
    "db": ["database_query_agent", "db", "数据库查询助手"],
    "ragflow": ["knowledge_base_agent", "ragflow", "RAGFlow助手", "知识库助手"],
    "数据库查询助手": ["database_query_agent", "db", "数据库查询助手"],
    "网络搜索助手": ["network_search_agent", "tavily", "网络搜索助手"],
    "ragflow助手": ["knowledge_base_agent", "ragflow", "RAGFlow助手", "知识库助手"],
}


def build_live_eval_agent():
    """
    组装真实的 DeepSearch Agent 图实例
    使用轻量 MemorySaver 作为 Checkpointer，避免强依赖外部未启动的 Redis 服务，
    支持本地随时随地开展真实大模型端到端压测。
    """
    checkpointer = MemorySaver()
    return create_deep_agent(
        model=model,
        system_prompt=main_agent_content["system_prompt"],
        tools=[generate_markdown, convert_md_to_pdf],
        checkpointer=checkpointer,
        subagents=[database_query_agent, network_search_agent, knowledge_base_agent],
    )


async def evaluate_single_live_case(agent, case: dict) -> dict:
    case_id = case["case_id"]
    name = case["name"]
    query = case["query"]

    log_print("\n" + "=" * 80)
    log_print(f"🧪 [真实 Agent 运行] {case_id} - {name}")
    log_print(f"📝 用户输入: {query}")
    log_print("-" * 80)

    thread_id = f"eval-{case_id.lower()}-{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}

    start_time = time.perf_counter()
    tool_sequence = []
    selected_subagents = []
    final_response = ""

    try:
        # 真正调用大模型流式执行
        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": query}]},
            config=config,
        ):
            for node_name, state in chunk.items():
                if not state or "messages" not in state:
                    continue
                messages = state["messages"]
                if not messages:
                    continue
                last_msg = messages[-1]

                # 捕获模型发出的 Tool Call
                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    for tc in last_msg.tool_calls:
                        tool_name = tc.get("name", "")
                        args = tc.get("args", {})
                        tool_sequence.append(tool_name)
                        log_print(f"  ⚡ [实时工具调用] 节点: {node_name} | 工具: {tool_name} | 参数摘要: {str(args)[:60]}...")

                        # 如果是委派子智能体任务
                        if tool_name == "task" and "subagent_type" in args:
                            sub_type = str(args["subagent_type"]).lower()
                            selected_subagents.append(sub_type)
                            log_print(f"  🤖 [实时委派子智能体] -> {sub_type}")

                # 记录最终文本输出
                if hasattr(last_msg, "content") and last_msg.content:
                    final_response = last_msg.content

        duration = time.perf_counter() - start_time
        log_print(f"⏱️ 真实执行耗时: {duration:.2f}s")
        if final_response:
            log_print(f"📄 最终响应摘要 (前150字):\n{final_response.strip()[:150]}...")

        # 真实行为断言
        eval_passed = True
        fail_reasons = []

        # 1. 轨迹时序与防抢跑断言 (验证第一步是否违规生成文件)
        if tool_sequence:
            traj_ok, traj_msg = validate_trajectory_contracts(tool_sequence)
            if not traj_ok:
                eval_passed = False
                fail_reasons.append(traj_msg)

        # 2. 路由断言：验证是否命中了期望的子智能体
        expected_subs = case.get("expected_subagents", [])
        if expected_subs and selected_subagents:
            matched = False
            for exp in expected_subs:
                exp_lower = exp.lower()
                for sel in selected_subagents:
                    aliases = SUBAGENT_ALIAS_MAP.get(sel, [sel])
                    if any(a.lower() in exp_lower or exp_lower in a.lower() for a in aliases):
                        matched = True
                        break
            if not matched:
                eval_passed = False
                fail_reasons.append(f"实际委派 [{selected_subagents}] 未命中预期 [{expected_subs}]")

        status_str = "✅ PASS" if eval_passed else f"❌ FAIL ({'; '.join(fail_reasons)})"
        log_print(f"🎯 真实评测判定: {status_str}")

        return {
            "case_id": case_id,
            "passed": eval_passed,
            "duration": duration,
            "tool_sequence": tool_sequence,
            "selected_subagents": selected_subagents,
            "fail_reasons": fail_reasons,
        }

    except Exception as e:
        duration = time.perf_counter() - start_time
        log_print(f"💥 真实运行异常崩溃: {e}")
        return {
            "case_id": case_id,
            "passed": False,
            "duration": duration,
            "tool_sequence": tool_sequence,
            "selected_subagents": selected_subagents,
            "fail_reasons": [f"执行异常: {str(e)}"],
        }


async def main():
    log_print("=" * 80)
    log_print("🚀 启动 DeepSearch Agent 真实大模型端到端评测 (Live E2E Evaluation)")
    log_print("   连接模型: DeepSeek-V4-Flash | 检查点: In-Memory Checkpointer")
    log_print(f"   详细日志: {log_file_path}")
    log_print("=" * 80)

    dataset_path = Path(__file__).parent / "dataset.json"
    with open(dataset_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    # 选取典型的测试用例进行现场实测：
    # TC-ROUTE-002: 数据库查询路由测试 (验证模型是否自主选择 db 子助手)
    # TC-SEQ-001: 研报生成任务 (验证是否遵守先检索后生成的防抢跑契约)
    target_case_ids = ["TC-ROUTE-002"]
    test_cases = [c for c in cases if c["case_id"] in target_case_ids]

    agent = build_live_eval_agent()
    results = []

    for c in test_cases:
        res = await evaluate_single_live_case(agent, c)
        results.append(res)

    log_print("\n" + "=" * 80)
    log_print("📋 真实端到端评测汇总")
    log_print("-" * 80)
    for r in results:
        status = "✅ PASS" if r["passed"] else "❌ FAIL"
        tools_summary = " -> ".join(r["tool_sequence"]) if r["tool_sequence"] else "无工具调用"
        subs_summary = ", ".join(r["selected_subagents"]) if r["selected_subagents"] else "无委派"
        log_print(f"[{status}] {r['case_id']} | 耗时: {r['duration']:.2f}s | 委派助手: {subs_summary} | 轨迹: {tools_summary}")
    log_print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
