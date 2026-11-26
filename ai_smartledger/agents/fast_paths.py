"""agents/fast_paths.py — 精确命令 + 单次结构化调度。

- 静态问候/帮助、账本/口令：只做整句完全相等，不做正则/包含匹配。
- 其余消息：一次 LLM 调度（intent + 参数 + 回复）；代码只做 switch。
- 调度失败/不确定 → 回退 ReAct Agent。
"""
from datetime import datetime
from typing import Optional

from .. import db
from ..core.prompts import GREETING_TEXT, HELP_TEXT
from ..tools import make_tools
from ..tools.builtin.ledgers import format_ledger_list
from . import fallback_agent

_STATIC_REPLIES = {
    "你好": GREETING_TEXT,
    "嗨": GREETING_TEXT,
    "在吗": GREETING_TEXT,
    "帮助": HELP_TEXT,
    "怎么用": HELP_TEXT,
    "有什么功能": HELP_TEXT,
}

_LEDGER_LIST_COMMANDS = frozenset(
    {"我的账本", "我有哪些账本", "账本列表", "查看账本", "看看我的账本", "看下我的账本"}
)
_INVITE_COMMANDS = frozenset(
    {"口令", "我的口令", "邀请码", "我的邀请码", "账本口令", "账本邀请码"}
)

_CATEGORIES = {"餐饮", "交通", "购物", "居住", "娱乐", "医疗", "工资", "外快", "其他"}
_INCOME_CATS = {"工资", "外快"}
_EXPENSE_CATS = {"餐饮", "交通", "购物", "居住", "娱乐", "医疗", "其他"}


def ledger_fast_reply(openid: str, content: str) -> Optional[str]:
    """整句精确匹配账本/口令命令；不匹配返回 None。"""
    key = content.strip()
    if key not in _LEDGER_LIST_COMMANDS and key not in _INVITE_COMMANDS:
        return None
    ledgers = db.get_my_ledgers(openid)
    if not ledgers:
        return None
    reply = format_ledger_list(ledgers, show_invite=key in _INVITE_COMMANDS)
    try:
        pending = db.list_pending_joins(openid)
    except Exception:
        pending = []
    if pending:
        reply += (
            f"\n（顺带一提：有 {len(pending)} 条加入申请待你同意，"
            "说「有哪些申请」可查看）"
        )
    return reply


def _with_ledger(reply: str, ledger_id: Optional[int]) -> str:
    if ledger_id is None:
        return reply
    info = db.get_ledger_info(ledger_id)
    if not info:
        return reply
    suffix = f"（当前账本：{info['name']}"
    if info.get("is_deleted"):
        suffix += "·已删除只读"
    return reply + suffix + "）"


