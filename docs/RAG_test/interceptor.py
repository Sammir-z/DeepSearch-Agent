"""
DeepSearch RAG 双引擎零侵入召回截获器 (Probe & Interceptor)

设计原则：
1. 绝对不修改 app/ 内部任何业务代码；
2. 在评测 Harness 中作为中间件/装饰器，对 MySQL 工具与 RAGFlow 工具调用进行动态探针截获；
3. 自动解析 Top-5 结构化行记录 (CSV) 与 Top-5 文本语义切片 (Chunks)；
4. 记录执行时延、执行状态与完整原始产物，供离线打分引擎使用。
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RetrievalRecord:
    """单次检索调用的截获记录"""
    case_id: str
    target_source: str              # "mysql_db" | "ragflow_knowledge_base" | "none_rejected"
    tool_name: str                  # 被调用的工具名
    raw_query: str                  # 用户自然语言提问
    invoked_args: dict[str, Any]    # 截获的工具入参 (如 SQL 语句, question, chat_name)
    raw_response: str               # 工具原始返回文本
    top5_records: list[Any] = field(default_factory=list)  # 解析后的 Top-5 记录 (行数据或文本切片)
    extracted_entities: set[str] = field(default_factory=set) # 提取的实体与词条集合
    duration_ms: float = 0.0        # 检索耗时
    is_rejected: bool = False       # 是否触发了拒答/空结果/降级
    rejection_reason: str = ""      # 拒答原因说明
    error_message: str | None = None


class RetrievalInterceptor:
    """零侵入拦截与探针管理器"""

    def __init__(self):
        self.records: list[RetrievalRecord] = []

    def clear(self):
        self.records.clear()

    @staticmethod
    def parse_sql_csv_top5(csv_text: str, limit: int = 5) -> tuple[list[dict[str, str]], set[str]]:
        """
        解析 MySQL 工具返回的 CSV 文本，提取前 limit 行记录及所有包含的实体特征
        """
        if not csv_text or "查询出现异常" in csv_text or "没有查询结果" in csv_text:
            return [], set()

        rows = []
        entities = set()

        try:
            f = io.StringIO(csv_text.strip())
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                if idx >= limit:
                    break
                clean_row = {k.strip(): v.strip() for k, v in row.items() if k and v}
                rows.append(clean_row)
                for val in clean_row.values():
                    if val and len(val) >= 2:
                        entities.add(val)
        except Exception:
            # 容错降级：按行拆分
            lines = [l.strip() for l in csv_text.splitlines() if l.strip()]
            if len(lines) > 1:
                header = [h.strip() for h in lines[0].split(",")]
                for line in lines[1 : limit + 1]:
                    parts = [p.strip() for p in line.split(",")]
                    row_dict = dict(zip(header, parts))
                    rows.append(row_dict)
                    entities.update([p for p in parts if len(p) >= 2])

        return rows, entities

    @staticmethod
    def parse_rag_chunks_top5(rag_text: str, limit: int = 5) -> tuple[list[str], set[str]]:
        """
        解析 RAGFlow 助手返回的切片与回答文本
        """
        if not rag_text or "异常" in rag_text or "没有可用知识库" in rag_text:
            return [], set()

        # 切片拆分（根据双换行或句号拆分语义块）
        paragraphs = [p.strip() for p in rag_text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [p.strip() for p in rag_text.split("\n") if p.strip()]

        top5 = paragraphs[:limit]
        entities = set()
        for p in top5:
            # 提取文本中出现的关键名词或专有名词
            entities.add(p)

        return top5, entities

    @staticmethod
    def detect_rejection(response_text: str) -> tuple[bool, str]:
        """
        自动化判定该次检索/回答是否触发了有效拒绝回答（Abstention）
        """
        rejection_keywords = [
            "没有查询结果",
            "没有可用知识库",
            "未检索到",
            "知识库未收录",
            "暂无公开",
            "无法回答",
            "不存在",
            "无可用助手",
            "超出范围",
            "未收录",
            "数据缺口",
            "连接失败",
            "任务受阻",
            "字段不存在",
        ]
        lower_resp = response_text.lower()
        for kw in rejection_keywords:
            if kw in response_text or kw in lower_resp:
                return True, f"命中拒答关键词 [{kw}]"

        # 如果返回内容过短且表示空
        if not response_text.strip() or response_text.strip() in ("空", "None", "{}"):
            return True, "返回空内容，视为拒答降级"

        return False, "正常回答，未拒答"

    def intercept_sql_call(
        self,
        case_id: str,
        query: str,
        sql_func: Callable[[str], str],
        sql_query: str,
    ) -> RetrievalRecord:
        """探针包装：截获 execute_sql_query"""
        start = time.perf_counter()
        error = None
        raw_res = ""

        try:
            raw_res = sql_func(sql_query)
        except Exception as e:
            error = str(e)
            raw_res = f"查询异常: {error}"

        duration = (time.perf_counter() - start) * 1000.0
        top5, entities = self.parse_sql_csv_top5(raw_res)
        is_rej, rej_reason = self.detect_rejection(raw_res)

        record = RetrievalRecord(
            case_id=case_id,
            target_source="mysql_db",
            tool_name="execute_sql_query",
            raw_query=query,
            invoked_args={"query": sql_query},
            raw_response=raw_res,
            top5_records=top5,
            extracted_entities=entities,
            duration_ms=duration,
            is_rejected=is_rej,
            rejection_reason=rej_reason,
            error_message=error,
        )
        self.records.append(record)
        return record

    def intercept_rag_call(
        self,
        case_id: str,
        query: str,
        rag_func: Callable[[str, str], str],
        chat_name: str,
        question: str,
    ) -> RetrievalRecord:
        """探针包装：截获 create_ask_delete"""
        start = time.perf_counter()
        error = None
        raw_res = ""

        try:
            raw_res = rag_func(chat_name, question)
        except Exception as e:
            error = str(e)
            raw_res = f"RAGFlow 调用异常: {error}"

        duration = (time.perf_counter() - start) * 1000.0
        top5, entities = self.parse_rag_chunks_top5(raw_res)
        is_rej, rej_reason = self.detect_rejection(raw_res)

        record = RetrievalRecord(
            case_id=case_id,
            target_source="ragflow_knowledge_base",
            tool_name="create_ask_delete",
            raw_query=query,
            invoked_args={"chat_name": chat_name, "question": question},
            raw_response=raw_res,
            top5_records=top5,
            extracted_entities=entities,
            duration_ms=duration,
            is_rejected=is_rej,
            rejection_reason=rej_reason,
            error_message=error,
        )
        self.records.append(record)
        return record
