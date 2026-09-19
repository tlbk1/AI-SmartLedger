"""
db.py — SQLite 持久化 + 进程内缓存（幂等去重 / 对话状态）

设计依据：
- 需求文档 11.3: 单表 transactions
- 需求文档 14.1: msgid 幂等去重，内存 dict，TTL 10min
- 需求文档 14.3: pending 对话状态，内存 dict，TTL 10min
- 需求文档 14.6: 多笔记账整批事务，全成全不记
- 需求文档 14.7: 同步 sqlite3，不用 aiosqlite
- 需求文档 14.6: 时区统一 Asia/Shanghai
"""

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
DB_PATH = Path(__file__).parent / "ledger.db"

# ──────────────────────────── 带过期时间的缓存 ────────────────────────────

@dataclass
class _TTLCache:
    """线程安全的带 TTL 的内存缓存。"""
    _store: dict = field(default_factory=dict)   # key -> (value, expire_ts)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key: str) -> Optional[object]:
        """命中返回 value，未命中或过期返回 None。"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expire_ts = entry
            if time.monotonic() > expire_ts:       # 已过期
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: object, ttl_seconds: int = 600):
        with self._lock:
            self._store[key] = (value, time.monotonic() + ttl_seconds)

    def pop(self, key: str) -> Optional[object]:
        """取出并删除；不存在返回 None。"""
        with self._lock:
            entry = self._store.pop(key, None)
            if entry is None:
                return None
            value, _ = entry
            return value

    def clear_expired(self):
        """主动清扫过期条目（可选调用）。"""
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (_, ts) in self._store.items() if now > ts]
            for k in expired:
                del self._store[k]


# ──────────────────────────── 全局缓存实例 ────────────────────────────

_seen_cache: _TTLCache = _TTLCache()       # msgid -> True（幂等去重）
_pending_cache: _TTLCache = _TTLCache()    # openid -> pending dict（对话状态）
_chat_history_cache: _TTLCache = _TTLCache()   # openid -> list[messages]（任务4：对话记忆）


# ──────────────────────────── 对话记忆（任务4：澄清循环） ────────────────────────────

# 每个用户保留最近 N 条消息，供 agent 多轮对话参考（澄清反问后用户补答能接上）。
_CHAT_HISTORY_MAX = 20          # 最近消息条数
_CHAT_HISTORY_TTL = 1800        # 30 分钟


def get_chat_history(openid: str) -> list:
    """取该用户最近的对话历史（列表，可为空）。"""
    return _chat_history_cache.get(openid) or []


def append_chat_history(openid: str, messages: list, maxlen: int = _CHAT_HISTORY_MAX):
    """把新消息追加到该用户历史，并裁剪到最近 maxlen 条。"""
    hist = get_chat_history(openid)
    hist.extend(messages)
    if len(hist) > maxlen:
        hist = hist[-maxlen:]
    _chat_history_cache.set(openid, hist, ttl_seconds=_CHAT_HISTORY_TTL)


def clear_chat_history(openid: str):
    """清空该用户对话历史。"""
    _chat_history_cache.pop(openid)


# ──────────────────────────── 幂等去重 ────────────────────────────

def mark_seen(msg_id: str) -> bool:
    """
    尝试将 msg_id 标记为已见。
    返回 True = 第一次见（应该处理）；
    返回 False = 已经见过了（重复消息，丢弃）。
    """
    if _seen_cache.get(msg_id) is not None:
        return False
    _seen_cache.set(msg_id, True, ttl_seconds=600)
    return True


# ──────────────────────────── 对话状态（反问机制） ────────────────────────────

@dataclass
class PendingState:
    """反问期间暂存的半成品账单 + 问题。"""
    draft: dict          # 部分填充的 transaction（缺 amount）
    question: str        # 反问的问题文本


def set_pending(openid: str, draft: dict, question: str):
    """用户被反问时存入 pending 状态。"""
    _pending_cache.set(openid, PendingState(draft=draft, question=question), ttl_seconds=600)


def get_pending(openid: str) -> Optional[PendingState]:
    """取出 pending（不删除）。过期返回 None。"""
    return _pending_cache.get(openid)


def clear_pending(openid: str):
    """记账完成或过期后清除。"""
    _pending_cache.pop(openid)


# ──────────────────────────── 未送达消息队列（FR-039~FR-042） ────────────────────────────

def enqueue_undelivered(openid: str, text: str):
    """FR-039：推送失败或通知生成异常时入队，等下次补发。
    spec 003：**持久化到 sqlite**（FR-041，重启不丢），替代 002 的进程内存 dict。"""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO undelivered_notices (openid, text, created_at) VALUES (?, ?, ?)",
            (openid, text, datetime.now(SHANGHAI).isoformat()),
        )
        conn.commit()


def drain_undelivered(openid: str) -> list[str]:
    """FR-042：取出并清空该用户所有待补发通知（按时间先后）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, text FROM undelivered_notices WHERE openid=? ORDER BY id ASC",
            (openid,),
        ).fetchall()
        if not rows:
            return []
        conn.execute(
            "DELETE FROM undelivered_notices WHERE id IN (%s)"
            % ",".join("?" * len(rows)),
            [r["id"] for r in rows],
        )
        conn.commit()
        return [r["text"] for r in rows]


# ──────────────────────────── SQLite 连接 ────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 并发读不阻塞
    return conn


