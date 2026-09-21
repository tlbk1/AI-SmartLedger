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
        # T050（FR-014）：当前账本已删除 → 明确提示用户（历史只读可查）
        if db.is_ledger_deleted(ledger_id):
            info = db.get_ledger_info(ledger_id) or {"name": "该账本"}
            return json.dumps(
                {
                    "ledger_deleted": True,
                    "ledger_name": info["name"],
                    "notice": f"⚠️ 账本「{info['name']}」已被删除，以下为历史账目（只读，不能再记账）。请在回复中明确提示用户。",
                    "records": rows,
                },
                ensure_ascii=False,
            )
        return json.dumps(rows, ensure_ascii=False)

    @tool
    def record_transactions(transactions: list) -> str:
        """一次性记录一笔或多笔账单（整批事务，全成全不记）。
        transactions 每项: type(expense/income), amount, category, note(可选), happened_at(可选)。
        确认信息才会记到账。用户未加入账本会提示先加。"""
        ledger_id = db.get_user_ledger_id(openid)
        if ledger_id is None:
            return "你还没有加入任何账本，请先创建或加入一个账本。"
        # T050（FR-014）：已删除账本只读——不能再记账
        if db.is_ledger_deleted(ledger_id):
            info = db.get_ledger_info(ledger_id) or {"name": "该账本"}
            return (
                f"不能记账：账本「{info['name']}」已被删除（只读，仅能查看历史账目）。"
                "请先切换到其他账本，或创建新账本。"
            )
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
        name 是账本名，如「我们家」「旅行账」。name 不能为空——
        用户没给名字时先反问（如「新账本叫什么名字？」），拿到名字再调用。"""
        ok, result = db.create_ledger(openid, name)
        if ok:
            return f"✅ 已创建账本「{name.strip()}」，邀请口令是 {result}，把这口令发给要加入的人即可。"
        return f"创建失败：{result}"

    @tool
    def join_ledger(invite_code: str) -> str:
        """凭邀请口令【申请】加入别人的账本（审批制：需 owner 同意后才成为成员）。
        invite_code 是对方创建账本时给你的口令。"""
        ok, msg, applied_lid = db.apply_join(openid, invite_code)
        if not ok:
            return f"申请失败：{msg}"
        # FR-038（评审问题8）：申请提交后尽力推送通知 owner（主通道）。
        # 推送失败 _do_push_customer 自己会入 undelivered；生成阶段异常也入队（FR-039）。
        # 定位用【本次申请的账本 id】——查「最新一条 pending」会在用户先后申请多本账时送错 owner。
        # 取账本名在 try 内（FR-039：通知"生成阶段"的异常也不许静默丢）——它跑在调用线程上，
        # 一旦抛出会带着"已提交的申请"一起逃出工具：通知既不发也不入队。
        try:
            info = db.get_ledger_info(applied_lid) if applied_lid else None
        except Exception:
            logging.getLogger(__name__).warning("取账本名失败，通知文案降级", exc_info=True)
            info = None
        ledger_name = (info or {}).get("name") or "该账本"
        import threading

        def _notify_owner():
            target = None
            try:
                from main import _do_push_customer  # 延迟导入，避免循环依赖
                if applied_lid is None:
                    return
                target = db.get_ledger_owner_openid(applied_lid)
                if target:
                    nick = db._ensure_nickname(openid)
                    _do_push_customer(target, (
                        f"📢 「{nick}」申请加入「{ledger_name}」（#{applied_lid}），"
                        f"回复「有哪些申请」查看；说「同意 {nick}」通过。"
                    ))
            except Exception:
                logging.getLogger(__name__).warning("申请通知生成失败（已入待补发）", exc_info=True)
                if target:
                    db.enqueue_undelivered(target, "有新的加入申请待处理，回复「有哪些申请」查看。")

        threading.Thread(target=_notify_owner, daemon=True).start()
        return f"✅ {msg}"

    @tool
    def my_join_status(show: str) -> str:
        """查看自己所有加入申请的状态（待审批/已通过）。show 填 'y' 即可。"""
        return db.get_my_join_status_text(openid)

    @tool
    def get_my_ledgers(show: str) -> str:
        """列出当前用户加入的所有账本（编号/名称/角色/成员名单/标记）。show 填 'y' 即可。"""
        ledgers = db.get_my_ledgers(openid)
        if not ledgers:
            return "你还没有加入任何账本。可创建（create_ledger）或凭口令加入（join_ledger）。"
        lines = []
        for l in ledgers:
            marks = []
            if l.get("is_current"):
                marks.append("当前")
            if l.get("is_default"):
                marks.append("默认")
            pv = db.ledger_member_preview(l["id"])
            members = "、".join(pv["names"])
            extra = f" 等 {pv['total']} 人" if pv["total"] > len(pv["names"]) else ""
            base = f"- #{l['id']}  {l['name']}（{l['role']}，成员：{members}{extra}"
            if l.get("is_deleted"):
                base += "，已删除·只读"
            base += "）"
            # 已删账本的口令已失效（_get_ledger_id_by_invite 过滤 deleted_at）——
            # 展示它会误导用户转发（评审：朋友申请时只会收到"口令不存在"）
            if l["role"] == "owner" and not l.get("is_deleted"):
                base += f" 口令 {l['invite_code']}"
            if marks:
                base += " ← " + " · ".join(marks)
            lines.append(base)
        return "你加入的账本（编号可用来切换或指定管理目标，如「#4」）：\n" + "\n".join(lines)

    @tool
    def switch_ledger(selector: str) -> str:
        """切换当前记账/查账账本。selector 是账本编号（如「#4」，编号见 get_my_ledgers）或账本名。
        名称对应多个账本时会返回候选列表——把候选原样转述给用户并等其回复编号，不要替用户选。"""
        ok, msg = db.switch_ledger(openid, selector)
        return f"✅ {msg}" if ok else msg

    # ── 管理操作（仅账本 owner / 管理员可用）──

    def _resolve_admin_ledger(ledger_name: str):
        """FR-023：管理操作指定账本——ledger_name 为空 → 当前账本；
        否则按编号/名称在用户已加入的账本内解析。返回 (ledger_id, 错误消息)。"""
        sel = (ledger_name or "").strip()
        if not sel:
            return db.get_user_ledger_id(openid), None
        return db.resolve_ledger_selector(openid, sel)

    @tool
    def list_pending(show: str, ledger_name: str = "") -> str:
        """[仅管理员] 列出待审批加入申请（含申请人昵称）。show 填 'y' 即可。
        ledger_name 是要查看的账本（编号或名称，不填为当前账本）。"""
        lid, err = _resolve_admin_ledger(ledger_name)
        if err:
            return err
        pending = db.list_pending_joins(openid, lid)
        if not pending:
            return "当前没有待审批的加入申请。"
        lines = [f"- {p['nickname']}" for p in pending]
        return f"待审批的加入申请（{len(pending)} 条）：\n" + "\n".join(lines) + \
               "\n如同意，说「同意 <昵称>」即可。"

    @tool
    def approve_join(applicant_nickname: str, ledger_name: str = "") -> str:
        """[仅管理员] 同意某人的加入申请。applicant_nickname 是申请人的昵称。
        ledger_name 是要操作的账本（编号或名称，不填为当前账本）。"""
        lid, err = _resolve_admin_ledger(ledger_name)
        if err:
            return f"操作失败：{err}"
        ok, msg = db.approve_join(openid, applicant_nickname, lid)
        if not ok:
            return f"操作失败：{msg}"
        # FR-016/评审修订：尽力推送通知申请人「已加入」；通知用【目标账本】定位，
        # 不再依赖 owner 的当前账本。失败/异常入 undelivered（见 _notify）。
        # 文案在推送与补发两条路径必须一致：补发路径若拿不到账本名就**不提名字**，
        # 不能写死占位符冒充真实账本名（曾把真实名字替换成「账本」两字）。
        # 取账本名在 try 内（FR-039：审批已提交后，取数异常不得带着通知一起逃出工具）。
        try:
            info = db.get_ledger_info(lid)
        except Exception:
            logging.getLogger(__name__).warning("取账本名失败，通知文案降级", exc_info=True)
            info = None
        display_name = (info or {}).get("name")
        notice = (
            f"「{display_name}」的管理员已同意你加入 🎉" if display_name
            else "你申请的账本管理员已同意你加入 🎉"
        )
        import threading

        def _notify():
            target = None
            try:
                from main import _do_push_customer  # 延迟导入，避免循环依赖
                target = db._get_openid_by_nickname_in_ledger(lid, applicant_nickname)
                if target:
                    _do_push_customer(target, notice)
            except Exception:
                # FR-039：生成通知异常时入待补发记录，不静默丢失（目标可得时）
                logging.getLogger(__name__).warning("审批通知生成失败（已入待补发）", exc_info=True)
                if target:
                    db.enqueue_undelivered(target, notice)

        threading.Thread(target=_notify, daemon=True).start()
        return f"✅ {msg}"

    @tool
    def reset_invite_code(show: str, ledger_name: str = "") -> str:
        """[仅管理员] 重置账本的邀请口令（旧口令失效，旧申请作废）。show 填 'y' 即可。
        ledger_name 是要操作的账本（编号或名称，不填为当前账本）。"""
        lid, err = _resolve_admin_ledger(ledger_name)
        if err:
            return f"操作失败：{err}"
        ok, result = db.reset_invite_code(openid, lid)
        if ok:
            return f"✅ 已重置口令，新口令是 {result}（旧口令已失效，旧申请已作废）"
        return f"操作失败：{result}"

    @tool
    def leave_ledger(show: str) -> str:
        """退出当前账本（仅普通成员可退出；管理员不能退出，只能删账本）。show 填 'y' 即可。"""
        ok, msg = db.leave_ledger(openid)
        return f"✅ {msg}" if ok else f"操作失败：{msg}"

    @tool
    def list_members(show: str) -> str:
        """列出当前账本的所有成员（昵称 + 角色），show 填 'y' 即可。"""
        members = db.list_ledger_members(openid)
        if not members:
            return "当前账本还没有成员。"
        lines = []
        for m in members:
            label = "管理员" if m["role"] == "owner" else "成员"
            # 只显示 nickname（默认或自设），绝不显示 openid（T008 / constitution 原则 I）
            name = m["nickname"] or "未命名"
            lines.append(f"- {name}（{label}）")
        return "账本成员：\n" + "\n".join(lines)

    @tool
    def set_nickname(nickname: str) -> str:
        """设置当前用户的昵称（全局唯一，被占用会被拒绝）。nickname 是用户昵称（≤20字，不含换行）。"""
        err = db.validate_nickname(openid, nickname)
        if err:
            return err if err != "昵称不能为空" else "昵称不能为空，请重新设置（比如「小王」）。"
        ok = db.set_nickname(openid, nickname)
        if not ok:
            return "该昵称已被占用，请换一个"  # 并发抢占兜底（校验与写入之间被抢）
        return f"✅ 已把你的昵称设为「{nickname.strip()}」"

    @tool
    def admin_remove_member(target_nickname: str, ledger_name: str = "") -> str:
        """[仅管理员] 从账本移除一个成员，按昵称查人。target_nickname 是被移除者的昵称。
        ledger_name 是要操作的账本（编号或名称，不填为当前账本）。
        只有账本创建者(owner)能执行。普通成员调用会被拒绝。"""
        lid, err = _resolve_admin_ledger(ledger_name)
        if err:
            return f"操作失败：{err}"
        ok, msg = db.admin_remove_member(openid, target_nickname, lid)
        return f"✅ {msg}" if ok else f"操作失败：{msg}"

    @tool
    def admin_rename_ledger(new_name: str, ledger_name: str = "") -> str:
        """[仅管理员] 修改账本名。new_name 是新账本名（不能为空，用户没给时先反问）。
        ledger_name 是要改名的账本（编号或名称，不填为当前账本）。只有账本 owner 能执行。"""
        lid, err = _resolve_admin_ledger(ledger_name)
        if err:
            return f"操作失败：{err}"
        ok, msg = db.admin_rename_ledger(openid, new_name, lid)
        return f"✅ {msg}" if ok else f"操作失败：{msg}"

    @tool
    def admin_delete_ledger(confirm: str, ledger_name: str = "") -> str:
        """[仅管理员] 删除账本。ledger_name 是要删除的账本（编号或名称，不填为当前账本）。
        只有账本 owner 能执行。confirm 填 'yes' 表示用户已确认。
        删除的是用户默认账本时必须先向用户复述后果并取得确认（工具会拦截未确认的执行）。"""
        lid, err = _resolve_admin_ledger(ledger_name)
        if err:
            return f"操作失败：{err}"
        if (confirm or "").strip().lower() != "yes":
            if db.is_default_ledger(openid, lid):
                # FR-014：删除默认账本必须先确认——告知后果（重建空账本、相当于清空）
                info = db.get_ledger_info(lid) or {"name": "该账本", "id": lid}
                return (
                    f"⚠️ 「{info['name']}」（#{info['id']}）是你的默认账本，删除后：\n"
                    f"· 会自动重建一个空的默认账本，日常记账从零开始（相当于清空）\n"
                    f"· 原账目不会真正删除，历史仍可在原账本查看\n"
                    f"确认删除吗？用户确认后请再次调用本工具并把 confirm 填 'yes'。"
                )
            return "请先向用户确认是否删除，确认后把 confirm 填 'yes' 再执行。"
        ok, msg = db.admin_delete_ledger(openid, lid)
        return f"✅ {msg}" if ok else f"操作失败：{msg}"

    @tool
    def ask_clarify(question: str) -> str:
        """当信息不足（缺金额、分类不明、口令没给全）时，向用户反问澄清。传入要问的话。"""
        return f"<澄清> {question}"

    return [
        query_transactions, record_transactions, create_ledger, join_ledger,
        my_join_status, get_my_ledgers, switch_ledger, list_members, set_nickname,
        list_pending, approve_join, reset_invite_code, leave_ledger,
        admin_remove_member, admin_rename_ledger, admin_delete_ledger, ask_clarify,
    ]


# ──────────────────────────── Agent 系统提示 ────────────────────────────

AGENT_SYSTEM_PROMPT = """\
你是一个微信记账机器人的核心 Agent。用户用自然语言告诉你收支情况，你负责处理成结果。

