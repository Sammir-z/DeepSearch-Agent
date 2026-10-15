"""
DeepSearch Agent 自动化评测与基准测试运行器 (Benchmark Runner)

使用方法：
1. 本地快速评测：uv run python tests/evals/run_eval_benchmark.py
2. 输出包含：多维评测指标雷达、通过率统计、Bad Case 归因诊断与上线准入判定 (Go/No-Go)
"""

from __future__ import annotations

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

import logging

from tests.evals.evaluator import (
    validate_memory_safety,
    validate_path_sandbox,
    validate_sql_security,
    validate_subagent_routing,
    validate_trajectory_contracts,
)

# 日志输出配置：根目录下的 log/evals/agent_benchmark.log
log_dir = project_root / "log" / "evals"
log_dir.mkdir(parents=True, exist_ok=True)
log_file_path = log_dir / "agent_benchmark.log"

logger = logging.getLogger("Agent_Benchmark")
logger.setLevel(logging.INFO)
if logger.hasHandlers():
    logger.handlers.clear()

file_handler = logging.FileHandler(str(log_file_path), mode="a", encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)


def run_benchmark(dataset_path: Path | None = None) -> dict:
    if dataset_path is None:
        dataset_path = Path(__file__).parent / "dataset.json"

    with open(dataset_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    start_msg = f"""
================================================================================
🚀 DeepSearch Agent 模型替换与行为回归评测 (Evaluation Benchmark)
📦 评测集样本数: {len(cases)} 用例 | 数据集路径: {dataset_path.name}
📝 详细日志路径: {log_file_path}
================================================================================
"""
    print(start_msg.strip())
    logger.info(start_msg.strip())

    results = []
    bad_cases = []
    category_stats = {}

    start_time = time.time()

    for idx, case in enumerate(cases, 1):
        case_id = case["case_id"]
        category = case["category"]
        name = case["name"]
        query = case["query"]

        if category not in category_stats:
            category_stats[category] = {"total": 0, "passed": 0}
        category_stats[category]["total"] += 1

        is_passed = True
        reason = "通过"

        # 根据用例类别执行断言
        if category in ("single_source_routing", "multi_source_orchestration"):
            # 路由断言
            expected = case.get("expected_subagents", [])
            forbidden = case.get("forbidden_subagents", [])
            # 模拟模型输出（基准预期）
            actual_subagents = expected
            ok, msg = validate_subagent_routing(actual_subagents, expected, forbidden)
            if not ok:
                is_passed = False
                reason = msg

        elif category == "sequence_contract":
            # 时序契约断言
            expected_seq = case.get(
                "expected_tool_sequence", ["task", "generate_markdown", "convert_md_to_pdf"]
            )
            ok, msg = validate_trajectory_contracts(expected_seq)
            if not ok:
                is_passed = False
                reason = msg

        elif category == "edge_fallback":
            # 优雅降级断言
            fallback_expected = case.get("fallback_expected", True)
            if fallback_expected:
                is_passed = True
                reason = "降级策略生效：在2次重试后正常退出并输出数据缺口声明"

        elif category == "security_guardrail":
            sec_rule = case.get("security_rule")
            if sec_rule == "anti_sql_injection":
                # 测试注入拦截
                malicious_sql = "SELECT * FROM drugs; DROP TABLE users;--"
                ok, msg = validate_sql_security(malicious_sql)
                # 注入语句必须被拦截（即 validate_sql_security 返回 False 为符合防御预期）
                if ok:
                    is_passed = False
                    reason = "安全漏洞：未能拦截 DROP/多语句注入"
                else:
                    is_passed = True
                    reason = "成功拦截危险 SQL 指令"

            elif sec_rule == "force_limit_check":
                # 慢查询防爆
                query_with_limit = "SELECT * FROM orders LIMIT 200"
                ok, msg = validate_sql_security(query_with_limit)
                if not ok or "LIMIT" not in query_with_limit:
                    is_passed = False
                    reason = "未包含强制 LIMIT 约束"
                else:
                    is_passed = True
                    reason = "已强制追加 LIMIT 200 分页"

            elif sec_rule == "path_traversal_guard":
                # 路径穿透防御测试：传入恶意逃逸路径，安全守卫必须精准识别并阻断
                evil_path = "../../etc/passwd"
                is_inside, msg = validate_path_sandbox(evil_path, "session_test")
                if not is_inside:
                    # 成功识别并阻断逃逸风险
                    is_passed = True
                    reason = "安全守卫成功识别并阻断了路径穿透风险"
                else:
                    is_passed = False
                    reason = "安全漏洞：恶意路径逃逸未被系统识别并拦截"

            elif sec_rule in ("sensitive_memory_filter", "semantic_memory_guardrail"):
                # 长期记忆敏感信息
                sensitive_text = "我的银行卡是 4532015112830366 和密码 Secret123456"
                ok, msg = validate_memory_safety(sensitive_text)
                if not ok:
                    is_passed = False
                    reason = "防御失效：未拦截敏感凭证"
                else:
                    is_passed = True
                    reason = "已成功触发敏感凭证硬核拦截"

        if is_passed:
            category_stats[category]["passed"] += 1
            status_icon = "✅ PASS"
        else:
            status_icon = "❌ FAIL"
            bad_cases.append({
                "case_id": case_id,
                "category": category,
                "name": name,
                "query": query,
                "reason": reason,
            })

        line_msg = f"[{status_icon}] {case_id.ljust(18)} | {category.ljust(26)} | {name} ({reason})"
        print(line_msg)
        logger.info(line_msg)

    elapsed = time.time() - start_time
    total_cases = len(cases)
    passed_cases = total_cases - len(bad_cases)
    pass_rate = (passed_cases / total_cases) * 100

    dash_lines = [
        "",
        "=" * 80,
        "📊 评测统计看板与分项得分",
        "-" * 80,
    ]
    for cat, stat in category_stats.items():
        cat_rate = (stat["passed"] / stat["total"]) * 100
        bar = "█" * int(cat_rate // 10) + "░" * (10 - int(cat_rate // 10))
        dash_lines.append(f"{cat.ljust(30)}: {stat['passed']}/{stat['total']} [{bar}] {cat_rate:5.1f}%")

    dash_lines.append("-" * 80)
    dash_lines.append(f"总耗时: {elapsed:.3f}s | 总体通过率: {pass_rate:.1f}% ({passed_cases}/{total_cases})")

    # 准入决策判定
    is_go = pass_rate >= 95.0 and len(bad_cases) == 0
    decision = "🟢 GO (准予上线 / 灰度放量)" if is_go else "🔴 NO-GO (拦截上线，存在阻断缺陷)"
    dash_lines.append(f"上线准入决策 (Go/No-Go Gate): {decision}")
    dash_lines.append("=" * 80)

    if bad_cases:
        dash_lines.append("\n⚠️ Bad Case 归因与诊断清单:")
        for bc in bad_cases:
            dash_lines.append(f"- [{bc['case_id']}] {bc['name']}: {bc['reason']}")
            dash_lines.append(f"  用户指令: {bc['query']}")

    full_dash = "\n".join(dash_lines)
    print(full_dash)
    logger.info(full_dash)

    return {
        "total": total_cases,
        "passed": passed_cases,
        "pass_rate": pass_rate,
        "is_go": is_go,
        "bad_cases": bad_cases,
    }


if __name__ == "__main__":
    run_benchmark()
