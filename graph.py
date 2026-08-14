"""
graph.py — LangGraph 编排

把 db / llm / tools 串起来，按意图路由到三个分支。

设计依据：
- 需求文档第 5 章: 意图识别 → 记账/查账/闲聊
- 需求文档 14.3: 对话状态（pending）+ looks_like_amount 护栏
- 需求文档 14.6: 多笔事务 / 分类不确定归其他 / 闲聊灰区
- 技术方案第四节: pending 合并护栏，非金额消息清掉 pending 走正常流程
"""

import logging
import re
import time
from datetime import datetime
from typing import Literal, Optional, TypedDict
from zoneinfo import ZoneInfo

import db
import llm
from tools import query_transactions

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


# ──────────────────────────── State ────────────────────────────

class GraphState(TypedDict, total=False):
    """LangGraph 在节点间传递的状态。"""
    openid: str
    content: str
    now: str                      # ISO 时间字符串，注入给 LLM
    intent: Optional[str]         # record / query / chat
    reply: str                    # 最终回复文本
    # record 分支中间态
    txns: Optional[list]
    need_amount: Optional[bool]
    question: Optional[str]
    # query 分支中间态
    query_rows: Optional[list]


# ──────────────────────────── 护栏：判断像不像金额回答 ────────────────────────────

_AMOUNT_RE = re.compile(r"^[+-]?\d+(\.\d{1,2})?(元|块|毛|角|分)?$")

def looks_like_amount(text: str) -> bool:
    """
    判断用户回复像不像金额（技术方案 4.2 护栏）。
    "35" / "+35" / "35元" / "35.5" → True
    "查这个月餐饮" / "你好" → False
    """
    text = text.strip()
    return bool(_AMOUNT_RE.match(text))


# ──────────────────────────── 节点：分类 ────────────────────────────

def classify_node(state: GraphState) -> GraphState:
    """
    意图识别节点。
    但先检查 pending——如果用户在反问窗口内回复了金额，直接跳到 record。
    """
    openid = state["openid"]
    content = state["content"]

    # ── 先查 pending（技术方案 4.2 + 审查护栏）──
    pending = db.get_pending(openid)
    if pending is not None:
        if looks_like_amount(content):
            # 用户在回答反问的金额 → 合并 draft 直接走 record
            logger.info("pending 命中 + 金额回答，合并 draft")
            merged_content = f"{pending.draft.get('note', '')} {content}元"
            # 补全 draft
            pending.draft.setdefault("type", "expense")
            # 重建 content 让 LLM 重新抽取（带上下文）
            state["content"] = merged_content
            db.clear_pending(openid)
            state["intent"] = "record"
            return state
        else:
            # 用户没在回答反问，发了新消息 → 清掉 pending 走正常流程
            logger.info("pending 命中但非金额回答，清除 pending")
            db.clear_pending(openid)

    # ── 正常意图分类 ──
    intent = llm.classify_intent(content, state["now"])
    state["intent"] = intent
    logger.info("意图分类: %s -> %s", content[:20], intent)
    return state


# ──────────────────────────── 节点：记账 ────────────────────────────

def record_node(state: GraphState) -> GraphState:
    """记账节点：LLM 抽取 → 校验 → 入库 / 反问 / 报错。"""
    content = state["content"]
    now_str = state["now"]

    result = llm.extract_transactions(content, now_str)

    if result.ok and result.txns:
        # 成功抽取 → 入库（整批事务）
        txns = [
            db.Transaction(
                type=t.type,
                amount=t.amount,
                category=t.category,
                note=t.note,
                happened_at=t.happened_at,
            )
            for t in result.txns
        ]
        if db.insert_many(txns):
            # 格式化确认消息
            lines = []
            for t in result.txns:
                emoji = "💰" if t.type == "income" else "🧾"
                lines.append(f"{emoji} {t.category} {t.type} ¥{t.amount:.0f}")
            state["reply"] = f"✅ 已记 {len(txns)} 笔：\n" + "\n".join(lines)
        else:
            state["reply"] = "没记上，请重发一次。"
        return state

    elif result.need_amount:
        # 缺金额 → 存 pending + 反问
        db.set_pending(state["openid"], result.draft or {}, result.question)
        state["reply"] = result.question
        return state

    else:
        # 错误
        logger.warning("记账抽取失败: %s", result.error)
        state["reply"] = "没太明白，能再说一次吗？"
        return state