当前时间（Asia/Shanghai）：{now}

你有以下工具可用：
- record_transactions(transactions): 记一笔或多笔账（支出/收入），自动记到当前账本
- query_transactions(date_from, date_to, ...): 查询账单（只读），查当前账本所有成员的账（会显示是谁记的）
- create_ledger(name): 创建账本，返回邀请口令（创建者自动成为该账本管理员）
- join_ledger(invite_code): 凭口令【申请】加入别人的账本（审批制，需 owner 同意后才成为成员）
- my_join_status(): 查看自己所有加入申请的状态（待审批/已通过）
- get_my_ledgers(): 列出用户加入的所有账本（编号 #id、名称、角色、成员名单、当前/默认/已删标记）
- switch_ledger(selector): 切换当前账本到 selector（编号如「#4」，或账本名）。名称对应多个账本时会返回候选列表——**把候选原样转述给用户并等其回复编号，绝不替用户选**
- list_members(): 列出当前账本的成员（昵称+角色）
- set_nickname(nickname): 设置用户昵称（**全局唯一**，被占用/非法会返回拒绝原因，照实转述即可）
- list_pending(show, ledger_name): [仅管理员] 查看待审批加入申请；ledger_name 可指定账本（编号或名称，不填=当前账本）
- approve_join(applicant_nickname, ledger_name): [仅管理员] 同意某人加入
- reset_invite_code(show, ledger_name): [仅管理员] 重置账本口令（旧口令失效、旧申请作废）
- leave_ledger(): 退出当前账本（仅普通成员；管理员不能退出）
- admin_remove_member(target_nickname, ledger_name): [仅管理员] 按昵称移除账本成员（不能移除管理员）
- admin_rename_ledger(new_name, ledger_name): [仅管理员] 改账本名
- admin_delete_ledger(confirm, ledger_name): [仅管理员] 删除账本（所有相关用户的当前账本会自动回落到各自默认账本；**删除默认账本时工具会先返回确认话术，必须取得用户明确确认后才执行**）
- ask_clarify(question): 信息不足时反问用户