def init():
    """启动时调用：建表。"""
    with _connect() as conn:
        # 现有 transactions 表（保持原结构，兼容旧数据）—— 若不存在则建
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                type         TEXT    NOT NULL,     -- expense / income
                amount       REAL    NOT NULL,     -- 金额（元）
                category     TEXT    NOT NULL,     -- 中文名，如"餐饮"
                note         TEXT,
                happened_at  TEXT    NOT NULL,     -- ISO 字符串, Asia/Shanghai
                created_at   TEXT    NOT NULL      -- ISO 字符串
            )
        """)

        # 给 transactions 加共账字段（若还不存在）—— 可空，兼容旧数据
        _add_column_if_missing(conn, "transactions", "ledger_id", "INTEGER")
        _add_column_if_missing(conn, "transactions", "created_by_user_id", "INTEGER")

        # 新增 3 张表：users / ledgers / ledger_members
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                openid     TEXT    UNIQUE NOT NULL,   -- 微信唯一标识
                nickname   TEXT,
                created_at TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ledgers (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL,        -- 账本名，如"我们家"
                owner_user_id INTEGER,
                invite_code   TEXT    UNIQUE,          -- 邀请口令（口令制核心）
                created_at    TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger_members (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ledger_id INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                role      TEXT    DEFAULT 'member',    -- owner / member
                joined_at TEXT,
                UNIQUE(ledger_id, user_id)             -- 一人在一个账本最多一条
            )
        """)

        # 软删除：ledgers.deleted_at（任务2，幂等补列）；NULL = 未删除
        _add_column_if_missing(conn, "ledgers", "deleted_at", "TEXT")

        # 任务3：users.current_ledger_id 显式记录用户当前账本（默认账本创建时设为它）
        _add_column_if_missing(conn, "users", "current_ledger_id", "INTEGER")

        # spec 003 FR-041：待补发通知持久化（重启后仍在，不再存进程内存）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS undelivered_notices (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                openid     TEXT NOT NULL,
                text       TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # spec 003 FR-012：users.default_ledger_id —— 默认账本"指向关系"锚点
        # （不靠名字识别：改名不影响指向；删除默认账本触发重建并更新指向）
        _add_column_if_missing(conn, "users", "default_ledger_id", "INTEGER")
        # 存量回填：优先「我的账本」（owner+未删除），否则最近加入的未删除账本
        for u in conn.execute(
            "SELECT id FROM users WHERE default_ledger_id IS NULL"
        ).fetchall():
            uid = u["id"]
            row = conn.execute("""
                SELECT l.id FROM ledgers l
                WHERE l.owner_user_id = ? AND l.name = '我的账本' AND l.deleted_at IS NULL
                ORDER BY l.id LIMIT 1
            """, (uid,)).fetchone()
            if row is None:
                row = conn.execute("""
                    SELECT lm.ledger_id AS id FROM ledger_members lm
                    JOIN ledgers l ON l.id = lm.ledger_id
                    WHERE lm.user_id = ? AND l.deleted_at IS NULL
                    ORDER BY lm.id DESC LIMIT 1
                """, (uid,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE users SET default_ledger_id=? WHERE id=?", (row["id"], uid)
                )
        conn.commit()

        # spec 003 FR-009：昵称全局唯一索引（并发设置同一昵称时至多一个成功）。
        # 存量若有重名会建失败——先运行 db.cleanup_duplicate_nicknames()（FR-010）
        import logging
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_nickname ON users(nickname)"
            )
            conn.commit()
        except sqlite3.IntegrityError:
            logging.getLogger(__name__).warning(
                "昵称唯一索引创建失败（存量存在重名/空名）——请运行 db.cleanup_duplicate_nicknames()"
            )

        # spec 002/T002：join_requests 加入申请表（审批制核心）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS join_requests (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ledger_id  INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'pending',  -- pending/approved/expired/rejected(预留)
                created_at TEXT    NOT NULL,
                UNIQUE(ledger_id, user_id)                      -- 幂等：一人一账本一条申请
            )
        """)


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


def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    """若表里还没有某列，则添加（幂等，兼容旧库）。"""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


# ──────────────────────────── transactions CRUD ────────────────────────────

@dataclass
class Transaction:
    type: str
    amount: float
    category: str
    happened_at: str
    note: str = ""
    created_at: str = ""

    def to_row(self) -> tuple:
        now = datetime.now(SHANGHAI).isoformat()
        return (
            self.type,
            self.amount,
            self.category,
            self.note or "",
            self.happened_at,
            self.created_at or now,
        )


# ════════════════════════ 共账（多用户） ════════════════════════

# 邀请口令：随机 6 位字母数字（去易混淆字符）
_INVITE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def _gen_invite_code(conn) -> str:
    """生成唯一邀请口令（6位，去易混淆字符）。"""
    import secrets
    for _ in range(50):  # 防碰撞，最多试50次
        code = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(6))
        if conn.execute("SELECT 1 FROM ledgers WHERE invite_code=?", (code,)).fetchone() is None:
            return code
    raise RuntimeError("无法生成唯一邀请口令")


def user_exists(openid: str) -> bool:
    """FR-005：区分"首关"与"回来"——openid 是否已有用户记录。"""
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM users WHERE openid=?", (openid,)).fetchone() is not None


def get_or_create_user(openid: str, nickname: str = "") -> int:
    """根据 openid 找到用户，没有则创建。返回 user_id。

    任务2：新用户创建时自动建一个默认账本「我的账本」（该用户 role=owner）。
    任务3：把默认账本设为用户的 current_ledger_id（显式当前账本）。
    """
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


def _fallback_ledger_id(conn, user_id: int, exclude: Optional[int] = None) -> Optional[int]:
    """FR-035 兜底链：用户自己的默认账本 → 最近加入的未删除账本 → None。

    评审问题2修复：旧逻辑只按「最近加入」回退，会把用户静默落进别人的共享账本。
    exclude 用于回落场景排除刚失效的那个账本。
    """
    default_lid = _get_default_ledger_id(conn, user_id)
    if default_lid is not None and default_lid != exclude:
        return default_lid
    row = conn.execute("""
        SELECT lm.ledger_id
        FROM ledger_members lm
        JOIN ledgers l ON l.id = lm.ledger_id
        WHERE lm.user_id = ? AND l.deleted_at IS NULL AND lm.ledger_id != ?
        ORDER BY lm.id DESC
        LIMIT 1
    """, (user_id, exclude if exclude is not None else -1)).fetchone()
    return row["ledger_id"] if row else None


def _settle_current_ledger(conn, uid: int, dead_ledger_id: int):
    """FR-032/033/034：uid 的当前账本失效（被移除/退出/被删）后确定回落。

    默认账本优先，取不到退最近加入（FR-035）；仅当用户已无任何账本
    （仅存量脏数据可达）才写空——由读时兜底链终结为「明确报错」。
    """
    cur = conn.execute("SELECT current_ledger_id FROM users WHERE id=?", (uid,)).fetchone()
    if not (cur and cur["current_ledger_id"] == dead_ledger_id):
        return
    target = _fallback_ledger_id(conn, uid, exclude=dead_ledger_id)
    conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (target, uid))


def get_user_ledger_id(openid: str) -> Optional[int]:
    """根据 openid 找用户「当前账本」id（任务3：读 users.current_ledger_id）。

    FR-036：**尊重用户的显式选择**——即使用户当前账本已被软删除也照原样返回
    （让他能查看历史账目，只读；写入在 insert/工具层拒绝）。
    FR-035：仅当指针为空或悬空时才兜底——默认账本 → 最近加入的未删除账本 →
    None（上层明确报错，绝不静默落进他人账本）。
    """
    with _connect() as conn:
        row = conn.execute("""
            SELECT u.current_ledger_id
            FROM users u
            WHERE u.openid = ?
        """, (openid,)).fetchone()
        if row and row["current_ledger_id"] is not None:
            # 账本存在即返回（含已软删除——用户显式切入是为了看历史，只读）
            l = conn.execute("SELECT 1 FROM ledgers WHERE id=?", (row["current_ledger_id"],)).fetchone()
            if l:
                return row["current_ledger_id"]
        # FR-035 兜底链：默认账本 → 最近加入的未删除账本 → None
        u = conn.execute("SELECT id FROM users WHERE openid=?", (openid,)).fetchone()
        if u is None:
            return None
        return _fallback_ledger_id(conn, u["id"])


def create_ledger(openid: str, name: str) -> tuple[bool, str]:
    """创建账本，创建者为 owner，并设为该用户的当前账本（任务3）。
    FR-008 同类校验（评审）：账本名不能为空/纯空白——否则列表里出现无名条目、
    且无法按名字选中（空 selector 会被解析器当成"未指定"）。
    返回 (成功?, 结果消息或口令)。"""
    name = (name or "").strip()
    if not name:
        return False, "账本名不能为空，请给账本起个名字（比如「我们家」「旅行账」）"
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        invite = _gen_invite_code(conn)
        now = datetime.now(SHANGHAI).isoformat()
        cur = conn.execute(
            "INSERT INTO ledgers (name, owner_user_id, invite_code, created_at) VALUES (?, ?, ?, ?)",
            (name, user_id, invite, now),
        )
        ledger_id = cur.lastrowid
        conn.execute(
            "INSERT INTO ledger_members (ledger_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
            (ledger_id, user_id, now),
        )
        # 任务3：新创建的账本设为当前账本
        conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (ledger_id, user_id))
        conn.commit()
        return True, invite


def get_ledger_owner_openid(ledger_id: int) -> Optional[str]:
    """FR-038：取账本 owner 的 openid（仅供推送通知使用，绝不进 LLM/展示层）。"""
    with _connect() as conn:
        row = conn.execute("""
            SELECT u.openid FROM ledgers l
            JOIN users u ON u.id = l.owner_user_id
            WHERE l.id = ?
        """, (ledger_id,)).fetchone()
        return row["openid"] if row else None


def get_my_latest_pending_join(openid: str) -> Optional[dict]:
    """FR-038：取该用户最近一条 pending 申请（含账本 id/名），供"通知 owner"用。"""
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        row = conn.execute("""
            SELECT jr.ledger_id, l.name AS ledger_name
            FROM join_requests jr
            JOIN ledgers l ON l.id = jr.ledger_id
            WHERE jr.user_id = ? AND jr.status = 'pending'
            ORDER BY jr.id DESC LIMIT 1
        """, (user_id,)).fetchone()
        if row is None:
            return None
        return {"ledger_id": row["ledger_id"], "ledger_name": row["ledger_name"]}


def _members_preview(conn, ledger_id: int, limit: int = 5) -> tuple[list[str], int]:
    """FR-016：成员昵称预览——owner 优先、其余按加入顺序（稳定），截断并返回总人数。
    只返回昵称，绝不返回 openid（constitution 原则 II）。"""
    rows = conn.execute("""
        SELECT u.id, u.nickname FROM ledger_members lm
        JOIN users u ON u.id = lm.user_id
        WHERE lm.ledger_id = ?
        ORDER BY CASE WHEN lm.role='owner' THEN 0 ELSE 1 END, lm.id ASC
    """, (ledger_id,)).fetchall()
    names = []
    for r in rows[:limit]:
        nick = (r["nickname"] or "").strip()
        if not nick:
            nick = _ensure_nickname_by_id(conn, r["id"])
        names.append(nick)
    return names, len(rows)


def ledger_member_preview(ledger_id: int, limit: int = 5) -> dict:
    """FR-015/FR-016：对外取某账本成员名单预览（供账本列表展示）。"""
    with _connect() as conn:
        names, total = _members_preview(conn, ledger_id, limit)
        return {"names": names, "total": total}


def get_my_ledgers(openid: str) -> list[dict]:
    """列出用户加入的所有账本（FR-015/FR-018 的数据源）。

    - **包含已软删除的账本**（带 `is_deleted` 标记）——原成员仍能查看其历史账目
    - 附 `is_current` / `is_default` 标记（FR-014：默认账本由指向关系确定）
    - 按加入顺序排序（lm.id ASC）：新加入的排末尾，展示顺序稳定
    """
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        u = conn.execute(
            "SELECT current_ledger_id, default_ledger_id FROM users WHERE id=?", (user_id,)
        ).fetchone()
        cur_id = u["current_ledger_id"] if u else None
        def_id = u["default_ledger_id"] if u else None
        rows = conn.execute("""
            SELECT l.id, l.name, l.invite_code, lm.role, lm.joined_at, l.deleted_at
            FROM ledger_members lm
            JOIN ledgers l ON l.id = lm.ledger_id
            WHERE lm.user_id = ?
            ORDER BY lm.id ASC
        """, (user_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["is_deleted"] = bool(d.get("deleted_at"))
            d["is_current"] = d["id"] == cur_id
            d["is_default"] = d["id"] == def_id
            out.append(d)
        return out


def get_ledger_info(ledger_id: int) -> Optional[dict]:
    """T050：取账本信息（含已软删除的）。返回 {id, name, is_deleted} 或 None。

    与 get_user_ledger_id 不同：**不过滤 deleted_at**，供"查已删账本历史"路径判断是否要附提示。
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, name, deleted_at FROM ledgers WHERE id=?", (ledger_id,)
        ).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "name": row["name"], "is_deleted": bool(row["deleted_at"])}