# ──────────────────────────── 节点：查账 ────────────────────────────

def query_node(state: GraphState) -> GraphState:
    """查账节点：LLM 产查询参数 → 参数化 SQL → LLM 总结。"""
    content = state["content"]
    now_str = state["now"]

    # 1. LLM 产出结构化查询参数
    params = llm.extract_query_params(content, now_str)
    if params is None:
        state["reply"] = "查不到，请换个说法试试。"
        return state

    # 2. 参数化查询（安全：LLM 无法注入）
    rows = query_transactions(params)
    state["query_rows"] = rows

    # 3. LLM 总结结果
    summary = llm.summarize_query_result(rows, content, now_str)
    state["reply"] = summary
    return state


# ──────────────────────────── 节点：闲聊 ────────────────────────────

def chat_node(state: GraphState) -> GraphState:
    """兜底闲聊。"""
    reply = llm.chat_reply(state["content"], state["now"])
    state["reply"] = reply
    return state


# ──────────────────────────── 路由函数 ────────────────────────────

def route_after_classify(state: GraphState) -> Literal["record", "query", "chat"]:
    """根据意图路由到对应分支。"""
    intent = state.get("intent", "chat")
    if intent == "record":
        return "record"
    elif intent == "query":
        return "query"
    return "chat"


# ──────────────────────────── 构建图 ────────────────────────────

def build_graph():
    """
    构建 LangGraph 状态图。
    structure:
        START → classify → [record | query | chat] → END
    """
    try:
        from langgraph.graph import StateGraph, START, END
    except ImportError:
        # langgraph 未安装时用简单 dispatch 兜底
        logger.warning("langgraph 未安装，降级为 dispatch 模式")
        return None

    g = StateGraph(GraphState)

    g.add_node("classify", classify_node)
    g.add_node("record", record_node)
    g.add_node("query", query_node)
    g.add_node("chat", chat_node)

    g.add_edge(START, "classify")
    g.add_conditional_edges(
        "classify",
        route_after_classify,
        {"record": "record", "query": "query", "chat": "chat"},
    )
    g.add_edge("record", END)
    g.add_edge("query", END)
    g.add_edge("chat", END)

    return g.compile()


# ──────────────────────────── 入口：处理一条消息 ────────────────────────────

# 全局编译好的 graph 实例（懒加载）
_graph_instance = None


def _get_graph():
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = build_graph()
    return _graph_instance


def process_message(openid: str, content: str) -> str:
    """
    主入口：处理一条用户消息，返回回复文本。
    被 main.py 的线程池调用（同步函数）。
    """
    now_str = datetime.now(SHANGHAI).isoformat()

    # 补发未送达消息（技术方案 4.3 undelivered 队列）
    undelivered = db.drain_undelivered(openid)
    prefix = ""
    if undelivered:
        prefix = "[补发] " + "\n[补发] ".join(undelivered) + "\n\n"

    state: GraphState = {
        "openid": openid,
        "content": content,
        "now": now_str,
    }

    graph = _get_graph()
    if graph is not None:
        # LangGraph 模式
        result = graph.invoke(state)
        reply = result.get("reply", "出了点问题，请重试。")
    else:
        # dispatch 降级模式（langgraph 未装时）
        reply = _dispatch_fallback(state)

    return prefix + reply


def _dispatch_fallback(state: GraphState) -> str:
    """langgraph 未安装时的简单 dispatch。"""
    try:
        state = classify_node(state)
        route = route_after_classify(state)
        if route == "record":
            state = record_node(state)
        elif route == "query":
            state = query_node(state)
        else:
            state = chat_node(state)
        return state.get("reply", "出了点问题，请重试。")
    except Exception as e:
        logger.error("处理失败: %s", e, exc_info=True)
        return "出了点问题，请重试。"