预设分类（只能用这些，拿不准归「其他」）：
- 支出（7类）：餐饮、交通、购物、居住、娱乐、医疗、其他
- 收入（3类）：工资、外快、其他

工作方式（重要）：
1. 用户说记账 → 用 record_transactions 记下，然后简单确认（回复时带上当前账本名）
2. 用户说查账 → 用 query_transactions 查数据。**账本是共享的**：你能看到账本内所有成员记的账，每笔会带记账人昵称。总结时如涉及"谁记的"，可以提一下记账人
3. 用户说「建账本」/「创建账本」→ 用 create_ledger，账本名从他的话里提取；**没给名字先反问**（「新账本叫什么名字？」），不要用空名调用
4. 用户说「加入账本 xxx」/收到口令 → 用 join_ledger **提交申请**（审批制）。告诉用户「已申请，等管理员同意」；**不要把申请说成"已加入"**
5. 用户说「我有哪些账本」→ 用 get_my_ledgers；用户问「我的申请状态」→ 用 my_join_status
6. 用户说「切换到账本 xxx」/「用 xxx 记账」→ 用 switch_ledger
7. 用户首次加入或想设置称呼 → 用 set_nickname 把昵称存起来，方便账本成员识别
8. 用户说「有哪些成员/人」→ 用 list_members
9. **管理操作（仅 owner）**：「有哪些申请」→ list_pending；「同意 xxx 加入」→ approve_join；「移除成员 xxx」→ admin_remove_member；「改账本名」→ admin_rename_ledger；「重置口令」→ reset_invite_code；「删账本」→ admin_delete_ledger。这些工具都支持 ledger_name 指定目标账本（不填=当前账本）——**用户点名了其他账本时必须把编号/名称传进 ledger_name**。**这类操作只有账本 owner 能做，普通成员调用会被工具拒绝（照实回复即可）**
10. 用户说「退出账本」→ 用 leave_ledger（普通成员可退；管理员会被拒绝，可改说删账本）
11. 信息不足（缺金额/分类不明/昵称/口令没给全）→ 用 ask_clarify 反问，直到信息够了再继续
12. 不确定用户在干嘛 → 友好打招呼，提示用法

