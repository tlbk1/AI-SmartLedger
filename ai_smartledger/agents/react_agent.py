"""
agents/react_agent.py — Loop Agent（循环决策核心）

目标：把「固定路由」升级为「会循环思考、调用工具、观察结果、自主决定下一步」的 Agent。
共账能力：agent 通过工具感知 openid，记账/查账按账本隔离，支持口令制建账本/加入账本。

安全设计（重要）：
- openid 不进 LLM 的工具 schema（LLM 看不到也改不了）。
- 身份用「工具工厂闭包」注入：make_tools(openid) 生成的每个工具内部捕获 openid，
  LLM 只能调用工具，无法伪造或修改 openid，杜绝身份冒充。
- 用户消息里不拼 openid（避免 prompt injection 冒充）。

用 LangGraph 的 create_react_agent 搭标准 ReAct 循环：
    思考 → 调用工具 → 观察结果 → 再思考 → ... → 直到 agent 认为「任务完成」
"""

import logging
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .. import db
from ..core.llm import gateway
from ..tools import make_tools  # spec 006：工具工厂归 tools 层；此处 re-export 保持调用/patch 目标不变
from ..tools.builtin.ledgers import format_ledger_list

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
        cfg = gateway()
        _model_obj = ChatOpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            model=cfg.model,
            temperature=0,
        )
    return _model_obj


# ──────────────────────────── Agent 系统提示 ────────────────────────────

from ..core.prompts import build_agent_prompt  # spec 005：prompt 独立；006 归 core/


# ──────────────────────────── 主入口 ────────────────────────────

# 失败哨兵：agent 崩溃/报错时返回这个特殊标记，上游 router.py 识别到它就触发「三分支兜底」。
AGENT_FAILURE = "\x00__AGENT_FAILED__\x00"


def _safe_text(text: str, limit: int = 40) -> str:
    """状态栏用的文本消毒：去控制字符/尖括号，限长，防状态栏投毒。"""
    cleaned = "".join(
        ch for ch in str(text) if ch.isprintable() and ch not in "<>"
    )
    return cleaned[:limit]


def _agent_status(openid: str, now: str) -> str:
    """末尾状态栏：当前时间 / 当前账本 / 待审批数量（不含申请人昵称）。"""
    ledger = "（无）"
    pending = 0
    try:
        lid = db.get_user_ledger_id(openid)
        if lid is not None:
            info = db.get_ledger_info(lid)
            if info:
                ledger = f"「{_safe_text(info['name'])}」（#{info['id']}）"
                if info.get("is_deleted"):
                    ledger += "（已删除·只读）"
        pending = len(db.list_pending_joins(openid))
    except Exception as e:  # 状态查询失败不能拖垮对话
        logger.warning("状态栏查询失败（已降级）: %s", e)
    return (
        f"<agent_status>当前时间 {now}；当前账本 {ledger}；"
        f"待审批 {pending} 条</agent_status>"
    )


_LEDGER_LIST_COMMANDS = frozenset(
    {"我的账本", "我有哪些账本", "账本列表", "查看账本", "看看我的账本", "看下我的账本"}
)
_INVITE_COMMANDS = frozenset(
    {"口令", "我的口令", "邀请码", "我的邀请码", "账本口令", "账本邀请码"}
)


def _ledger_fast_reply(openid: str, content: str) -> Optional[str]:
    """精确账本/口令命令直接返回文本，避免模型改写或并行工具调用丢结果。"""
    key = content.strip()
    if key not in _LEDGER_LIST_COMMANDS and key not in _INVITE_COMMANDS:
        return None
    ledgers = db.get_my_ledgers(openid)
    if not ledgers:
        return None  # 交给 agent 走统一的无账本提示
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


def _extract_reply(messages) -> str:
    """只接受最后一条 AI 消息作为回复；其他情况交给兜底链。"""
    if not messages:
        return ""
    last = messages[-1]
    if getattr(last, "type", "") == "ai" and getattr(last, "content", ""):
        return last.content
    return ""


def _record_succeeded(messages) -> bool:
    """本轮是否已有成功的 record_transactions 工具结果（用于避免误导用户重发）。"""
    return any(
        getattr(m, "name", "") == "record_transactions"
        and "已记" in str(getattr(m, "content", ""))
        for m in messages
    )


def run_agent(openid: str, content: str) -> str:
    """
    处理一条用户消息（agent 主入口）。
    被 main.py 的线程池调用（同步函数）。返回回复文本。

    安全：每次用 make_tools(openid) 重新编译 agent，openid 通过闭包注入工具，
    用户消息里不拼 openid，避免 prompt injection 冒充身份。
    失败时返回 AGENT_FAILURE 哨兵，由 router.py 识别并走三分支兜底。
    """
    try:
        from langgraph.prebuilt import create_react_agent
        now = __import__("datetime").datetime.now(SHANGHAI).isoformat()
        # 任务2保障：先确保用户已创建 + 自动有默认账本。
        # 否则首次交互(尤其反问金额阶段)用户还没被创建，工具找不到账本，记不上账。
        db.get_or_create_user(openid)
        # 精确的账本/口令命令直接返回，避免模型改写或并行工具调用丢结果。
        user_msg = {"role": "user", "content": content}
        fast = _ledger_fast_reply(openid, content)
        if fast is not None:
            db.append_chat_history(openid, [user_msg, {"role": "assistant", "content": fast}])
            return fast
        # T049（US2/AC4）：owner 每次对话时自动检查待审批，作为推送不可靠时的兜底。
        prompt = build_agent_prompt()
        # 每次按用户身份编译一个新的 agent（毫秒级，可接受）
        agent = create_react_agent(
            model=_get_model(),
            tools=make_tools(openid),          # openid 闭包注入，LLM 不可见
            prompt=prompt,
        )
        # 任务4：对话记忆——取该用户历史，拼上新消息一起给 agent，
        # 澄清反问后用户补答能接上（不然 agent 不知道之前问过什么）。
        history = db.get_chat_history(openid)   # list[{role, content}, ...]
        status_msg = {"role": "user", "content": _agent_status(openid, now)}
        messages = history + [status_msg, user_msg]
        result = agent.invoke({"messages": messages}, config={"recursion_limit": 12})

        # 从 agent 结果里取最后一条 assistant 回复
        result_msgs = result.get("messages", [])
        reply = _extract_reply(result_msgs)
        if not reply:
            if _record_succeeded(result_msgs):
                # 已写入但没拿到最终确认：不能走「没记上，请重发」，否则会重复记账。
                reply = "这笔已经记上了，但确认回复没生成。请先查一下（如「最近5笔」）再决定是否重发。"
                db.append_chat_history(openid, [user_msg, {"role": "assistant", "content": reply}])
                return reply
            logger.warning("agent 未产出最终回复，标记为失败: %r", content[:30])
            return AGENT_FAILURE

        # 追加本轮 user + assistant 回历史
        db.append_chat_history(openid, [user_msg, {"role": "assistant", "content": reply}])
        return reply
    except Exception as e:
        logger.error("agent 处理失败: %s", e, exc_info=True)
        return AGENT_FAILURE