def _valid_date(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _fmt_date(value: datetime) -> str:
    return value.strftime("%m/%d")


def _record_reply(openid: str, data: dict) -> Optional[str]:
    txns = data.get("transactions")
    if not isinstance(txns, list) or not txns:
        return None
    clean = []
    for t in txns:
        if not isinstance(t, dict) or "amount" not in t:
            return None
        clean.append(
            {
                "type": t.get("type", "expense"),
                "amount": t["amount"],
                "category": t.get("category", "其他"),
                "note": t.get("note", ""),
                "happened_at": t.get("happened_at", ""),
            }
        )

    tools = {t.name: t for t in make_tools(openid)}
    out = str(tools["record_transactions"].invoke({"transactions": clean}))
    if not out.startswith("✓ 已记"):
        return out  # 校验失败/已删账本等：不写库，也不再走 Agent

    parts = []
    for t in clean:
        note = str(t.get("note") or "").strip() or str(t["category"])
        sign = "收入" if t.get("type") == "income" else "支出"
        parts.append(f"{note} {t['amount']:g} 元（{t['category']}·{sign}）")
    reply = "记好了：" + "、".join(parts) + "。"
    return _with_ledger(reply, db.get_user_ledger_id(openid))


def _query_reply(openid: str, data: dict) -> Optional[str]:
    q = data.get("query")
    if not isinstance(q, dict):
        return None
    start = _valid_date(q.get("date_from", ""))
    end = _valid_date(q.get("date_to", ""))
    if start is None or end is None or start > end:
        return None

    category = q.get("category")
    if category not in _CATEGORIES:
        category = None
    type_filter = q.get("type")
    if type_filter not in ("expense", "income"):
        type_filter = None
    if category in _INCOME_CATS and type_filter is None:
        type_filter = "income"
    elif category in _EXPENSE_CATS and type_filter is None:
        type_filter = "expense"

    detail = bool(q.get("detail"))
    order_by = q.get("order_by") if q.get("order_by") in ("recent", "top_amount") else "recent"
    try:
        limit = max(1, min(int(q.get("limit") or 5), 50))
    except (TypeError, ValueError):
        limit = 5
    label = str(q.get("label") or "查询区间")

    ledger_id = db.get_user_ledger_id(openid)
    if ledger_id is None:
        return None

    date_from = start.strftime("%Y-%m-%d")
    date_to = end.strftime("%Y-%m-%d")
    date_range = _fmt_date(start)
    if start != end:
        date_range += f"–{_fmt_date(end)}"

    if detail:
        rows = db.query_by_ledger(
            ledger_id,
            date_from,
            date_to,
            category=category,
            type_filter=type_filter,
            limit=limit,
            order_by=order_by,
        )
        if not rows:
            reply = f"{label}没有记录。"
        else:
            lines = []
            for r in rows:
                day = (r.get("happened_at") or "")[:10]
                sign = "+" if r.get("type") == "income" else "-"
                note = (r.get("note") or "").strip() or r.get("category", "其他")
                lines.append(f"· {day} {note} {sign}¥{float(r['amount']):g}")
            if label == "最近":
                header = f"最近 {len(rows)} 笔："
            else:
                header = f"{label}明细（{date_range}）："
            reply = header + "\n" + "\n".join(lines)
        return _with_ledger(reply, ledger_id)

    totals = db.sum_by_ledger(
        ledger_id,
        date_from,
        date_to,
        category=category,
        type_filter=type_filter,
    )
    exp, inc = totals["expense"], totals["income"]
    cat = category or ""
    date_label = f"{label}（{date_range}）"
    if type_filter == "expense":
        reply = (
            f"{date_label}{cat}没有支出记录。"
            if exp["count"] == 0
            else f"{date_label}{cat}支出 ¥{exp['total']:g}（{exp['count']} 笔）。"
        )
    elif type_filter == "income":
        reply = (
            f"{date_label}{cat}没有收入记录。"
            if inc["count"] == 0
            else f"{date_label}{cat}收入 ¥{inc['total']:g}（{inc['count']} 笔）。"
        )
    elif exp["count"] == 0 and inc["count"] == 0:
        reply = f"{date_label}没有记录。"
    else:
        reply = (
            f"{date_label}支出 ¥{exp['total']:g}（{exp['count']} 笔），"
            f"收入 ¥{inc['total']:g}（{inc['count']} 笔）。"
        )
    return _with_ledger(reply, ledger_id)


def fast_reply(openid: str, content: str, status: str) -> Optional[str]:
    """精确命令 → 0 LLM；其余一次结构化调度；不确定/失败 → None（Agent）。"""
    static = _STATIC_REPLIES.get(content.strip())
    if static is not None:
        return static

    reply = ledger_fast_reply(openid, content)
    if reply is not None:
        return reply

    data = fallback_agent.dispatch_message(content, status)
    intent = data.get("intent")
    if intent == "record":
        return _record_reply(openid, data)
    if intent == "query":
        return _query_reply(openid, data)
    if intent in ("chat", "clarify"):
        reply = str(data.get("reply") or "").strip()
        return reply or None
    return None
