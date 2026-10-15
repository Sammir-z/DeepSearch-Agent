"""
自动化回归测试套件 (Automated Regression Test Suite)

可直接由 unittest 驱动，作为 CI/CD 质量门禁与换模回归测试：
uv run python -m unittest tests/evals/test_regression_evals.py
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.memory.user_memory import contains_sensitive_content
from app.utils.word_converter import convert_md_to_pdf
from tests.evals.evaluator import (
    validate_memory_safety,
    validate_path_sandbox,
    validate_report_quality,
    validate_sql_security,
    validate_subagent_routing,
    validate_trajectory_contracts,
)


class TestDeepSearchRegressionEvals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dataset_path = Path(__file__).parent / "dataset.json"
        with open(dataset_path, "r", encoding="utf-8") as f:
            cls.benchmark_dataset = json.load(f)

    def test_routing_benchmark_dataset_loaded(self):
        """验证黄金评测集有效加载且覆盖 5 大核心类别"""
        self.assertGreaterEqual(len(self.benchmark_dataset), 10)
        categories = {item["category"] for item in self.benchmark_dataset}
        expected_categories = {
            "single_source_routing",
            "multi_source_orchestration",
            "sequence_contract",
            "edge_fallback",
            "security_guardrail",
        }
        self.assertTrue(expected_categories.issubset(categories))

    def test_subagent_routing_isolation_rules(self):
        """回归测试：专家助手路由与负向隔离规则"""
        # 1. 纯网络搜索用例不能误调数据库助手
        ok, msg = validate_subagent_routing(
            selected_subagents=["network_search_agent"],
            expected_subagents=["network_search_agent"],
            forbidden_subagents=["database_query_agent"],
        )
        self.assertTrue(ok, msg)

        # 2. 模拟 Badcase：如果模型错误调用了数据库助手，必须被断言捕获
        bad_ok, _ = validate_subagent_routing(
            selected_subagents=["database_query_agent"],
            expected_subagents=["network_search_agent"],
            forbidden_subagents=["database_query_agent"],
        )
        self.assertFalse(bad_ok, "应拦截误调数据库助手的 Badcase")

    def test_trajectory_anti_race_condition(self):
        """回归测试：防抢跑契约与严格时序"""
        # 合法时序：先 task 获取信息 -> generate_markdown -> convert_md_to_pdf
        valid_trajectory = ["task", "generate_markdown", "convert_md_to_pdf"]
        ok, msg = validate_trajectory_contracts(valid_trajectory)
        self.assertTrue(ok, msg)

        # 模拟抢跑 Badcase：第一步直接调用 generate_markdown
        race_trajectory = ["generate_markdown", "task"]
        bad_ok, _ = validate_trajectory_contracts(race_trajectory)
        self.assertFalse(bad_ok, "必须拦截第一步抢跑生成的 Badcase")

        # 模拟乱序 Badcase：未生成 Markdown 直接调用 convert_md_to_pdf
        order_bad_trajectory = ["task", "convert_md_to_pdf"]
        bad_ok2, _ = validate_trajectory_contracts(order_bad_trajectory)
        self.assertFalse(bad_ok2, "必须拦截未生成 Markdown 就转 PDF 的 Badcase")

    def test_sql_security_and_anti_injection(self):
        """回归测试：Text-to-SQL 注入防御与只读合规"""
        # 合法 SQL
        valid_sql = "SELECT id, drug_name, price FROM drug_inventory WHERE stock < 50 LIMIT 100"
        ok, msg = validate_sql_security(valid_sql)
        self.assertTrue(ok, msg)

        # 拦截恶意 DDL / DML
        malicious_sqls = [
            "DROP TABLE users;",
            "SELECT * FROM drugs; DELETE FROM orders WHERE 1=1;",
            "TRUNCATE TABLE audit_log",
            "UPDATE user_accounts SET role='admin'",
        ]
        for bad_sql in malicious_sqls:
            bad_ok, _ = validate_sql_security(bad_sql)
            self.assertFalse(bad_ok, f"未能拦截恶意 SQL: {bad_sql}")

    def test_sandbox_path_traversal_guard(self):
        """回归测试：文件工具沙箱路径防穿透"""
        with tempfile.TemporaryDirectory() as temp_session_dir:
            # 1. 正常相对路径，必须收敛在 temp_session_dir 内
            ok, msg = validate_path_sandbox("report.md", temp_session_dir)
            self.assertTrue(ok, msg)

            # 2. 模拟恶意穿透路径：如试图穿透到系统敏感目录
            # 在 Windows 上模拟越权访问其他绝对路径
            host_evil_path = "C:/Windows/System32/evil.md"
            ok_evil, _ = validate_path_sandbox(host_evil_path, temp_session_dir)
            # 外部路径不应被视为落在 temp_session_dir 内
            self.assertFalse(ok_evil, "必须识别并阻断逃逸出当前会话目录的路径")

    def test_long_term_memory_security_guard(self):
        """回归测试：长期记忆敏感信息硬核拦截（凭证与 Luhn 算法）"""
        sensitive_samples = [
            "我的银行卡是 4532015112830366",
            "password: MySuperSecretPassword123!",
            "my api_key is sk-1234567890abcdef12345678",
            "身份证为 110101199003072345",
        ]
        for sample in sensitive_samples:
            ok, msg = validate_memory_safety(sample)
            self.assertTrue(ok, f"未成功拦截高危凭证: {sample}")

        # 正常业务偏好不应被误杀
        normal_preference = "我偏好中文撰写的深度研报，要求重点分析财务毛利指标"
        self.assertFalse(
            contains_sensitive_content(normal_preference),
            "正常偏好不应被敏感词过滤器误杀",
        )

    def test_report_quality_and_pdf_rendering(self):
        """回归测试：中文字体与复杂表格 PDF 真实编译闭环"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            md_file = temp_path / "test_report.md"
            pdf_file = temp_path / "test_report.pdf"

            sample_markdown = """# 2024 年行业深度研究报告

【数据来源：公开财报披露与业务底表交叉验证】

## 一、核心业务指标统计

| 药品通用名 | 规格包装 | 季度销售额 (万元) | 同比增速 (%) | 审批状态 |
| :--- | :--- | :--- | :--- | :--- |
| 复方甘草片 | 100片/瓶 | 1,245.50 | +12.3% | 已上市 |
| 盐酸左氧氟沙星胶囊 | 0.25g*24粒 | 3,890.12 | +24.8% | 已上市 |
| 靶向抗癌抑制剂-TX01 | 50mg*30支 | 8,420.00 | +145.0% | III期临床 |

## 二、总结与研判

经核对内部业务库与外部公开统计数据，抗肿瘤管线产品呈现高速增长态势。
[1] 官网公告：2024年三季度正式进入医保谈判目录。
"""
            # 1. 报告内容质量校验
            ok_quality, msg_q = validate_report_quality(
                sample_markdown, min_length=200, check_citations=True
            )
            self.assertTrue(ok_quality, msg_q)

            # 2. 真实调用 ReportLab 编译为 PDF
            md_file.write_text(sample_markdown, encoding="utf-8")
            result_msg = convert_md_to_pdf(md_file, pdf_file)

            # 断言转换成功且生成了真实的 PDF 文件
            self.assertIn("成功", result_msg)
            self.assertTrue(pdf_file.exists())
            self.assertGreater(pdf_file.stat().st_size, 1024, "PDF 文件大小应大于 1KB")


if __name__ == "__main__":
    unittest.main()