def is_ledger_deleted(ledger_id: Optional[int]) -> bool:
    """T050：账本是否已被软删除（不存在也算 True——不存在的账本不可用）。"""
    if ledger_id is None:
        return True
    info = get_ledger_info(ledger_id)
    return True if info is None else info["is_deleted"]


def resolve_ledger_selector(openid: str, selector: str) -> tuple[Optional[int], Optional[str]]:
    """FR-018/FR-023：在用户已加入的账本（含已删除）中解析 selector（`#N` 或名称）。

    返回 (ledger_id, None) 或 (None, 提示文本)。重名时提示文本为候选列表
    （编号+成员名单，由代码实时重算，FR-022），供调用方原样转述给用户。
    """
    user_id = get_or_create_user(openid)
    sel = (selector or "").strip()
    if not sel:
        return None, "请指定账本（编号或名称）"
    with _connect() as conn:
        raw = sel.lstrip("#").strip()
        if raw.isdigit():
            row = conn.execute("""
                SELECT l.id FROM ledger_members lm
                JOIN ledgers l ON l.id = lm.ledger_id
                WHERE lm.user_id = ? AND l.id = ?
            """, (user_id, int(raw))).fetchone()
            if row is None:
                return None, f"没有编号为 #{int(raw)} 的账本"
            return row["id"], None
        rows = conn.execute("""
            SELECT l.id, l.name FROM ledger_members lm
            JOIN ledgers l ON l.id = lm.ledger_id
            WHERE lm.user_id = ? AND l.name = ?
            ORDER BY lm.id ASC
        """, (user_id, sel)).fetchall()
        if not rows:
            return None, f"你还没有加入叫「{sel}」的账本"
        if len(rows) > 1:
            # FR-019：重名不猜——列候选请用户回复编号
            lines = []
            for r in rows:
                names, total = _members_preview(conn, r["id"])
                extra = f" 等 {total} 人" if total > len(names) else ""
                lines.append(f"· #{r['id']} {r['name']}（成员：{'、'.join(names) or '（无）'}{extra}）")
            return None, f"你有 {len(rows)} 个叫「{sel}」的账本，回复编号选择：\n" + "\n".join(lines)
        return rows[0]["id"], None


