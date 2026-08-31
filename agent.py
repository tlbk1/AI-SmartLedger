"""
agent.py — Loop Agent（循环决策核心）

目标：把「固定路由」升级为「会循环思考、调用工具、观察结果、自主决定下一步」的 Agent。
共账能力：agent 通过工具感知 openid，记账/查账按账本隔离，支持口令制建账本/加入账本。

用 LangGraph 的 create_react_agent 搭标准 ReAct 循环：
    思考 → 调用工具 → 观察结果 → 再思考 → ... → 直到 agent 认为「任务完成」

设计依据：
- 需求文档 5 章：意图识别 → 记账/查账/闲聊
- 方案文档 docs/共账-agent设计方案.md
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

load_dotenv()  # 模块级就加载，避免被其他模块先 import 时 os.environ 为空

_model_obj = None


def _get_model():
    """langchain 的 ChatOpenAI（供 create_react_agent 用，需能 .bind_tools()）。"""
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
# 所有工具都接收 openid，用于定位用户所属账本（数据隔离可靠）。
# 用 tool 装饰器让 langchain 自动生成 schema。

from langchain_core.tools import tool


# ── 记账（带账本隔离）──

@tool
def query_transactions(
    openid: str,
    date_from: str,
    date_to: str,
    category: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 20,
) -> str:
    """查询账单记录（只读）。用 openid 定位用户所属账本，只查该账本的记录。
    date_from/date_to 必填，格式 YYYY-MM-DD；分类和类型可选。返回 JSON 字符串。"""
    ledger_id = db.get_user_ledger_id(openid)
    if ledger_id is None:
        return "你还没有加入任何账本，请先创建或加入一个账本。"
    rows = db.query_by_ledger(
        ledger_id=ledger_id,
        date_from=date_from,
        date_to=date_to,
        category=category,
        type_filter=type_filter,
        limit=limit,
    )
    import json
    return json.dumps(rows, ensure_ascii=False)


@tool
def record_transactions(openid: str, transactions: list) -> str:
    """一次性记录一笔或多笔账单（整批事务，全成全不记）。用 openid 定位所属账本。
    transactions 每项: type(expense/income), amount, category, note(可选), happened_at(可选)。
    成功返回确认，用户还未加入账本则提示先加。"""
    ledger_id = db.get_user_ledger_id(openid)
    if ledger_id is None:
        return "你还没有加入任何账本，请先创建或加入一个账本。"
    user_id = db.get_or_create_user(openid)
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
    ok = db.insert_many_for_ledger(ledger_id, user_id, txns)
    return f"✓ 已记 {len(txns)} 笔" if ok else "没记上，请重试"


# ── 账本管理（共账核心）──

@tool
def create_ledger(openid: str, name: str) -> str:
    """创建一个新的账本，创建者为账本 owner。返回邀请口令（别人凭口令加入）。
    name 是账本名，如「我们家」「旅行账」。"""
    ok, result = db.create_ledger(openid, name)
    if ok:
        return f"✅ 已创建账本「{name}」，邀请口令是 {result}，把这口令发给要加入的人即可。"
    return f"创建失败：{result}"


@tool
def join_ledger(openid: str, invite_code: str) -> str:
    """凭邀请口令加入别人的账本，成为 member。invite_code 是对方创建账本时给你的口令。"""
    ok, msg = db.join_ledger(openid, invite_code)
    if ok:
        return f"✅ {msg}，从此你们可以共同记账。"
    return f"加入失败：{msg}"


@tool
def get_my_ledgers(openid: str) -> str:
    """列出当前用户加入的所有账本及邀请口令。"""
    ledgers = db.get_my_ledgers(openid)
    if not ledgers:
        return "你还没有加入任何账本。可创建（create_ledger）或凭口令加入（join_ledger）。"
    lines = [f"- {l['name']}（口令 {l['invite_code']}，{l['role']}）" for l in ledgers]
    return "你加入的账本：\n" + "\n".join(lines)


# ── 管理操作（仅账本 owner / 管理员可用）──

@tool
def admin_remove_member(openid: str, target_openid: str) -> str:
    """[仅管理员] 从账本移除一个成员。target_openid 是被移除者的 openid。
    只有账本创建者(owner)能执行。普通成员调用会被拒绝。"""
    ok, msg = db.admin_remove_member(openid, target_openid)
    return f"✅ {msg}" if ok else f"操作失败：{msg}"


@tool
def admin_rename_ledger(openid: str, new_name: str) -> str:
    """[仅管理员] 修改账本名。new_name 是新账本名。只有账本 owner 能执行。"""
    ok, msg = db.admin_rename_ledger(openid, new_name)
    return f"✅ {msg}" if ok else f"操作失败：{msg}"


@tool
def admin_delete_ledger(openid: str) -> str:
    """[仅管理员] 删除当前账本。只有账本 owner 能执行。删除后成员关系清空，账目保留。"""
    ok, msg = db.admin_delete_ledger(openid)
    return f"✅ {msg}" if ok else f"操作失败：{msg}"


@tool
def ask_clarify(question: str) -> str:
    """当信息不足（缺金额、分类不明、口令没给全）时，向用户反问澄清。传入要问的话。"""
    return f"<澄清> {question}"


AGENT_TOOLS = [query_transactions, record_transactions, create_ledger, join_ledger, get_my_ledgers,
               admin_remove_member, admin_rename_ledger, admin_delete_ledger, ask_clarify]


# ──────────────────────────── Agent 系统提示 ────────────────────────────

AGENT_SYSTEM_PROMPT = """\
你是一个微信记账机器人的核心 Agent。用户用自然语言告诉你收支情况，你负责处理成结果。

