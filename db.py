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
_undelivered: dict[str, list[str]] = {}   # openid -> [未送达文本]（客服消息失败兜底）


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


# ──────────────────────────── 未送达消息队列 ────────────────────────────

def enqueue_undelivered(openid: str, text: str):
    """客服消息推送失败（超48h等），入队等下次补发。"""
    _undelivered.setdefault(openid, []).append(text)


def drain_undelivered(openid: str) -> list[str]:
    """取出并清空该用户所有未送达消息。"""
    return _undelivered.pop(openid, [])


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


def insert_many(txns: list[Transaction]) -> bool:
    """
    整批一个事务写入，全成全不记（需求 14.6）。
    返回 True = 成功，False = 失败。
    """
    if not txns:
        return True
    conn = _connect()
    try:
        with conn:  # 上下文管理器：正常退出 commit，异常 rollback
            for t in txns:
                conn.execute(
                    "INSERT INTO transactions (type, amount, category, note, happened_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    t.to_row(),
                )
        return True
    except Exception as e:
        # with conn 已 rollback
        import logging
        logging.getLogger(__name__).error("insert_many 失败: %s", e, exc_info=True)
        return False
    finally:
        conn.close()


def query(
    date_from: str,
    date_to: str,
    category: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """
    参数化只读查询（需求 14.4）。
    日期参数为 ISO 日期 'YYYY-MM-DD'，此处补全为半开区间。
    """
    # 日期边界补全（审查错误2：BETWEEN 会丢最后一天，改用 >= 和 <）
    # date_from 当天 00:00:00；date_to 次日 00:00:00（半开区间）
    dt_from = datetime.fromisoformat(date_from).replace(
        tzinfo=SHANGHAI, hour=0, minute=0, second=0
    )
    dt_to = datetime.fromisoformat(date_to).replace(
        tzinfo=SHANGHAI, hour=0, minute=0, second=0
    ) + timedelta(days=1)  # 次日凌晨

    ts_from = dt_from.isoformat()
    ts_to = dt_to.isoformat()

    sql = "SELECT * FROM transactions WHERE happened_at >= ? AND happened_at < ?"
    args: list = [ts_from, ts_to]

    if category:
        sql += " AND category = ?"
        args.append(category)
    if type_filter:
        sql += " AND type = ?"
        args.append(type_filter)

    sql += " ORDER BY happened_at DESC LIMIT ?"
    args.append(limit)

    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


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


def get_or_create_user(openid: str, nickname: str = "") -> int:
    """根据 openid 找到用户，没有则创建。返回 user_id。"""
    with _connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE openid=?", (openid,)).fetchone()
        if row:
            return row["id"]
        now = datetime.now(SHANGHAI).isoformat()
        cur = conn.execute(
            "INSERT INTO users (openid, nickname, created_at) VALUES (?, ?, ?)",
            (openid, nickname, now),
        )
        conn.commit()
        return cur.lastrowid


def get_user_ledger_id(openid: str) -> Optional[int]:
    """根据 openid 找用户最近加入的账本 id（多账本时归最近加入的）。
    找不到返回 None。"""
    with _connect() as conn:
        row = conn.execute("""
            SELECT lm.ledger_id
            FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE u.openid = ?
            ORDER BY lm.id DESC
            LIMIT 1
        """, (openid,)).fetchone()
        return row["ledger_id"] if row else None


def create_ledger(openid: str, name: str) -> tuple[bool, str]:
    """创建账本，创建者为 owner。返回 (成功?, 结果消息或口令)。"""
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        # 检查用户是否已有同名账本（可选，暂不强制）
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
        conn.commit()
        return True, invite


def join_ledger(openid: str, invite_code: str) -> tuple[bool, str]:
    """凭口令加入账本，成为 member。返回 (成功?, 结果消息)。"""
    user_id = get_or_create_user(openid)
    code = invite_code.strip().lower()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, name FROM ledgers WHERE invite_code=?", (code,)
        ).fetchone()
        if row is None:
            return False, "口令不存在，请核对"
        ledger_id, name = row["id"], row["name"]
        # 已在账本里？
        if conn.execute(
            "SELECT 1 FROM ledger_members WHERE ledger_id=? AND user_id=?",
            (ledger_id, user_id),
        ).fetchone():
            return True, f"你已在账本「{name}」里了"
        now = datetime.now(SHANGHAI).isoformat()
        conn.execute(
            "INSERT INTO ledger_members (ledger_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
            (ledger_id, user_id, now),
        )
        conn.commit()
        return True, f"已加入账本「{name}」"


def get_my_ledgers(openid: str) -> list[dict]:
    """列出用户加入的所有账本。"""
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        rows = conn.execute("""
            SELECT l.id, l.name, l.invite_code, lm.role, lm.joined_at
            FROM ledger_members lm
            JOIN ledgers l ON l.id = lm.ledger_id
            WHERE lm.user_id = ?
            ORDER BY lm.id DESC
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]


# ─── 记账 / 查账（带账本隔离）───
# 保留旧 insert_many / query 签名供原有测试用；新增带 ledger 的版本供 agent 用。

def insert_many_for_ledger(ledger_id: int, created_by_user_id: int, txns: list[Transaction]) -> bool:
    """整批写入事务，每笔带上 ledger_id + created_by_user_id。"""
    if not txns:
        return True
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
    """按账本隔离的查询。只查指定 ledger 的记录。"""
    dt_from = datetime.fromisoformat(date_from).replace(
        tzinfo=SHANGHAI, hour=0, minute=0, second=0
    )
    dt_to = datetime.fromisoformat(date_to).replace(
        tzinfo=SHANGHAI, hour=0, minute=0, second=0
    ) + timedelta(days=1)

    sql = "SELECT * FROM transactions WHERE ledger_id = ? AND happened_at >= ? AND happened_at < ?"
    args: list = [ledger_id, dt_from.isoformat(), dt_to.isoformat()]

    if category:
        sql += " AND category = ?"
        args.append(category)
    if type_filter:
        sql += " AND type = ?"
        args.append(type_filter)
    sql += " ORDER BY happened_at DESC LIMIT ?"
    args.append(limit)

    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


def migrate_old_data(openid: str, ledger_name: str = "我的账本") -> Optional[int]:
    """迁移旧数据（ledger_id 为 NULL 的记录）归到该 openid 名下的专属账本。
    返回记账本 id（若没有旧数据则只会创建一个空账本；成功返回 id）。"""
    with _connect() as conn:
        null_count = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE ledger_id IS NULL"
        ).fetchone()[0]
        if null_count == 0:
            return None

        user_id = get_or_create_user(openid)
        invite = _gen_invite_code(conn)
        now = datetime.now(SHANGHAI).isoformat()
        cur = conn.execute(
            "INSERT INTO ledgers (name, owner_user_id, invite_code, created_at) VALUES (?, ?, ?, ?)",
            (ledger_name, user_id, invite, now),
        )
        ledger_id = cur.lastrowid
        conn.execute(
            "INSERT INTO ledger_members (ledger_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
            (ledger_id, user_id, now),
        )
        # 把无语本归属的旧数据归入这个专属账本 + 标记 created_by_user_id
        conn.execute(
            "UPDATE transactions SET ledger_id = ?, created_by_user_id = ? WHERE ledger_id IS NULL",
            (ledger_id, user_id),
        )
        conn.commit()
        return ledger_id
