"""builtin/ledgers — 账本域工具：创建 / 我的账本 / 切换。"""
from typing import Optional

from langchain_core.tools import tool

from ... import db


def format_ledger_list(ledgers: list, show_invite: bool = False) -> str:
    """账本列表的最终展示文本：一行一个、带人数；默认不显示口令。"""
    lines = ["你的账本："]
    for l in ledgers:
        marks = []
        if l.get("is_current"):
            marks.append("当前")
        marks.append(f"{l['member_count']} 人")
        if l.get("is_deleted"):
            marks.append("已删除·只读")
        if show_invite and l.get("role") == "owner" and not l.get("is_deleted"):
            marks.append(f"口令 {l['invite_code']}")
        lines.append(f"- #{l['id']} {l['name']}（{' · '.join(marks)}）")
    lines.append("要再建一个，说「建个账本 XXX」就行。")
    return "\n".join(lines)


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
    def get_my_ledgers(show: str = "y", show_invite: Optional[bool] = False) -> str:
        """列出当前用户加入的所有账本（编号/名称/当前/人数）。show 可不填。
        show_invite：用户明确问「口令/邀请码」时才传 True；默认 False 不展示口令。"""
        ledgers = db.get_my_ledgers(openid)
        if not ledgers:
            return "你还没有加入任何账本。说「建个账本 XXX」新建一个，或把管理员给你的口令发我，申请加入。"
        return format_ledger_list(ledgers, show_invite=show_invite)

    @tool
    def switch_ledger(selector: str) -> str:
        """切换当前记账/查账账本。selector 是账本编号（如「#4」，编号见 get_my_ledgers）或账本名。
        名称对应多个账本时会返回候选列表——把候选原样转述给用户并等其回复编号，不要替用户选。"""
        ok, msg = db.switch_ledger(openid, selector)
        return f"✅ {msg}" if ok else msg

    return [create_ledger, get_my_ledgers, switch_ledger]
