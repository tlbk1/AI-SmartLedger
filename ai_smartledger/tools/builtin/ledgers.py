"""builtin/ledgers — 账本域工具：创建 / 我的账本 / 切换。"""
from langchain_core.tools import tool

from ... import db


def build(openid: str) -> list:
    """生成账本域工具（openid 闭包注入）。"""

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

    return [create_ledger, get_my_ledgers, switch_ledger]
