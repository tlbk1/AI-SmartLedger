"""
tools.py — 查询参数 schema（NL2SQL 安全）

QueryParams / TOOL_SCHEMA 供 llm.py 的兜底查询参数抽取使用。
实际查询统一走 db.query_by_ledger（账本隔离，见 constitution 原则 I）；
旧的免账本查询入口（tools.query_transactions / db.query）已按 spec 003 FR-037 移除。

设计依据：
- 需求文档 14.4: LLM 永远不写 SQL 字符串，只产出结构化参数；
                  代码侧用参数化查询拼 SQL，天然免疫注入。
- 审查错误 2: 日期边界补全，避开 BETWEEN，用半开区间 >= 和 <。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class QueryParams:
    """LLM 产出的结构化查询参数——工具 schema 只收这些。"""
    date_from: str               # ISO 日期 'YYYY-MM-DD'，必填
    date_to: str                 # ISO 日期 'YYYY-MM-DD'，必填
    category: Optional[str] = None  # 餐饮/交通/...；None = 不限
    type_filter: Optional[str] = None  # expense / income；None = 不限
    limit: int = 20              # 最多返回条数


# ──────────────────────────── 工具 schema（暴露给 LLM） ────────────────────────────

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_transactions",
        "description": (
            "查询用户的账单记录。只能查（只读），不能修改/删除。"
            "时间范围必填；分类和类型可选。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "查询起始日期，格式 YYYY-MM-DD，如 2026-08-01",
                },
                "date_to": {
                    "type": "string",
                    "description": "查询结束日期，格式 YYYY-MM-DD，如 2026-08-31",
                },
                "category": {
                    "type": "string",
                    "description": "分类名（可选），如：餐饮、交通、购物、居住、娱乐、医疗、其他、工资、外快",
                },
                "type_filter": {
                    "type": "string",
                    "enum": ["expense", "income"],
                    "description": "类型（可选）：expense=支出，income=收入",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数，默认20",
                    "default": 20,
                },
            },
            "required": ["date_from", "date_to"],
        },
    },
}
