"""db 包 —— SQLite 数据层，按业务域拆分（spec 005）。

DB_PATH / _connect 必须住在本文件：测试用 monkeypatch.setattr(db, "DB_PATH", ...)
替换测试库，_connect 读取的是同一命名空间——拆散会造成静默假绿。"""

import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
DB_PATH = Path(__file__).parent / "ledger.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 并发读不阻塞
    return conn

# ── 按域 re-export：import db; db.xxx 的调用方零改动（spec 005 FR-002）──
from .notices import PendingState, append_chat_history, clear_chat_history, clear_pending, drain_undelivered, enqueue_undelivered, get_chat_history, get_pending, mark_seen, set_pending
from .schema import _add_column_if_missing, init
from .users import _ensure_nickname, _ensure_nickname_by_id, _get_user_id_by_openid, cleanup_duplicate_nicknames, find_duplicate_nicknames, get_or_create_user, set_nickname, user_exists, validate_nickname
from .ledgers import _fallback_ledger_id, create_ledger, get_ledger_info, get_ledger_owner_openid, get_my_ledgers, get_user_ledger_id, is_default_ledger, is_ledger_deleted, ledger_member_preview, resolve_ledger_selector, switch_ledger
from .members import _get_openid_by_nickname_in_ledger, admin_delete_ledger, admin_remove_member, admin_rename_ledger, apply_join, approve_join, get_my_join_status, get_my_join_status_text, is_ledger_admin, is_ledger_member, leave_ledger, list_ledger_members, list_pending_joins, reset_invite_code
from .transactions import Transaction, insert_many_for_ledger, query_by_ledger, sum_by_ledger