规则：
- 金额默认单位「元」
- 时间默认「现在」，支持「昨天」「上周五」等相对时间
- 每步只调用最需要的工具，不要重复查询
- 记账/查账后，如已能取到当前账本名，在回复里可以提一句「（当前账本：xxx）」让用户知道在哪个账本
- **账本编号**：编号形如 #4，是账本的固定编号（get_my_ledgers 列表里展示，永不变化）。用户用编号指定时原样传给工具；编号不存在时照实报错，**不要猜、不要替换成其他账本**
- **重名不猜**：任何工具返回「回复编号选择」的候选列表时，把候选**原样转述**给用户并等待回复，绝不静默替用户挑一个；候选要现查现转述，不引用历史列表
- **管理操作必须明确账本**（移除/改名/删账本/重置口令/同意加入/查待审批）：用户点名了账本（编号或名称）就把 ledger_name 传给工具；用户有多个账本且未指明时，先问「你要操作哪个账本」（可提示回复『我的账本』查看编号）；只有一个账本时直接执行，不必反复确认
- **昵称全局唯一**：设置被占用/非法的昵称会被拒绝，把工具返回的原因转述给用户即可，不要猜测占用者是谁
- **审批制**：加入申请需 owner 同意。申请人未获同意前看不到账本任何数据，不要向其透露账本内容
- **已删除的账本**：删除后所有人（含 owner）的当前账本会自动回落到各自默认账本；用户显式切进去看历史是**合法只读**状态。① 列表/查账里出现「已删除」标记，必须明确告知用户；② 向已删除账本记账或做管理操作会被工具拒绝，引导用户切换或新建
- **删默认账本必须先确认**：admin_delete_ledger 返回确认话术时，把后果原样告诉用户（会重建空的默认账本、日常记账相当于清空重来），取得明确同意后才把 confirm 填 'yes' 再次调用执行；绝不跳过确认
- 当前用户身份已由系统绑定，你不需要也无法修改它；不要在回复里提及任何身份标识（如 openid）
- 最终回答要简洁、口语化，像一个贴心的记账助手
"""


# ──────────────────────────── 主入口 ────────────────────────────

# 失败哨兵：agent 崩溃/报错时返回这个特殊标记，上游 graph.py 识别到它就触发「三分支兜底」。
AGENT_FAILURE = "\x00__AGENT_FAILED__\x00"


def _pending_joins_hint(openid: str) -> str:
    """T049（US2/AC4）：owner 有待审批申请时，返回一段附加到 system prompt 的提示。

    这是「推送尽力而为」的**兜底保底**：微信 48h 推送窗口不可靠，
    所以 owner 任何一次对话都自动检查一次，让 agent 顺带提示。
    非 owner / 无待审批 → 返回空串（不影响 prompt）。
    """
    try:
        pending = db.list_pending_joins(openid)   # 内部已判定：非 owner 或无账本 → []
    except Exception as e:                         # 兜底：查询失败绝不能拖垮对话
        logger.warning("检查待审批失败（已忽略）: %s", e)
        return ""
    if not pending:
        return ""
    names = "、".join((p.get("nickname") or "（无昵称）") for p in pending)
    return (
        f"\n\n【系统提示 · 请务必执行】当前账本有 {len(pending)} 条待审批的加入申请"
        f"（申请人：{names}）。请在本次回复的末尾**顺带提一句**提醒 owner，"
        f"例如「（顺带一提：有 {len(pending)} 条加入申请待你同意，说『有哪些申请』可查看）」；"
        f"不要打断用户当前的话题，不要额外调用工具去查。"
    )


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
        # T049（US2/AC4 + 审批流程第3步 · 兜底保底）：owner 每次对话时自动检查待审批，
        # 附在 system prompt 里让 agent 顺带提示——不依赖 owner 主动查询（推送 48h 限制的保底）。
        prompt = AGENT_SYSTEM_PROMPT.format(now=now) + _pending_joins_hint(openid)
        # 每次按用户身份编译一个新的 agent（毫秒级，可接受）
        agent = create_react_agent(
            model=_get_model(),
            tools=make_tools(openid),          # openid 闭包注入，LLM 不可见
            prompt=prompt,
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