def switch_ledger(openid: str, selector: str) -> tuple[bool, str]:
    """FR-018~FR-022：用编号（`#N` / 裸数字）或名称切换当前账本。

    - `#N`/纯数字 → 按内部 id 在用户账本列表（含已删除）中**精确匹配**；
      未命中明确报错，MUST NOT 按位置/最近加入解释（FR-018/FR-021）
    - 名称唯一 → 直接切换（FR-020）
    - 名称重名 → **不切换**，返回全部同名候选请用户回复编号（FR-019/FR-022）
    - 允许**显式切入**已删除账本（合法只读，带提示）（FR-036）
    """
    user_id = get_or_create_user(openid)
    sel = (selector or "").strip()
    if not sel:
        return False, "请告诉我要切换到哪个账本（编号或名称），如「#4」或「我们家」"
    lid, err = resolve_ledger_selector(openid, sel)
    if err is not None:
        return False, err
    with _connect() as conn:
        target = conn.execute(
            "SELECT id, name, deleted_at FROM ledgers WHERE id=?", (lid,)
        ).fetchone()
        if target is None:
            return False, "该账本不存在"
        conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (target["id"], user_id))
        conn.commit()
        if target["deleted_at"]:
            return True, (
                f"已切到「{target['name']}」（#{target['id']}）——该账本已被删除，"
                "只能查看历史账目（只读），不能再记账。"
            )
        return True, f"已切到「{target['name']}」（#{target['id']}），后续记账/查账都在这个账本"


