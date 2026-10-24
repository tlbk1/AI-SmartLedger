"""builtin/admin — 管理域工具（仅账本 owner / 管理员可用）。"""
from langchain_core.tools import tool

from ... import db
from .common import resolve_admin_ledger


def build(openid: str) -> list:
    """生成管理域工具（openid 闭包注入）。"""

    @tool
    def reset_invite_code(show: str, ledger_name: str = "") -> str:
        """[仅管理员] 重置账本的邀请口令（旧口令失效，旧申请作废）。show 填 'y' 即可。
        ledger_name 是要操作的账本（编号或名称，不填为当前账本）。"""
        lid, err = resolve_admin_ledger(openid, ledger_name)
        if err:
            return f"操作失败：{err}"
        ok, result = db.reset_invite_code(openid, lid)
        if ok:
            return f"✅ 已重置口令，新口令是 {result}（旧口令已失效，旧申请已作废）"
        return f"操作失败：{result}"

    @tool
    def admin_remove_member(target_nickname: str, ledger_name: str = "") -> str:
        """[仅管理员] 从账本移除一个成员，按昵称查人。target_nickname 是被移除者的昵称。
        ledger_name 是要操作的账本（编号或名称，不填为当前账本）。
        只有账本创建者(owner)能执行。普通成员调用会被拒绝。"""
        lid, err = resolve_admin_ledger(openid, ledger_name)
        if err:
            return f"操作失败：{err}"
        ok, msg = db.admin_remove_member(openid, target_nickname, lid)
        return f"✅ {msg}" if ok else f"操作失败：{msg}"

    @tool
    def admin_rename_ledger(new_name: str, ledger_name: str = "") -> str:
        """[仅管理员] 修改账本名。new_name 是新账本名（不能为空，用户没给时先反问）。
        ledger_name 是要改名的账本（编号或名称，不填为当前账本）。只有账本 owner 能执行。"""
        lid, err = resolve_admin_ledger(openid, ledger_name)
        if err:
            return f"操作失败：{err}"
        ok, msg = db.admin_rename_ledger(openid, new_name, lid)
        return f"✅ {msg}" if ok else f"操作失败：{msg}"

    @tool
    def admin_delete_ledger(confirm: str, ledger_name: str = "") -> str:
        """[仅管理员] 删除账本。ledger_name 是要删除的账本（编号或名称，不填为当前账本）。
        只有账本 owner 能执行。confirm 填 'yes' 表示用户已确认。
        删除的是用户默认账本时必须先向用户复述后果并取得确认（工具会拦截未确认的执行）。"""
        lid, err = resolve_admin_ledger(openid, ledger_name)
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

    return [reset_invite_code, admin_remove_member, admin_rename_ledger, admin_delete_ledger]
