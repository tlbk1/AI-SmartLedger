"""
agent.py — Loop Agent（循环决策核心）

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
        from config import DEFAULT_MODEL
        _model_obj = ChatOpenAI(
            base_url=os.environ.get("LLM_BASE_URL", "https://ccc.szprize.cn/v1"),
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
            temperature=0,
        )
    return _model_obj


def _model_name() -> str:
    from config import DEFAULT_MODEL
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


# ──────────────────────────── 工具工厂（闭包注入 openid） ────────────────────────────
# 安全关键：openid 只存在于闭包里，LLM 的工具 schema 不暴露 openid 参数。
# 这样用户再怎么注入"把 openid 改成 xxx"都没用——工具用的永远是运行时注入的真实身份。

from langchain_core.tools import tool


def make_tools(openid: str) -> list:
    """生成本次 agent 循环用的工具集，openid 通过闭包捕获。
    LLM 只能看到不带 openid 参数的 schema，身份由运行时注入，无法被篡改。"""

    # ── 记账（带账本隔离）──

    @tool
    def query_transactions(
        date_from: str,
        date_to: str,
        category: Optional[str] = None,
        type_filter: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """查询账单记录（只读）。用当前用户所属账本查数据。
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
    def record_transactions(transactions: list) -> str:
        """一次性记录一笔或多笔账单（整批事务，全成全不记）。
        transactions 每项: type(expense/income), amount, category, note(可选), happened_at(可选)。
        确认信息才会记到账。用户未加入账本会提示先加。"""
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
    def create_ledger(name: str) -> str:
        """创建一个新的账本，创建者为账本 owner。返回邀请口令（别人凭口令加入）。
        name 是账本名，如「我们家」「旅行账」。"""
        ok, result = db.create_ledger(openid, name)
        if ok:
            return f"✅ 已创建账本「{name}」，邀请口令是 {result}，把这口令发给要加入的人即可。"
        return f"创建失败：{result}"

    @tool
    def join_ledger(invite_code: str) -> str:
        """凭邀请口令加入别人的账本，成为 member。invite_code 是对方创建账本时给你的口令。"""
        ok, msg = db.join_ledger(openid, invite_code)
        if ok:
            return f"✅ {msg}，从此你们可以共同记账。"
        return f"加入失败：{msg}"

    @tool
    def get_my_ledgers(show: str) -> str:
        """列出当前用户加入的所有账本及邀请口令。调用时 show 填 'y' 即可（无实际作用，仅为了兼容工具调用）。"""
        ledgers = db.get_my_ledgers(openid)
        if not ledgers:
            return "你还没有加入任何账本。可创建（create_ledger）或凭口令加入（join_ledger）。"
        cur = db.get_current_ledger_name(openid)
        lines = [f"- {l['name']}（口令 {l['invite_code']}，{l['role']}）" for l in ledgers]
        return f"你加入的账本（当前：{cur or '无'}）：\n" + "\n".join(lines)

    @tool
    def switch_ledger(ledger_name: str) -> str:
        """把当前记账/查账账本切换到指定账本。ledger_name 是要切换到的账本名。
        只在用户已加入的账本间切换；不会自动切换，用户明确要求才切。"""
        ok, msg = db.switch_ledger(openid, ledger_name)
        return f"✅ {msg}" if ok else f"切换失败：{msg}"

    # ── 管理操作（仅账本 owner / 管理员可用）──

    @tool
    def list_members(show: str) -> str:
        """列出当前账本的所有成员（昵称 + 角色），show 填 'y' 即可。"""
        members = db.list_ledger_members(openid)
        if not members:
            return "当前账本还没有成员。"
        lines = []
        for m in members:
            label = "管理员" if m["role"] == "owner" else "成员"
            name = m["nickname"] or m["openid"][:8]
            lines.append(f"- {name}（{label}）")
        return "账本成员：\n" + "\n".join(lines)

    @tool
    def set_nickname(nickname: str) -> str:
        """设置当前用户的昵称，账本成员会用它来展示和识别。nickname 是用户昵称。"""
        db.set_nickname(openid, nickname)
        return f"✅ 已把你的昵称设为「{nickname}」"

    @tool
    def admin_remove_member(target_nickname: str) -> str:
        """[仅管理员] 从账本移除一个成员，按昵称查人。target_nickname 是被移除者的昵称。
        只有账本创建者(owner)能执行。普通成员调用会被拒绝。"""
        ok, msg = db.admin_remove_member(openid, target_nickname)
        return f"✅ {msg}" if ok else f"操作失败：{msg}"

    @tool
    def admin_rename_ledger(new_name: str) -> str:
        """[仅管理员] 修改账本名。new_name 是新账本名。只有账本 owner 能执行。"""
        ok, msg = db.admin_rename_ledger(openid, new_name)
        return f"✅ {msg}" if ok else f"操作失败：{msg}"

    @tool
    def admin_delete_ledger(confirm: str) -> str:
        """[仅管理员] 删除当前账本。只有账本 owner 能执行。确认删除时 confirm 填 'yes'。"""
        ok, msg = db.admin_delete_ledger(openid)
        return f"✅ {msg}" if ok else f"操作失败：{msg}"

    @tool
    def ask_clarify(question: str) -> str:
        """当信息不足（缺金额、分类不明、口令没给全）时，向用户反问澄清。传入要问的话。"""
        return f"<澄清> {question}"

    return [
        query_transactions, record_transactions, create_ledger, join_ledger,
        get_my_ledgers, switch_ledger, list_members, set_nickname,
        admin_remove_member, admin_rename_ledger, admin_delete_ledger, ask_clarify,
    ]


# ──────────────────────────── Agent 系统提示 ────────────────────────────