def get_current_ledger_name(openid: str) -> str:
    """取用户当前账本名（用于 agent 回复显示）。无则返回空串。"""
    ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return ""
    with _connect() as conn:
        row = conn.execute("SELECT name FROM ledgers WHERE id=?", (ledger_id,)).fetchone()
        return row["name"] if row else ""


# ─── 记账 / 查账（带账本隔离）───

def insert_many_for_ledger(ledger_id: int, created_by_user_id: int, txns: list[Transaction]) -> bool:
    """整批写入事务，每笔带上 ledger_id + created_by_user_id。
    防御（任务2）：ledger_id 不允许 NULL——避免写入查不到的孤儿数据。
    防御（T050/FR-014）：**已软删除的账本只读**，拒绝写入。"""
    if not txns:
        return True
    if ledger_id is None:
        import logging
        logging.getLogger(__name__).error("insert_many_for_ledger 拒绝: ledger_id 为 None")
        return False
    # T050：已删除账本只读（db 层纵深防御，不只靠 agent 层拦截）
    if is_ledger_deleted(ledger_id):
        import logging
        logging.getLogger(__name__).warning(
            "insert_many_for_ledger 拒绝: 账本 %s 已删除（只读）", ledger_id
        )
        return False
    conn = _connect()
    try:
        with conn:
            for t in txns:
                now = datetime.now(SHANGHAI).isoformat()
                conn.execute(
                    "INSERT INTO transactions (type, amount, category, note, happened_at, created_at, ledger_id, created_by_user_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (t.type, t.amount, t.category, t.note or "", t.happened_at,
                     t.created_at or now, ledger_id, created_by_user_id),
                )
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("insert_many_for_ledger 失败: %s", e, exc_info=True)
        return False
    finally:
        conn.close()


