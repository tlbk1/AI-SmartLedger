"""builtin/members — 成员域工具：加入申请、审批、成员列表、昵称、退出。"""
import logging

from langchain_core.tools import tool

from ... import db
from .common import resolve_admin_ledger


def build(openid: str) -> list:
    """生成成员域工具（openid 闭包注入）。"""

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
                from ...main import _do_push_customer  # 延迟导入，避免循环依赖（T3 notify 解环）
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
    def list_pending(show: str, ledger_name: str = "") -> str:
        """[仅管理员] 列出待审批加入申请（含申请人昵称）。show 填 'y' 即可。
        ledger_name 是要查看的账本（编号或名称，不填为当前账本）。"""
        lid, err = resolve_admin_ledger(openid, ledger_name)
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
        lid, err = resolve_admin_ledger(openid, ledger_name)
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
                from ...main import _do_push_customer  # 延迟导入，避免循环依赖（T3 notify 解环）
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

    return [
        join_ledger, my_join_status, list_pending, approve_join,
        leave_ledger, list_members, set_nickname,
    ]