AGENT_SYSTEM_PROMPT = """\
你是一个微信记账机器人的核心 Agent。用户用自然语言告诉你收支情况，你负责处理成结果。

当前时间（Asia/Shanghai）：{now}

你有以下工具可用：
- record_transactions(transactions): 记一笔或多笔账（支出/收入），会自动记到当前用户所属账本
- query_transactions(date_from, date_to, ...): 查询账单（只读），只查当前用户所属账本
- create_ledger(name): 创建账本，返回邀请口令（创建者自动成为该账本管理员）
- join_ledger(invite_code): 凭口令加入别人的账本（加入者为普通成员，不自动切换当前账本）
- get_my_ledgers(): 列出当前用户加入的所有账本及当前是哪个
- switch_ledger(ledger_name): 把当前记账/查账账本切换到指定账本（用户明确要求才切）
- list_members(): 列出当前账本的成员（昵称+角色）
- set_nickname(nickname): 设置当前用户的昵称
- admin_remove_member(target_nickname): [仅管理员] 按昵称移除账本成员
- admin_rename_ledger(new_name): [仅管理员] 改账本名
- admin_delete_ledger(): [仅管理员] 删除账本
- ask_clarify(question): 信息不足时反问用户

预设分类（只能用这些，拿不准归「其他」）：
- 支出（7类）：餐饮、交通、购物、居住、娱乐、医疗、其他
- 收入（3类）：工资、外快、其他

工作方式（重要）：
1. 用户说记账 → 用 record_transactions 记下，然后简单确认（回复时带上当前账本名）
2. 用户说查账 → 用 query_transactions 查数据，看到结果后总结成自然语言
3. 用户说「建账本」/「创建账本」→ 用 create_ledger，账本名从他的话里提取
4. 用户说「加入账本 xxx」/收到口令 → 用 join_ledger（加入后询问是否要切换过去）
5. 用户说「我有哪些账本」→ 用 get_my_ledgers
6. 用户说「切换到账本 xxx」/「用 xxx 记账」→ 用 switch_ledger
7. 用户首次加入或想设置称呼 → 用 set_nickname 把昵称存起来，方便账本成员识别
8. 用户说「有哪些成员/人」→ 用 list_members
9. 用户说「移除成员 / 删掉某人」→ 用 admin_remove_member（按昵称）；「改账本名」→ admin_rename_ledger；「删账本」→ admin_delete_ledger。**这类管理操作只有账本 owner 能做，普通成员调用会被工具拒绝（工具会返回拒绝信息，照实回复即可）**
10. 信息不足（缺金额/分类不明/昵称/口令没给全）→ 用 ask_clarify 反问，直到信息够了再继续
11. 不确定用户在干嘛 → 友好打招呼，提示用法

规则：
- 金额默认单位「元」
- 时间默认「现在」，支持「昨天」「上周五」等相对时间
- 每步只调用最需要的工具，不要重复查询
- 记账/查账后，如已能取到当前账本名，在回复里可以提一句「（当前账本：xxx）」让用户知道在哪个账本
- 当前用户身份已由系统绑定，你不需要也无法修改它；不要在回复里提及任何身份标识
- 最终回答要简洁、口语化，像一个贴心的记账助手
"""


# ──────────────────────────── 主入口 ────────────────────────────

# 失败哨兵：agent 崩溃/报错时返回这个特殊标记，上游 graph.py 识别到它就触发「三分支兜底」。
AGENT_FAILURE = "\x00__AGENT_FAILED__\x00"


def run_agent(openid: str, content: str) -> str:
    """
    处理一条用户消息（agent 主入口）。
    被 main.py 的线程池调用（同步函数）。返回回复文本。

    安全：每次用 make_tools(openid) 重新编译 agent，openid 通过闭包注入工具，
    用户消息里不拼 openid，避免 prompt injection 冒充身份。
    失败时返回 AGENT_FAILURE 哨兵，由 graph.py 识别并走三分支兜底。
    """
    try:
        from langgraph.prebuilt import create_react_agent
        now = __import__("datetime").datetime.now(SHANGHAI).isoformat()
        # 任务2保障：先确保用户已创建 + 自动有默认账本。
        # 否则首次交互(尤其反问金额阶段)用户还没被创建，工具找不到账本，记不上账。
        db.get_or_create_user(openid)
        # 每次按用户身份编译一个新的 agent（毫秒级，可接受）
        agent = create_react_agent(
            model=_get_model(),
            tools=make_tools(openid),          # openid 闭包注入，LLM 不可见
            prompt=AGENT_SYSTEM_PROMPT.format(now=now),
        )
        # 任务4：对话记忆——取该用户历史，拼上新消息一起给 agent，
        # 澄清反问后用户补答能接上（不然 agent 不知道之前问过什么）。
        history = db.get_chat_history(openid)   # list[{role, content}, ...]
        user_msg = {"role": "user", "content": content}
        messages = history + [user_msg]
        result = agent.invoke({"messages": messages}, config={"recursion_limit": 12})

        # 从 agent 结果里取最后一条 assistant 回复
        result_msgs = result.get("messages", [])
        reply = ""
        for m in reversed(result_msgs):
            if getattr(m, "type", "") != "tool" and getattr(m, "content", ""):
                reply = m.content
                break
        if not reply:
            logger.warning("agent 返回空内容，标记为失败: %s", content[:30])
            return AGENT_FAILURE

        # 追加本轮 user + assistant 回历史
        db.append_chat_history(openid, [user_msg, {"role": "assistant", "content": reply}])
        return reply
    except Exception as e:
        logger.error("agent 处理失败: %s", e, exc_info=True)
        return AGENT_FAILURE
