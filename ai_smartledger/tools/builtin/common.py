"""builtin 共享助手（不直接暴露给 LLM）。"""
from ... import db


def resolve_admin_ledger(openid: str, ledger_name: str):
    """FR-023：管理操作指定账本——ledger_name 为空 → 当前账本；
    否则按编号/名称在用户已加入的账本内解析。返回 (ledger_id, 错误消息)。"""
    sel = (ledger_name or "").strip()
    if not sel:
        return db.get_user_ledger_id(openid), None
    return db.resolve_ledger_selector(openid, sel)
