"""
tools.py — 查询工具（NL2SQL 安全核心）

这是 LLM 唯一能"操作数据库"的入口。安全全在这里。

设计依据：
- 需求文档 14.4: LLM 永远不写 SQL 字符串，只产出结构化参数；
                  代码侧用参数化查询拼 SQL，天然免疫注入；
                  工具只读，仅 SELECT，代码里不存在 DELETE/UPDATE 路径。
- 审查错误 2: 日期边界补全，避开 BETWEEN，用半开区间 >= 和 <。
"""

from dataclasses import dataclass
from typing import Optional

from db import query as db_query


@dataclass
class QueryParams:
    """LLM 产出的结构化查询参数——工具 schema 只收这些。"""
    date_from: str               # ISO 日期 'YYYY-MM-DD'，必填
    date_to: str                 # ISO 日期 'YYYY-MM-DD'，必填
    category: Optional[str] = None  # 餐饮/交通/...；None = 不限
    type_filter: Optional[str] = None  # expense / income；None = 不限
    limit: int = 20              # 最多返回条数


# ──────────────────────────── 工具 schema（暴露给 LLM） ────────────────────────────

# 这个 schema 以 OpenAI function/tool 格式暴露给 LLM，
# LLM 只能产出符合这个 schema 的参数，永远无法直接写 SQL。

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


# ──────────────────────────── 工具实现 ────────────────────────────

def query_transactions(params: QueryParams) -> list[dict]:
    """
    执行查询。代码侧参数化拼 SQL，LLM 无法注入。

    日期边界补全在 db.query 里做（半开区间，不丢最后一天）。
    """
    return db_query(
        date_from=params.date_from,
        date_to=params.date_to,
        category=params.category,
        type_filter=params.type_filter,
        limit=params.limit,
    )