def query_by_ledger(
    ledger_id: int,
    date_from: str,
    date_to: str,
    category: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """按账本隔离的查询。只查指定 ledger 的记录。

    US5/T027：附加 `created_by_nickname`（记账人昵称）——账目共享时展示"谁记的"。
    **绝不返回 openid**（constitution 原则 II）。
    """
    dt_from = datetime.fromisoformat(date_from).replace(
        tzinfo=SHANGHAI, hour=0, minute=0, second=0
    )
    dt_to = datetime.fromisoformat(date_to).replace(
        tzinfo=SHANGHAI, hour=0, minute=0, second=0
    ) + timedelta(days=1)

    sql = (
        "SELECT t.*, u.nickname AS created_by_nickname "
        "FROM transactions t "
        "LEFT JOIN users u ON u.id = t.created_by_user_id "
        "WHERE t.ledger_id = ? AND t.happened_at >= ? AND t.happened_at < ?"
    )
    args: list = [ledger_id, dt_from.isoformat(), dt_to.isoformat()]

    if category:
        sql += " AND t.category = ?"
        args.append(category)
    if type_filter:
        sql += " AND t.type = ?"
        args.append(type_filter)
    sql += " ORDER BY t.happened_at DESC LIMIT ?"
    args.append(limit)

    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # 不返回 openid（即使表里有）；内部 user_id 也不外露——昵称已由
            # created_by_nickname 提供（评审：防 LLM 总结时吐出「记账人 3」）
            d.pop("openid", None)
            d.pop("created_by_user_id", None)
            out.append(d)
        return out


# ════════════════════════ 账本内分权（owner = 管理 / member = 普通） ════════════════════════

def is_ledger_admin(openid: str, ledger_id: Optional[int] = None) -> bool:
    """判断 openid 是否是指定账本的 owner（管理员）。

    T003/spec-002 D2：显式传 ledger_id 时按【目标账本】判定（修复"当前账本误判"缺陷）；
    不传时回退到当前账本（向后兼容旧调用）。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False
    with _connect() as conn:
        row = conn.execute("""
            SELECT lm.role
            FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE u.openid = ? AND lm.ledger_id = ?
        """, (openid, ledger_id)).fetchone()
        return bool(row and row["role"] == "owner")


def is_ledger_member(openid: str, ledger_id: int) -> bool:
    """T004：判断 openid 是否为指定账本的成员（owner 或 member 均可）。"""
    with _connect() as conn:
        row = conn.execute("""
            SELECT 1 FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE u.openid = ? AND lm.ledger_id = ?
        """, (openid, ledger_id)).fetchone()
        return row is not None


def _get_ledger_id_by_invite(conn, invite_code: str) -> Optional[tuple[int, str]]:
    """T005：按口令查未删除账本，返回 (ledger_id, name)；找不到返回 None。"""
    row = conn.execute(
        "SELECT id, name FROM ledgers WHERE invite_code=? AND deleted_at IS NULL",
        (invite_code.strip().lower(),),
    ).fetchone()
    return (row["id"], row["name"]) if row else None


def _get_default_ledger_id(conn, user_id: int) -> Optional[int]:
    """FR-012：默认账本由指向关系确定（users.default_ledger_id），**不靠名字识别**。

    账本改名不影响返回值。指向已删除/不存在的账本视为失效（返回 None 走兜底）——
    正常数据下不会发生（删除默认账本会触发重建并更新指向，见 admin_delete_ledger）。
    """
    row = conn.execute(
        "SELECT default_ledger_id FROM users WHERE id=?", (user_id,)
    ).fetchone()
    lid = row["default_ledger_id"] if row else None
    if lid is None:
        return None
    alive = conn.execute(
        "SELECT 1 FROM ledgers WHERE id=? AND deleted_at IS NULL", (lid,)
    ).fetchone()
    return lid if alive else None


def _get_user_id_by_openid(conn, openid: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM users WHERE openid=?", (openid,)).fetchone()
    return row["id"] if row else None


# ════════════════════════ 审批制加入（spec 002 / US1, US2） ════════════════════════

def apply_join(openid: str, invite_code: str) -> tuple[bool, str]:
    """US1/T010：凭口令提交加入申请 → 进入待审批(pending)。

    - 口令无效 → 失败
    - 已是成员 → 提示已是成员
    - 已有 pending → 幂等，不重复
    返回 (成功?, 消息)。
    """
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        found = _get_ledger_id_by_invite(conn, invite_code)
        if found is None:
            return False, "口令不存在，请核对"
        ledger_id, name = found
        # 已是成员？
        if conn.execute(
            "SELECT 1 FROM ledger_members WHERE ledger_id=? AND user_id=?", (ledger_id, user_id)
        ).fetchone():
            return False, f"你已经是「{name}」的成员了"
        # 已有申请？
        row = conn.execute(
            "SELECT status FROM join_requests WHERE ledger_id=? AND user_id=?", (ledger_id, user_id)
        ).fetchone()
        if row:
            if row["status"] == "pending":
                return True, f"你已经申请加入「{name}」，等管理员同意即可"
            if row["status"] == "approved":
                # 仍是成员 → 真的已是成员；若已被移除（ledger_members 无此人）→ 允许重新申请
                still_member = conn.execute(
                    "SELECT 1 FROM ledger_members WHERE ledger_id=? AND user_id=?",
                    (ledger_id, user_id),
                ).fetchone()
                if still_member:
                    return False, f"你已经是「{name}」的成员了"
                conn.execute(
                    "UPDATE join_requests SET status='pending', created_at=? WHERE ledger_id=? AND user_id=?",
                    (datetime.now(SHANGHAI).isoformat(), ledger_id, user_id),
                )
                conn.commit()
                return True, f"已重新提交加入「{name}」的申请，等管理员同意"
            # expired/rejected 之外的旧记录 → 重新申请（覆盖为 pending）
            conn.execute(
                "UPDATE join_requests SET status='pending', created_at=? WHERE ledger_id=? AND user_id=?",
                (datetime.now(SHANGHAI).isoformat(), ledger_id, user_id),
            )
            conn.commit()
            return True, f"已重新提交加入「{name}」的申请，等管理员同意"
        # 新建申请
        conn.execute(
            "INSERT INTO join_requests (ledger_id, user_id, status, created_at) VALUES (?, ?, 'pending', ?)",
            (ledger_id, user_id, datetime.now(SHANGHAI).isoformat()),
        )
        conn.commit()
        return True, f"已提交加入「{name}」的申请，等管理员同意即可"


def get_my_join_status(openid: str, ledger_id: int) -> Optional[str]:
    """US1/T011（FR-015）：查自己对某账本的申请状态；无申请返回 None。"""
    with _connect() as conn:
        uid = _get_user_id_by_openid(conn, openid)
        if uid is None:
            return None
        row = conn.execute(
            "SELECT status FROM join_requests WHERE ledger_id=? AND user_id=?", (ledger_id, uid)
        ).fetchone()
        return row["status"] if row else None


def _get_openid_by_nickname_in_ledger(ledger_id: int, nickname: str) -> Optional[str]:
    """T018：按昵称查某账本内成员的 openid（仅用于审批后推送通知，绝不进 LLM）。"""
    with _connect() as conn:
        row = conn.execute("""
            SELECT u.openid FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE lm.ledger_id = ? AND u.nickname = ?
            LIMIT 1
        """, (ledger_id, nickname)).fetchone()
        return row["openid"] if row else None


def get_my_join_status_text(openid: str) -> str:
    """US1：把用户所有申请状态整理成文本（供 agent 工具展示）。"""
    with _connect() as conn:
        uid = _get_user_id_by_openid(conn, openid)
        if uid is None:
            return "你还没有任何加入申请。"
        rows = conn.execute("""
            SELECT l.name, jr.status
            FROM join_requests jr
            JOIN ledgers l ON l.id = jr.ledger_id
            WHERE jr.user_id = ?
            ORDER BY jr.id DESC
        """, (uid,)).fetchall()
    if not rows:
        return "你还没有任何加入申请。"
    label = {"pending": "待审批", "approved": "已通过", "expired": "已作废（口令已重置）", "rejected": "已拒绝"}
    lines = [f"- 「{r['name']}」：{label.get(r['status'], r['status'])}" for r in rows]
    return "你的加入申请：\n" + "\n".join(lines)


def list_pending_joins(openid: str, ledger_id: Optional[int] = None) -> list[dict]:
    """US5/FR-026：owner 视角列出【指定账本】（缺省当前账本）的全部待审批申请
    （含申请人 nickname，不含 openid）。非该账本 owner → []。"""
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None or not is_ledger_admin(openid, ledger_id):
        return []
    with _connect() as conn:
        rows = conn.execute("""
            SELECT u.nickname, jr.created_at
            FROM join_requests jr
            JOIN users u ON u.id = jr.user_id
            WHERE jr.ledger_id = ? AND jr.status = 'pending'
            ORDER BY jr.id
        """, (ledger_id,)).fetchall()
        return [dict(r) for r in rows]


def approve_join(openid: str, applicant_nickname: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US5/FR-023/FR-026：owner 同意【指定账本】（缺省当前账本）的某申请人。

    按申请人昵称在该账本的 pending 申请中定位；pending → approved，并插入 member。
    返回 (成功?, 消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能同意加入申请"
    if is_ledger_deleted(ledger_id):
        return False, "该账本已被删除（只读），无法批准加入申请"
    with _connect() as conn:
        # 找 pending 申请中昵称匹配的申请人
        row = conn.execute("""
            SELECT jr.id AS req_id, u.id AS uid, u.nickname
            FROM join_requests jr
            JOIN users u ON u.id = jr.user_id
            WHERE jr.ledger_id = ? AND jr.status = 'pending' AND u.nickname = ?
            LIMIT 1
        """, (ledger_id, applicant_nickname)).fetchone()
        if row is None:
            return False, f"没有叫「{applicant_nickname}」的待审批申请"
        now = datetime.now(SHANGHAI).isoformat()
        conn.execute("UPDATE join_requests SET status='approved' WHERE id=?", (row["req_id"],))
        conn.execute(
            "INSERT OR IGNORE INTO ledger_members (ledger_id, user_id, role, joined_at) "
            "VALUES (?, ?, 'member', ?)",
            (ledger_id, row["uid"], now),
        )
        conn.commit()
        return True, f"已同意「{applicant_nickname}」加入"


def admin_remove_member(openid: str, target_nickname: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US3/T021 + US4/T024：owner 移除账本成员（按昵称在账本内查人）。

    - 权限按【目标账本】判定（显式传 ledger_id，默认当前账本）
    - **owner 不可被移除**（D7）
    - 移除只删成员关系，**不动历史账目**
    返回 (成功?, 消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能移除成员"
    if is_ledger_deleted(ledger_id):
        return False, "该账本已被删除（只读），无法移除成员"
    with _connect() as conn:
        # 在账本内按昵称找目标用户（注意：不在账本里的同名用户不误伤）
        row = conn.execute("""
            SELECT u.id, u.nickname, lm.role
            FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE lm.ledger_id = ? AND u.nickname = ?
            ORDER BY lm.id DESC LIMIT 1
        """, (ledger_id, target_nickname)).fetchone()
        if row is None:
            return False, f"账本里没有叫「{target_nickname}」的成员"
        # owner 不可被移除（D7）
        if row["role"] == "owner":
            return False, "不能移除管理员（owner）"
        target_uid = row["id"]
        cur = conn.execute(
            "DELETE FROM ledger_members WHERE ledger_id=? AND user_id=?",
            (ledger_id, target_uid),
        )
        conn.commit()
        if cur.rowcount == 0:
            return False, "该用户不在这个账本里"
        # FR-032/033/034：被移除者的当前账本确定回落（默认账本优先，永不静默落他人账本）
        _settle_current_ledger(conn, target_uid, ledger_id)
        conn.commit()
        return True, f"已移除成员 {target_nickname}"


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
            return False  # FR-009：并发抢占同一昵称，后到者失败


def list_ledger_members(openid: str) -> list[dict]:
    """T004/T009: 列出当前账本的成员，只含代表身份的信息，绝不返回 openid。

    - SELECT 不再取 openid（从源头杜绝泄露，constitution 原则 I）
    - 返回前对每个成员调用 _ensure_nickname，保证 nickname 非空（默认或自设）
    """
    ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return []
    with _connect() as conn:
        rows = conn.execute("""
            SELECT u.id, u.nickname, lm.role
            FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE lm.ledger_id = ?
            ORDER BY (lm.role='owner') DESC, lm.id
        """, (ledger_id,)).fetchall()
        members = []
        for r in rows:
            nick = r["nickname"]
            if not nick or not nick.strip():
                # 空昵称 → 生成默认昵称并落库（_ensure_nickname 负责）
                # 这里在函数外单独落库，避免在 with conn 里再开连接
                nick = None
            members.append({"user_id": r["id"], "nickname": nick, "role": r["role"]})
        conn.commit()
    # 对空昵称成员在外面落默认昵称，并回填
    for m in members:
        if not m["nickname"] or not m["nickname"].strip():
            m["nickname"] = _ensure_nickname_by_id(m["user_id"])
    return members


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


def admin_rename_ledger(openid: str, new_name: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US3/T021：owner 修改账本名（权限按目标账本判定）。返回 (成功?, 消息)。
    评审：新名不能为空/纯空白（与 create_ledger 同规）。"""
    new_name = (new_name or "").strip()
    if not new_name:
        return False, "账本名不能为空，请给账本起个名字"
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能修改账本名"
    if is_ledger_deleted(ledger_id):
        return False, "该账本已被删除（只读），无法改名"
    with _connect() as conn:
        conn.execute("UPDATE ledgers SET name=? WHERE id=?", (new_name, ledger_id))
        conn.commit()
        return True, f"账本已改名为「{new_name}」"


def reset_invite_code(openid: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US9/T040（FR-013）：owner 重置账本口令。

    - 生成新口令，旧口令失效
    - 该账本所有 pending 申请置 expired（D5）
    返回 (成功?, 新口令 或 错误消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能重置口令"
    if is_ledger_deleted(ledger_id):
        return False, "该账本已被删除（只读），无法重置口令"
    with _connect() as conn:
        new_code = _gen_invite_code(conn)
        conn.execute("UPDATE ledgers SET invite_code=? WHERE id=?", (new_code, ledger_id))
        # 作废旧申请（D5）
        conn.execute(
            "UPDATE join_requests SET status='expired' WHERE ledger_id=? AND status='pending'",
            (ledger_id,),
        )
        conn.commit()
        return True, new_code


def leave_ledger(openid: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US8/T037（FR-012）：member 主动退出账本。

    - member 可退出（删除自己的成员关系，**不动历史账目**）
    - **owner 不能退出**（单 owner 模型，只能删账本，D7）
    返回 (成功?, 消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if is_ledger_deleted(ledger_id):
        return False, "该账本已被删除（只读），无法退出"
    with _connect() as conn:
        uid = _get_user_id_by_openid(conn, openid)
        if uid is None:
            return False, "用户不存在"
        row = conn.execute(
            "SELECT role FROM ledger_members WHERE ledger_id=? AND user_id=?", (ledger_id, uid)
        ).fetchone()
        if row is None:
            return False, "你不在这个账本里"
        if row["role"] == "owner":
            return False, "管理员不能退出账本（可删除账本）"
        conn.execute("DELETE FROM ledger_members WHERE ledger_id=? AND user_id=?", (ledger_id, uid))
        # FR-032/033/034：退出后当前账本确定回落（默认账本优先）
        _settle_current_ledger(conn, uid, ledger_id)
        conn.commit()
        return True, "已退出账本（你的历史账目仍保留在这个账本里）"


def is_default_ledger(openid: str, ledger_id: int) -> bool:
    """FR-014：判断账本是否为该用户的默认账本（供删除确认流程使用）。"""
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        return _get_default_ledger_id(conn, user_id) == ledger_id


def admin_delete_ledger(openid: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US6/FR-028~FR-029 + US3 FR-013：owner 软删除账本。

    - 已删除的账本不能重复删（FR-029：明确提示，**不覆盖原删除时间**）
    - 成员（含 owner 本人）的当前账本**立即回落**到各自默认账本（FR-033）——
      系统默认不把任何人留在已删除账本；查看历史须显式切入（只读）
    - 默认账本即被删账本的成员 → 自动重建新的空「我的账本」并更新指向
      （FR-013 方案b：锚点永不落空；调用方须先经 FR-014 确认）
    返回 (成功?, 消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能删除账本"
    with _connect() as conn:
        row = conn.execute("SELECT deleted_at FROM ledgers WHERE id=?", (ledger_id,)).fetchone()
        if row is None:
            return False, "账本不存在"
        if row["deleted_at"]:
            return False, "该账本已被删除（只读），无需重复删除"
        members = conn.execute(
            "SELECT user_id FROM ledger_members WHERE ledger_id=?", (ledger_id,)
        ).fetchall()
        # 必须在打删除标记**之前**判断谁以此为默认账本——否则 _get_default_ledger_id
        # 会因账本已删而返回 None，重建逻辑（FR-013）永远不触发
        default_holders = [
            m["user_id"]
            for m in members
            if _get_default_ledger_id(conn, m["user_id"]) == ledger_id
        ]
        now = datetime.now(SHANGHAI).isoformat()
        conn.execute("UPDATE ledgers SET deleted_at=? WHERE id=?", (now, ledger_id))
        caller_uid = _get_user_id_by_openid(conn, openid)
        rebuilt_for_caller = False
        rebuilt_any = False
        for m in members:
            uid = m["user_id"]
            if uid in default_holders:
                # FR-013：默认账本就是刚删的这个 → 重建空默认账本并改指向
                new_lid = _create_default_ledger(conn, uid, now)
                conn.execute("UPDATE users SET default_ledger_id=? WHERE id=?", (new_lid, uid))
                rebuilt_any = True
                if uid == caller_uid:
                    rebuilt_for_caller = True
            _settle_current_ledger(conn, uid, ledger_id)
        conn.commit()
        if rebuilt_for_caller:
            cur = conn.execute(
                "SELECT current_ledger_id FROM users WHERE id=?", (caller_uid,)
            ).fetchone()
            extra = ""
            if cur and cur["current_ledger_id"]:
                r = conn.execute(
                    "SELECT name FROM ledgers WHERE id=?", (cur["current_ledger_id"],)
                ).fetchone()
                if r:
                    extra = f"，当前账本已切到「{r['name']}」"
            return True, f"账本已删除（软删除，账目保留可追溯）。已自动重建新的空默认账本「我的账本」{extra}"
        if rebuilt_any:
            return True, "账本已删除（软删除，账目保留可追溯）。已为默认账本被删的成员自动重建「我的账本」"
        return True, "账本已删除（软删除，账目保留可追溯；成员当前账本已回落到各自默认账本）"
