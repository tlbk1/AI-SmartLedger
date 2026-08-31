"""
agent.py — Loop Agent（循环决策核心）

目标：把「固定路由」升级为「会循环思考、调用工具、观察结果、自主决定下一步」的 Agent。

用 LangGraph 的 create_react_agent 搭标准 ReAct 循环：
    思考 → 调用工具 → 观察结果 → 再思考 → ... → 直到 agent 认为「任务完成」

设计依据：
- 需求文档 5 章：意图识别 → 记账/查账/闲聊
- 技术方案：tool calling 强制结构化输出
- 方案文档 docs/loop-agent-改造方案.md
"""

import logging
import os
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

import db

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")

# ──────────────────────────── LLM 客户端 ────────────────────────────

# 模块级就加载 .env，避免被其他模块先 import 时 os.environ 为空
load_dotenv()

# 懒加载 ChatOpenAI（langchain 版，供 create_react_agent 用）
_model_obj = None


def _get_model():
    """
    返回 langchain 的 ChatOpenAI，指向公司中转站。
    用 ChatOpenAI 而不是原生 openai.OpenAI，因为 create_react_agent 需要
    能 .bind_tools() 的 BaseChatModel。
    """
    global _model_obj
    if _model_obj is None:
        from langchain_openai import ChatOpenAI
        _model_obj = ChatOpenAI(
            base_url=os.environ.get("LLM_BASE_URL", "https://ccc.szprize.cn/v1"),
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
            temperature=0,
        )
    return _model_obj


def _model_name() -> str:
    return os.environ.get("LLM_MODEL", "deepseek-v4-flash")


# ──────────────────────────── Agent 工具 ────────────────────────────
# 这里定义 agent 能调用的工具。用 `tool` 装饰器让 langchain 自动生成 schema。
# 工具内部复用了 db.py 的现有能力（query / insert_many），保持数据层不变。

from langchain_core.tools import tool


@tool
def query_transactions(
    date_from: str,
    date_to: str,
    category: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 20,
) -> str:
    """查询账单记录（只读）。根据日期范围必填；分类和类型可选。返回 JSON 字符串。"""
    rows = db.query(
        date_from=date_from,
        date_to=date_to,
        category=category,
        type_filter=type_filter,
        limit=limit,
    )
    import json
    return json.dumps(rows, ensure_ascii=False)


@tool
def record_transactions(transactions: list) -> str:
    """一次性记录一笔或多笔账单（整批事务，全成全不记）。

    transactions 是列表，每项包含：
      type: 'expense' 或 'income'
      amount: 金额（元）
      category: 分类（餐饮/交通/购物/居住/娱乐/医疗/其他/工资/外快/其他）
      note: 备注（可选）
      happened_at: 发生时间 ISO 字符串（可选，默认现在）
    成功返回 "✓ 已记 N 笔"，失败返回错误信息。
    """
    txns = []
    for t in transactions:
        txns.append(
            db.Transaction(
                type=str(t.get("type", "expense")),
                amount=float(t["amount"]),
                category=str(t.get("category", "其他")),
                note=str(t.get("note", "")),
                happened_at=str(t.get("happened_at") or ""),
            )
        )
    ok = db.insert_many(txns)
    return f"✓ 已记 {len(txns)} 笔" if ok else "没记上，请重试"


@tool
def ask_clarify(question: str) -> str:
    """当信息不足（比如缺金额、分类不明）时，向用户反问澄清。传入要问的话。"""
    return f"<澄清> {question}"


AGENT_TOOLS = [query_transactions, record_transactions, ask_clarify]


# ──────────────────────────── Agent 系统提示 ────────────────────────────

AGENT_SYSTEM_PROMPT = """\
你是一个微信记账机器人的核心 Agent。用户用自然语言告诉你他的收支情况，你负责处理成结果。

当前时间（Asia/Shanghai）：{now}

你有以下工具可用：
- query_transactions: 查询账单（只读），根据日期/分类/类型查记录
- record_transactions: 记一笔或多笔账（支出/收入）
- ask_clarify: 信息不足时反问用户

预设分类（只能用这些，拿不准归「其他」）：
- 支出（7类）：餐饮、交通、购物、居住、娱乐、医疗、其他
- 收入（3类）：工资、外快、其他

工作方式（重要）：
1. 用户说记账 → 先用 record_transactions 记下，然后简单确认
2. 用户说查账 → 先用 query_transactions 查数据，看到结果后总结成自然语言
3. 信息不足（缺金额/分类不明）→ 用 ask_clarify 反问用户，直到信息够了再继续
4. 不确定用户在干嘛 → 友好地打招呼，提示用法

规则：
- 金额默认单位「元」
- 时间默认「现在」，支持「昨天」「上周五」等相对时间
- 每步只调用最需要的工具，不要重复查询
- 最终回答要简洁、口语化，像一个贴心的记账助手
"""


# ──────────────────────────── Agent 实例（懒加载） ────────────────────────────

_agent = None


def _get_agent():
    """懒加载编译好的 ReAct agent。"""
    global _agent
    if _agent is None:
        from langgraph.prebuilt import create_react_agent
        now = __import__("datetime").datetime.now(SHANGHAI).isoformat()
        _agent = create_react_agent(
            model=_get_model(),
            tools=AGENT_TOOLS,
            prompt=AGENT_SYSTEM_PROMPT.format(now=now),
        )
    return _agent


# ──────────────────────────── 主入口 ────────────────────────────

# 失败哨兵：agent 崩溃/报错时返回这个特殊标记，
# 上游 graph.py 识别到它就触发「三分支兜底」。
# 用极不可能出现在正常回复中的字符串，防止与真实回答撞车。
AGENT_FAILURE = "\x00__AGENT_FAILED__\x00"


def run_agent(openid: str, content: str) -> str:
    """
    处理一条用户消息（agent 主入口）。
    被 main.py 的线程池调用（同步函数）。返回回复文本。

    失败时返回 AGENT_FAILURE 哨兵（不抛异常），由 graph.py 识别并走三分支兜底。
    """
    try:
        agent = _get_agent()
        # 调用编译好的 agent，传入用户消息
        result = agent.invoke({"messages": [{"role": "user", "content": content}]})
        # 取最后一条 assistant 消息作为回复
        messages = result.get("messages", [])
        if messages:
            # 找到最后一条非工具的消息
            for m in reversed(messages):
                if getattr(m, "type", "") != "tool" and getattr(m, "content", ""):
                    return m.content
        # agent 正常调用但没产出有效内容（比如完全没工具调用、空回复）
        logger.warning("agent 返回空内容，标记为失败: %s", content[:30])
        return AGENT_FAILURE
    except Exception as e:
        logger.error("agent 处理失败: %s", e, exc_info=True)
        return AGENT_FAILURE
