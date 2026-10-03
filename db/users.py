"""用户与昵称：创建 / 身份 / 昵称全局唯一 / 默认账本创建原语。"""
import sqlite3
from typing import Optional
from datetime import datetime
from . import SHANGHAI, _connect


def find_duplicate_nicknames() -> list[dict]:
    """FR-010：存量重名检测。返回 [{nickname, count, user_ids}]（不含 NULL/空名）。"""
    with _connect() as conn:
        rows = conn.execute("""
            SELECT nickname, COUNT(*) AS c, GROUP_CONCAT(id) AS ids
            FROM users
            WHERE nickname IS NOT NULL AND nickname != ''
            GROUP BY nickname HAVING c > 1
        """).fetchall()
        return [
            {
                "nickname": r["nickname"],
                "count": r["c"],
                "user_ids": [int(x) for x in r["ids"].split(",")],
            }
            for r in rows
        ]


def cleanup_duplicate_nicknames() -> int:
    """FR-010：存量清理——重名组保留最早注册者，其余改为新生成的默认昵称；
    顺带为 NULL/空名用户补生成昵称。清理后重建唯一索引。返回改名人数。"""
    renamed = 0
    with _connect() as conn:
        for r in conn.execute(
            "SELECT id FROM users WHERE nickname IS NULL OR nickname = ''"
        ).fetchall():
            nick = _gen_default_nickname(conn, r["id"])
            conn.execute("UPDATE users SET nickname=? WHERE id=?", (nick, r["id"]))
            renamed += 1
        for d in find_duplicate_nicknames():
            for uid in sorted(d["user_ids"])[1:]:
                nick = _gen_default_nickname(conn, uid)
                conn.execute("UPDATE users SET nickname=? WHERE id=?", (nick, uid))
                renamed += 1
        conn.commit()
    with _connect() as conn:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_nickname ON users(nickname)"
        )
        conn.commit()
    return renamed


def user_exists(openid: str) -> bool:
    """FR-005：区分"首关"与"回来"——openid 是否已有用户记录。"""
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM users WHERE openid=?", (openid,)).fetchone() is not None


def get_or_create_user(openid: str, nickname: str = "") -> int:
    """根据 openid 找到用户，没有则创建。返回 user_id。

    任务2：新用户创建时自动建一个默认账本「我的账本」（该用户 role=owner）。
    任务3：把默认账本设为用户的 current_ledger_id（显式当前账本）。
    """
    # 函数内导入：ledgers 顶层依赖本文件，函数内导入打破环（spec 005）
    from .ledgers import _create_default_ledger, _fallback_ledger_id
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, current_ledger_id, default_ledger_id FROM users WHERE openid=?",
            (openid,),
        ).fetchone()
        if row:
            # spec 003 FR-035 兜底链：当前账本/默认锚点缺失时回填（默认 → 最近加入）
            updates = {}
            if row["current_ledger_id"] is None:
                led = _fallback_ledger_id(conn, row["id"])
                if led:
                    updates["current_ledger_id"] = led
            if row["default_ledger_id"] is None:
                led = _fallback_ledger_id(conn, row["id"])
                if led:
                    updates["default_ledger_id"] = led
            for col, val in updates.items():
                conn.execute(f"UPDATE users SET {col}=? WHERE id=?", (val, row["id"]))
            if updates:
                conn.commit()
            return row["id"]
        now = datetime.now(SHANGHAI).isoformat()
        # constitution III：昵称永不为空——**插入前**生成（FR-006 全局唯一）。
        # 空名/'None' 永不进表：唯一索引下若表内存在一行空昵称，后续新用户的
        # INSERT 会集体撞索引——安全性来自结构，不依赖"INSERT 后紧跟 UPDATE"的顺序
        if not (nickname or "").strip():
            nickname = _gen_default_nickname(conn, None)
        cur = conn.execute(
            "INSERT INTO users (openid, nickname, created_at) VALUES (?, ?, ?)",
            (openid, nickname, now),
        )
        user_id = cur.lastrowid
        # 自动创建默认账本「我的账本」，用户是 owner，设为当前账本 + 默认锚点
        # （spec 003 FR-002/FR-012：身份与默认账本在初始化时一并建立）
        ledger_id = _create_default_ledger(conn, user_id, now)
        conn.execute(
            "UPDATE users SET current_ledger_id=?, default_ledger_id=? WHERE id=?",
            (ledger_id, ledger_id, user_id),
        )
        conn.commit()
        return user_id