当前时间（Asia/Shanghai）：{now}

你有以下工具可用：
- record_transactions(openid, transactions): 记一笔或多笔账（支出/收入），会自动记到用户所属账本
- query_transactions(openid, date_from, date_to, ...): 查询账单（只读），只查用户所属账本
- create_ledger(openid, name): 创建账本，返回邀请口令（创建者自动成为该账本管理员）
- join_ledger(openid, invite_code): 凭口令加入别人的账本（加入者为普通成员）
- get_my_ledgers(openid): 列出用户加入的所有账本
- admin_remove_member(openid, target_openid): [仅管理员] 移除账本成员
- admin_rename_ledger(openid, new_name): [仅管理员] 改账本名
- admin_delete_ledger(openid): [仅管理员] 删除账本
- ask_clarify(question): 信息不足时反问用户

预设分类（只能用这些，拿不准归「其他」）：
- 支出（7类）：餐饮、交通、购物、居住、娱乐、医疗、其他
- 收入（3类）：工资、外快、其他

工作方式（重要）：
1. 用户说记账 → 用 record_transactions 记下（openid 用当前用户），然后简单确认
2. 用户说查账 → 用 query_transactions 查数据（openid 用当前用户），看到结果后总结成自然语言
3. 用户说「建账本」/「创建账本」→ 用 create_ledger，账本名从他的话里提取
4. 用户说「加入账本 xxx」/收到口令 → 用 join_ledger
5. 用户说「我有哪些账本」→ 用 get_my_ledgers
6. 用户说「移除成员 / 删掉某人」→ 用 admin_remove_member；「改账本名」→ admin_rename_ledger；「删账本」→ admin_delete_ledger。**这类管理操作只有账本 owner 能做，普通成员调用会被工具拒绝（工具会返回拒绝信息，照实回复即可）**
7. 信息不足（缺金额/分类不明）→ 用 ask_clarify 反问，直到信息够了再继续
8. 不确定用户在干嘛 → 友好打招呼，提示用法

规则：
- 金额默认单位「元」
- 时间默认「现在」，支持「昨天」「上周五」等相对时间
- 每步只调用最需要的工具，不要重复查询
- openid 只作为工具参数传给工具代码，你在回复里不要提及它
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

# 失败哨兵：agent 崩溃/报错时返回这个特殊标记，上游 graph.py 识别到它就触发「三分支兜底」。
AGENT_FAILURE = "\x00__AGENT_FAILED__\x00"


def run_agent(openid: str, content: str) -> str:
    """
    处理一条用户消息（agent 主入口）。
    被 main.py 的线程池调用（同步函数）。返回回复文本。

    失败时返回 AGENT_FAILURE 哨兵（不抛异常），由 graph.py 识别并走三分支兜底。
    """
    try:
        agent = _get_agent()
        # 关键：把 openid 拼进用户消息，让 agent 一定能看到并有据可依填进工具参数。
        # （工具参数需要 openid 才能定位账本；放 system 里可能被 agent 忽略，放 user 内容更可靠）
        user_msg = f"[当前用户 openid = {openid}]\n{content}"
        result = agent.invoke({
            "messages": [
                {"role": "user", "content": user_msg},
            ]
        })
        messages = result.get("messages", [])
        if messages:
            for m in reversed(messages):
                if getattr(m, "type", "") != "tool" and getattr(m, "content", ""):
                    return m.content
        logger.warning("agent 返回空内容，标记为失败: %s", content[:30])
        return AGENT_FAILURE
    except Exception as e:
        logger.error("agent 处理失败: %s", e, exc_info=True)
        return AGENT_FAILURE
