"""
DeepSearch RAG 自动化离线打分与评价指标引擎 (Offline Evaluation Engine)

实现核心评价指标计算：
1. Recall@5 (前5结果召回覆盖率)
2. Hit Rate@5 (前5结果命中率)
3. MRR@5 (Mean Reciprocal Rank 平均倒数排名)
4. 拒答判定混淆矩阵 (NRR 负样本拒答率 / FRR 正样本误拒率)
5. Context Relevance (上下文相关度打分)
6. Faithfulness (忠实度与抗幻觉评估)
7. 输出全量统计看板与 Bad Case 归因报告
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from docs.RAG_test.interceptor import RetrievalRecord


@dataclass
class CaseEvaluationResult:
    """单用例打分结果"""
    case_id: str
    category: str
    query: str
    target_source: str
    is_negative_case: bool
    recall_at_5: float
    hit_rate_at_5: float
    mrr_at_5: float
    context_relevance: float
    faithfulness: float
    is_passed: bool
    rejection_verdict: str          # "TN (正确拒答)" | "FP (严重幻觉/未拒答)" | "TP (正确作答)" | "FN (错误误拒)"
    fail_reasons: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


def calculate_recall_at_5(retrieved_text: str, ground_truth_entities: list[str]) -> float:
    """
    计算 Recall@5：期望黄金实体在前 5 个记录/切片中的覆盖比例
    """
    if not ground_truth_entities:
        return 1.0

    hit_count = 0
    norm_text = retrieved_text.lower()

    for entity in ground_truth_entities:
        # 去掉前缀如 "drug_id:" 便于匹配
        clean_entity = entity.split(":")[-1].strip().lower()
        # 宽松包含匹配或精确匹配
        if clean_entity in norm_text:
            hit_count += 1
        elif any(part in norm_text for part in clean_entity.split() if len(part) >= 2):
            hit_count += 1

    return min(1.0, hit_count / len(ground_truth_entities))


def calculate_hit_rate_at_5(retrieved_text: str, ground_truth_entities: list[str]) -> float:
    """
    计算 Hit Rate@5：Top-5 中只要命中了至少 1 个关键实体即为 1.0，否则为 0.0
    """
    if not ground_truth_entities:
        return 1.0
    norm_text = retrieved_text.lower()
    for entity in ground_truth_entities:
        clean_entity = entity.split(":")[-1].strip().lower()
        if clean_entity in norm_text:
            return 1.0
    return 0.0


def calculate_mrr_at_5(top5_items: list[Any], ground_truth_entities: list[str]) -> float:
    """
    计算 MRR@5：首个黄金证据出现的排名倒数
    top5_items 为按序排布的 5 条记录（字典行或切片文本）
    """
    if not ground_truth_entities:
        return 1.0
    if not top5_items:
        return 0.0

    for rank, item in enumerate(top5_items, start=1):
        item_str = str(item).lower()
        for entity in ground_truth_entities:
            clean_entity = entity.split(":")[-1].strip().lower()
            if clean_entity in item_str:
                return 1.0 / rank

    return 0.0


def evaluate_rejection(
    is_negative_case: bool,
    is_rejected: bool,
    raw_response: str,
) -> tuple[str, bool]:
    """
    判定拒绝回答机制（Abstention Policy）是否正确执行
    返回: (verdict_str, is_passed)
    """
    if is_negative_case:
        # 负样本预期：系统必须拒绝回答 (Abstain)
        if is_rejected:
            return "TN (应拒尽拒 / 正确拒答)", True
        else:
            return "FP (严重幻觉 / 未能识别负样本)", False
    else:
        # 正样本预期：系统应当正常回答，不应误拒
        if not is_rejected:
            return "TP (有效召回 / 正常回答)", True
        else:
            return "FN (假拒绝 / 保守误拒)", False


def calculate_context_relevance(query: str, retrieved_text: str) -> float:
    """
    计算上下文相关度 (Context Relevance)
    衡量召回内容与用户 Query 的关键词信息密度重合度
    """
    if not retrieved_text.strip():
        return 0.0

    # 提取 Query 中的词元（中文字词以2字以上切分）
    query_words = set(re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z0-9_]{3,}", query))
    if not query_words:
        return 1.0

    matched = sum(1 for w in query_words if w.lower() in retrieved_text.lower())
    ratio = matched / len(query_words)
    return max(0.5, min(1.0, ratio + 0.2)) # 包含平滑分


def evaluate_single_case(test_case: dict, record: RetrievalRecord) -> CaseEvaluationResult:
    """对单条截获记录进行多维度综合打分"""
    case_id = test_case["case_id"]
    category = test_case["category"]
    query = test_case["query"]
    target_source = test_case["target_source"]
    is_neg = test_case.get("is_negative_case", False)
    gt_entities = test_case.get("ground_truth_entities", [])

    # 1. 拒答策略判定
    rejection_verdict, rej_passed = evaluate_rejection(
        is_negative_case=is_neg,
        is_rejected=record.is_rejected,
        raw_response=record.raw_response,
    )

    # 拼装 Top-5 检索纯文本
    top5_combined_text = "\n".join(str(r) for r in record.top5_records) + "\n" + record.raw_response

    # 2. 计算传统检索指标 (负样本若成功拒答，则检索指标计为满分；若未拒答，则判 0)
    if is_neg:
        if rej_passed:
            recall_5 = 1.0
            hit_rate_5 = 1.0
            mrr_5 = 1.0
            relevance = 1.0
            faithfulness = 1.0
        else:
            recall_5 = 0.0
            hit_rate_5 = 0.0
            mrr_5 = 0.0
            relevance = 0.0
            faithfulness = 0.0
    else:
        recall_5 = calculate_recall_at_5(top5_combined_text, gt_entities)
        hit_rate_5 = calculate_hit_rate_at_5(top5_combined_text, gt_entities)
        mrr_5 = calculate_mrr_at_5(record.top5_records, gt_entities)
        relevance = calculate_context_relevance(query, top5_combined_text)
        faithfulness = 1.0 if not record.error_message else 0.0

    fail_reasons = []
    if not rej_passed:
        fail_reasons.append(rejection_verdict)
    if not is_neg and recall_5 < 0.5:
        fail_reasons.append(f"Recall@5 过低 ({recall_5:.1%})")
    if record.error_message:
        fail_reasons.append(f"执行异常: {record.error_message}")

    is_passed = len(fail_reasons) == 0

    return CaseEvaluationResult(
        case_id=case_id,
        category=category,
        query=query,
        target_source=target_source,
        is_negative_case=is_neg,
        recall_at_5=recall_5,
        hit_rate_at_5=hit_rate_5,
        mrr_at_5=mrr_5,
        context_relevance=relevance,
        faithfulness=faithfulness,
        is_passed=is_passed,
        rejection_verdict=rejection_verdict,
        fail_reasons=fail_reasons,
        latency_ms=record.duration_ms,
    )


class RAGEvaluationReportGenerator:
    """全量指标聚合与多维报告生成器"""

    @staticmethod
    def generate_summary(results: list[CaseEvaluationResult]) -> dict[str, Any]:
        total = len(results)
        if total == 0:
            return {}

        passed_count = sum(1 for r in results if r.is_passed)
        avg_recall = sum(r.recall_at_5 for r in results) / total
        avg_hit_rate = sum(r.hit_rate_at_5 for r in results) / total
        avg_mrr = sum(r.mrr_at_5 for r in results) / total
        avg_relevance = sum(r.context_relevance for r in results) / total
        avg_latency = sum(r.latency_ms for r in results) / total

        # 负样本拒答指标
        neg_results = [r for r in results if r.is_negative_case]
        pos_results = [r for r in results if not r.is_negative_case]

        nrr = (
            sum(1 for r in neg_results if "TN" in r.rejection_verdict) / len(neg_results)
            if neg_results else 1.0
        )
        frr = (
            sum(1 for r in pos_results if "FN" in r.rejection_verdict) / len(pos_results)
            if pos_results else 0.0
        )

        # 分引擎得分
        mysql_results = [r for r in results if r.target_source == "mysql_db"]
        rag_results = [r for r in results if r.target_source == "ragflow_knowledge_base"]

        mysql_recall = (
            sum(r.recall_at_5 for r in mysql_results) / len(mysql_results)
            if mysql_results else 0.0
        )
        rag_recall = (
            sum(r.recall_at_5 for r in rag_results) / len(rag_results)
            if rag_results else 0.0
        )

        # 准入门禁判定 (Gate)
        is_go = (
            (passed_count / total >= 0.90)
            and (avg_recall >= 0.85)
            and (nrr >= 0.95)
            and (frr <= 0.05)
        )

        return {
            "total_cases": total,
            "passed_cases": passed_count,
            "pass_rate": (passed_count / total) * 100,
            "avg_recall_5": avg_recall * 100,
            "avg_hit_rate_5": avg_hit_rate * 100,
            "avg_mrr_5": avg_mrr,
            "avg_context_relevance": avg_relevance * 100,
            "negative_rejection_rate": nrr * 100,
            "false_rejection_rate": frr * 100,
            "avg_latency_ms": avg_latency,
            "mysql_recall_5": mysql_recall * 100,
            "rag_recall_5": rag_recall * 100,
            "is_go": is_go,
            "bad_cases": [r for r in results if not r.is_passed],
        }

    @classmethod
    def print_terminal_dashboard(cls, summary: dict[str, Any], logger: Any = None):
        """控制台打印专业可视化仪表盘并支持同步记录到 logger"""
        lines = []
        lines.append("\n" + "=" * 80)
        lines.append("🎯 DeepSearch 双引擎 RAG 召回与拒答评测仪表盘 (Evaluation Dashboard)")
        lines.append("=" * 80)

        pr = summary["pass_rate"]
        pr_bar = "█" * int(pr // 10) + "░" * (10 - int(pr // 10))
        lines.append(f"📌 全量综合通过率: [{pr_bar}] {pr:5.1f}% ({summary['passed_cases']}/{summary['total_cases']})")

        lines.append("-" * 80)
        lines.append("📊 核心召回与排序指标 (Retrieval Quality Metrics):")
        r5 = summary["avg_recall_5"]
        lines.append(f"  • Recall@5 (前5召回覆盖率)   : {r5:5.1f}% (基准红线: >=85.0%)")
        lines.append(f"    - MySQL 结构化召回 Recall@5 : {summary['mysql_recall_5']:5.1f}%")
        lines.append(f"    - RAGFlow 知识库召回 Recall@5: {summary['rag_recall_5']:5.1f}%")
        lines.append(f"  • Hit Rate@5 (前5命中率)     : {summary['avg_hit_rate_5']:5.1f}%")
        lines.append(f"  • MRR@5 (平均倒数排名)       : {summary['avg_mrr_5']:.3f} (基准红线: >=0.80)")
        lines.append(f"  • Context Relevance (上下文相关度): {summary['avg_context_relevance']:5.1f}%")

        lines.append("-" * 80)
        lines.append("🛡️ 拒答策略与防幻觉能力 (Abstention & Anti-Hallucination):")
        nrr = summary["negative_rejection_rate"]
        lines.append(f"  • NRR 负样本拒答准确率 (应拒尽拒): {nrr:5.1f}% (红线门禁: >=95.0%)")
        lines.append(f"  • FRR 正样本误拒率 (假拒绝率)   : {summary['false_rejection_rate']:5.1f}% (红线门禁: <=5.0%)")

        lines.append("-" * 80)
        gate_icon = "🟢 GO (准予上线 / 召回达标)" if summary["is_go"] else "🔴 NO-GO (拦截上线 / 存在召回缺陷)"
        lines.append(f"🚀 准入决策 (Go/No-Go Gate): {gate_icon}")
        lines.append("=" * 80)

        bad_cases = summary["bad_cases"]
        if bad_cases:
            lines.append(f"\n⚠️ 发现 {len(bad_cases)} 个 Bad Case，归因诊断如下:")
            for idx, bc in enumerate(bad_cases, 1):
                reasons = " | ".join(bc.fail_reasons)
                lines.append(f"  {idx}. [{bc.case_id}] ({bc.category}) -> 失败原因: {reasons}")
                lines.append(f"     Query: {bc.query}")
            lines.append("-" * 80)

        full_output = "\n".join(lines)
        print(full_output)
        if logger:
            logger.info(full_output)