def _create_default_ledger(conn, user_id: int, now: str) -> int:
    """创建默认账本「我的账本」（该用户为 owner）+ 成员关系，返回账本 id。

    get_or_create_user（新用户初始化）与 admin_delete_ledger（FR-013 方案b：
    默认账本被删后自动重建，锚点永不落空）共用。
    """
    from .ledgers import _gen_invite_code  # 函数内导入：打破环
    invite = _gen_invite_code(conn)
    cur = conn.execute(
        "INSERT INTO ledgers (name, owner_user_id, invite_code, created_at) VALUES (?, ?, ?, ?)",
        ("我的账本", user_id, invite, now),
    )
    ledger_id = cur.lastrowid
    conn.execute(
        "INSERT INTO ledger_members (ledger_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
        (ledger_id, user_id, now),
    )
    return ledger_id


def _get_user_id_by_openid(conn, openid: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM users WHERE openid=?", (openid,)).fetchone()
    return row["id"] if row else None


def _nickname_taken(conn, nickname: str, exclude_user_id: Optional[int] = None) -> bool:
    """FR-006：昵称全局唯一——任一**其他**用户占用即冲突（跨账本亦然）。"""
    row = conn.execute(
        "SELECT 1 FROM users WHERE nickname = ? AND id != ? LIMIT 1",
        (nickname, exclude_user_id if exclude_user_id is not None else -1),
    ).fetchone()
    return row is not None


def _gen_default_nickname(conn, user_id: Optional[int] = None, excluded_nickname: str = "") -> str:
    """生成唯一默认昵称「账本成员 + 4位随机hex」（如"账本成员 a3f9"）。

    spec 003 修订（FR-006）：查重范围为**全系统用户**，不再限定账本——
    同时修正 001 期 docstring 与实现不一致的问题。
    user_id 为 None（如新用户 INSERT 前）时全表查重。
    """
    import secrets
    for _ in range(50):
        nick = "账本成员 " + secrets.token_hex(2)  # 4 位 hex（token_hex(2)=4字符）
        if nick == excluded_nickname:
            continue
        if not _nickname_taken(conn, nick, exclude_user_id=user_id):
            return nick
    raise RuntimeError("无法生成唯一默认昵称")


def _ensure_nickname(openid: str) -> str:
    """T003: 保证用户 nickname 非空——为空/空白时生成默认昵称并落库。

    返回最终昵称（可能被落库的默认昵称）。遵循 constitution III（默认昵称用户级一个、永不为空）。
    """
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        row = conn.execute("SELECT nickname FROM users WHERE id=?", (user_id,)).fetchone()
        cur_nick = (row["nickname"] if row else "") or ""
        if cur_nick.strip():  # 已有非空昵称
            return cur_nick
        # 为空 → 生成默认昵称并落库
        nick = _gen_default_nickname(conn, user_id)
        conn.execute("UPDATE users SET nickname=? WHERE id=?", (nick, user_id))
        conn.commit()
        return nick


def validate_nickname(openid: str, nickname: str) -> str:
    """FR-007/FR-008：昵称合法性校验。合法返回 ""，否则返回可直接展示的错误消息
    （占用提示不透露占用者身份）。"""
    if "\n" in (nickname or "") or "\r" in (nickname or ""):
        return "昵称不能包含换行符"
    nick = (nickname or "").strip()
    if not nick:
        return "昵称不能为空"
    if len(nick) > 20:
        return "昵称不能超过 20 个字符"
    uid = get_or_create_user(openid)
    with _connect() as conn:
        if _nickname_taken(conn, nick, exclude_user_id=uid):
            return "该昵称已被占用，请换一个"
    return ""


def set_nickname(openid: str, nickname: str) -> bool:
    """FR-006~FR-009：设置/更新用户昵称（**全局唯一**）。

    空/空白昵称不生效；非法或已被占用被拒（不透露占用者）；
    改名到自身当前昵称视为成功；并发抢占由唯一索引兜底（至多一个成功）。
    """
    nick = (nickname or "").strip()
    if not nick:
        return False
    if validate_nickname(openid, nick):
        return False
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        try:
            conn.execute("UPDATE users SET nickname=? WHERE id=?", (nick, user_id))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def _ensure_nickname_by_id(user_id: int) -> str:
    """按 user_id 保证昵称非空并落库（供 list_ledger_members 内部用）。"""
    with _connect() as conn:
        row = conn.execute("SELECT nickname FROM users WHERE id=?", (user_id,)).fetchone()
        cur = (row["nickname"] if row else "") or ""
        if cur.strip():
            return cur
        nick = _gen_default_nickname(conn, user_id)
        conn.execute("UPDATE users SET nickname=? WHERE id=?", (nick, user_id))
        conn.commit()
        return nick
