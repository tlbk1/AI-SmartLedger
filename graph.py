"""
graph.py — 消息编排（已升级为 Loop Agent）

核心变化：
  从「固定路由（classify → record/query/chat）」升级为「Loop Agent（循环决策）」。
  真实意图 → 复用 agent.py 的 create_react_agent 循环。
  兜底 → 保留原固定路由（语义上不再依赖，但失败时可回退）。

设计依据：
- 需求文档 5 章：意图识别 → 记账/查账/闲聊
- 方案文档 docs/loop-agent-改造方案.md
"""

import logging
from datetime import datetime
from typing import Literal, Optional, TypedDict
from zoneinfo import ZoneInfo

import db
import agent as agent_mod

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


# ──────────────────────────── State（保留，兼容旧接口） ────────────────────────────

class GraphState(TypedDict, total=False):
    """LangGraph 在节点间传递的状态。保留以兼容旧调用。"""
    openid: str
    content: str
    now: str
    intent: Optional[str]
    reply: str
    txns: Optional[list]
    need_amount: Optional[bool]
    question: Optional[str]
    query_rows: Optional[list]


# ──────────────────────────── 主入口：处理一条消息 ────────────────────────────

def process_message(openid: str, content: str) -> str:
    """
    主入口：处理一条用户消息，返回回复文本。
    被 main.py 的线程池调用（同步函数）。

    现在改为优先走 agent 循环（agent.py），失败时回退到原固定路由。
    """
    now_str = datetime.now(SHANGHAI).isoformat()

    # 补发未送达消息（技术方案 4.3 undelivered 队列）
    undelivered = db.drain_undelivered(openid)
    prefix = ""
    if undelivered:
        prefix = "[补发] " + "\n[补发] ".join(undelivered) + "\n\n"

    # 优先走 Loop Agent
    reply = agent_mod.run_agent(openid, content)

    # 关键：识别「失败哨兵」→ 才走真正的三分支兜底
    # 只依赖异常(except)是不够的——agent 会把报错"吞"成一句人话，
    # 导致上层永远接不到失败信号。用哨兵标记明确区分"失败"和"正常回答"。
    if reply == agent_mod.AGENT_FAILURE:
        logger.warning("agent 返回失败哨兵，走三分支兜底: %s", content[:30])
        reply = _dispatch_fallback(
            {"openid": openid, "content": content, "now": now_str}
        )

    return prefix + reply


# ──────────────────────────── 兜底：原固定路由（agent 失败时用） ────────────────────────────

def _dispatch_fallback(state: GraphState) -> str:
    """agent 循环不可用时的简单 dispatch 兜底（保留原逻辑）。"""
    try:
        reply = _classify_and_route(state)
        return reply
    except Exception as e:
        logger.error("处理失败: %s", e, exc_info=True)
        return "出了点问题，请重试。"


def _classify_and_route(state: GraphState) -> str:
    """简单的分类 + 路由兜底（不引入 LangGraph 复杂编排）。"""
    import llm

    openid = state["openid"]
    content = state["content"]
    now = state.get("now", "")

    intent = llm.classify_intent(content, now)

    if intent == "record":
        result = llm.extract_transactions(content, now)
        if result.ok and result.txns:
            # 任务2：记账必须带 ledger_id——先定位用户当前账本，写孤儿数据的旧 insert_many 不再用
            ledger_id = db.get_user_ledger_id(openid)
            if ledger_id is None:
                # 有了默认账本后正常不会发生；防御性提示
                return "你还没有账本，请先创建一个。"
            user_id = db.get_or_create_user(openid)
            txns = [
                db.Transaction(
                    type=t.type, amount=t.amount, category=t.category,
                    note=t.note, happened_at=t.happened_at,
                )
                for t in result.txns
            ]
            if db.insert_many_for_ledger(ledger_id, user_id, txns):
                lines = []
                for t in result.txns:
                    emoji = "💰" if t.type == "income" else "🧾"
                    lines.append(f"{emoji} {t.category} {t.type} ¥{t.amount:.0f}")
                return f"✅ 已记 {len(txns)} 笔：\n" + "\n".join(lines)
            return "没记上，请重发一次。"
        elif result.need_amount:
            db.set_pending(openid, result.draft or {}, result.question)
            return result.question
        return "没太明白，能再说一次吗？"
    elif intent == "query":
        params = llm.extract_query_params(content, now)
        if params is None:
            return "查不到，请换个说法试试。"
        # 任务2：查账按账本隔离——用 openid 定位当前账本，旧的 query(不过滤账本)不再用
        ledger_id = db.get_user_ledger_id(openid)
        if ledger_id is None:
            return "你还没有账本，请先创建一个。"
        rows = db.query_by_ledger(
            ledger_id=ledger_id,
            date_from=params.date_from,
            date_to=params.date_to,
            category=params.category,
            type_filter=params.type_filter,
            limit=params.limit,
        )
        return llm.summarize_query_result(rows, content, now)
    else:
        return llm.chat_reply(content, now)
