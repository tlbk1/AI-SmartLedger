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

        # 软删除：ledgers.deleted_at（任务2，幂等补列）；NULL = 未删除
        _add_column_if_missing(conn, "ledgers", "deleted_at", "TEXT")

        # 任务3：users.current_ledger_id 显式记录用户当前账本（默认账本创建时设为它）
        _add_column_if_missing(conn, "users", "current_ledger_id", "INTEGER")

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


def query(
    date_from: str,
    date_to: str,
    category: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """【已退役，仅测试/tools 引用】参数化只读查询。
    注意：此函数不过滤 ledger_id，会查到「无主账」(ledger_id=NULL)。
    生产查账请用 query_by_ledger(按账本隔离)。保留仅因旧代码/测试引用。
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
    """根据 openid 找到用户，没有则创建。返回 user_id。

    任务2：新用户创建时自动建一个默认账本「我的账本」（该用户 role=owner）。
    任务3：把默认账本设为用户的 current_ledger_id（显式当前账本）。
    """
    with _connect() as conn:
        row = conn.execute("SELECT id, current_ledger_id FROM users WHERE openid=?", (openid,)).fetchone()
        if row:
            # 老用户（current_ledger_id 为 NULL）回填为其最近账本，保证有当前账本
            if row["current_ledger_id"] is None:
                # 找一个未软删除的账本回填；没有则建默认
                led = conn.execute("""
                    SELECT lm.ledger_id FROM ledger_members lm
                    JOIN ledgers l ON l.id=lm.ledger_id
                    WHERE lm.user_id=? AND l.deleted_at IS NULL
                    ORDER BY lm.id DESC LIMIT 1
                """, (row["id"],)).fetchone()
                if led:
                    conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (led["ledger_id"], row["id"]))
                    conn.commit()
            return row["id"]
        now = datetime.now(SHANGHAI).isoformat()
        cur = conn.execute(
            "INSERT INTO users (openid, nickname, created_at) VALUES (?, ?, ?)",
            (openid, nickname, now),
        )
        user_id = cur.lastrowid
        # constitution III：昵称永不为空——创建时即时生成默认昵称，
        # 否则 owner 看待审批列表/账目时该用户显示空名（e2e V1 发现的真 bug）
        if not (nickname or "").strip():
            nick = _gen_default_nickname(conn, user_id)
            conn.execute("UPDATE users SET nickname=? WHERE id=?", (nick, user_id))
        # 自动创建默认账本「我的账本」，用户是 owner，且设为当前账本
        invite = _gen_invite_code(conn)
        ledger_cur = conn.execute(
            "INSERT INTO ledgers (name, owner_user_id, invite_code, created_at) VALUES (?, ?, ?, ?)",
            ("我的账本", user_id, invite, now),
        )
        ledger_id = ledger_cur.lastrowid
        conn.execute(
            "INSERT INTO ledger_members (ledger_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
            (ledger_id, user_id, now),
        )
        conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (ledger_id, user_id))
        conn.commit()
        return user_id


def get_user_ledger_id(openid: str) -> Optional[int]:
    """根据 openid 找用户「当前账本」id（任务3：读 users.current_ledger_id）。

    T050/FR-014：**尊重用户的显式选择**——即使用户当前账本已被软删除也照原样返回
    （让他能查看历史账目，只读；写入在 insert/工具层拒绝）。
    仅当用户没有当前账本时，才兜底回退到最近加入的**未删除**账本。
    """
    with _connect() as conn:
        row = conn.execute("""
            SELECT u.current_ledger_id
            FROM users u
            WHERE u.openid = ?
        """, (openid,)).fetchone()
        if row and row["current_ledger_id"] is not None:
            # 账本存在即返回（含已软删除——用户显式切进去是为了看历史，只读）
            l = conn.execute("SELECT 1 FROM ledgers WHERE id=?", (row["current_ledger_id"],)).fetchone()
            if l:
                return row["current_ledger_id"]
        # 兜底：回退到最近加入的未软删除账本（不自动落进已删账本）
        fallback = conn.execute("""
            SELECT lm.ledger_id
            FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            JOIN ledgers l ON l.id = lm.ledger_id
            WHERE u.openid = ? AND l.deleted_at IS NULL
            ORDER BY lm.id DESC
            LIMIT 1
        """, (openid,)).fetchone()
        return fallback["ledger_id"] if fallback else None


def create_ledger(openid: str, name: str) -> tuple[bool, str]:
    """创建账本，创建者为 owner，并设为该用户的当前账本（任务3）。
    返回 (成功?, 结果消息或口令)。"""
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


def join_ledger(openid: str, invite_code: str) -> tuple[bool, str]:
    """凭口令加入账本，成为 member。任务3：加入后不自动切换当前账本。
    返回 (成功?, 结果消息)。"""
    user_id = get_or_create_user(openid)
    code = invite_code.strip().lower()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, name FROM ledgers WHERE invite_code=? AND deleted_at IS NULL", (code,)
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
        # 不自动切换 current_ledger_id；提示用户是否要切换
        return True, f"已加入账本「{name}」，当前记账账本未变。如需切换请说「切换到账本 {name}」"


def get_my_ledgers(openid: str) -> list[dict]:
    """列出用户加入的所有账本。

    US10/T050（FR-014）：**包含已软删除的账本**（带 `is_deleted` 标记）——
    原成员仍能查看其历史账目，前提是能发现它还在。
    """
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        rows = conn.execute("""
            SELECT l.id, l.name, l.invite_code, lm.role, lm.joined_at, l.deleted_at
            FROM ledger_members lm
            JOIN ledgers l ON l.id = lm.ledger_id
            WHERE lm.user_id = ?
            ORDER BY lm.id DESC
        """, (user_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["is_deleted"] = bool(d.get("deleted_at"))
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


def switch_ledger(openid: str, ledger_name: str) -> tuple[bool, str]:
    """任务3：切换当前账本（按名查找，必须是用户加入的）。

    US10/T050：允许切到**已删除**的账本（仅查看历史，只读），但明确提示已被删除。
    """
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        row = conn.execute("""
            SELECT l.id, l.name, l.deleted_at FROM ledger_members lm
            JOIN ledgers l ON l.id = lm.ledger_id
            WHERE lm.user_id = ? AND l.name = ?
            ORDER BY lm.id DESC LIMIT 1
        """, (user_id, ledger_name)).fetchone()
        if row is None:
            return False, f"你还没有加入叫「{ledger_name}」的账本"
        conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (row["id"], user_id))
        conn.commit()
        if row["deleted_at"]:
            return True, (
                f"已切到账本「{row['name']}」——**该账本已被删除**，只能查看历史账目（只读），不能再记账。"
            )
        return True, f"已切换到账本「{row['name']}」，后续记账/查账都在这个账本"


def get_current_ledger_name(openid: str) -> str:
    """取用户当前账本名（用于 agent 回复显示）。无则返回空串。"""
    ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return ""
    with _connect() as conn:
        row = conn.execute("SELECT name FROM ledgers WHERE id=?", (ledger_id,)).fetchone()
        return row["name"] if row else ""


# ─── 记账 / 查账（带账本隔离）───
# 保留旧 insert_many / query 签名供原有测试用；新增带 ledger 的版本供 agent 用。

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
            # 不返回 openid（即使表里有）；用记账人昵称兜底
            d.pop("openid", None)
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
    """T005：取用户「我的账本」这一默认账本的 id（用于删账本后回落）。

    默认账本 = 该用户为 owner、名字为「我的账本」且未删除的账本。
    """
    row = conn.execute("""
        SELECT id FROM ledgers
        WHERE owner_user_id = ? AND name = '我的账本' AND deleted_at IS NULL
        ORDER BY id LIMIT 1
    """, (user_id,)).fetchone()
    return row["id"] if row else None


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


def list_pending_joins(openid: str) -> list[dict]:
    """US2/T015（FR-009）：owner 视角列出本账本全部待审批申请（含申请人 nickname，不含 openid）。"""
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


def approve_join(openid: str, applicant_nickname: str) -> tuple[bool, str]:
    """US2/T016（FR-002）：owner 同意某申请人加入。

    按申请人昵称在本账本的 pending 申请中定位；pending → approved，并插入 member。
    返回 (成功?, 消息)。
    """
    ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能同意加入申请"
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
        # e2e V13 发现的隔离泄漏：被移除者的 current_ledger_id 若还指向此账本，
        # 他仍能以它为当前账本查账 → 回落到他自己的默认账本（与 leave_ledger 同规）
        cur2 = conn.execute("SELECT current_ledger_id FROM users WHERE id=?", (target_uid,)).fetchone()
        if cur2 and cur2["current_ledger_id"] == ledger_id:
            default_lid = _get_default_ledger_id(conn, target_uid)
            conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (default_lid, target_uid))
            conn.commit()
        return True, f"已移除成员 {target_nickname}"


def _gen_default_nickname(conn, user_id: int, excluded_nickname: str = "") -> str:
    """生成唯一默认昵称「账本成员 + 4位随机hex」。

    T002: 只要目标昵称在当前用户所有账本内不与任何成员 nickname 冲突即可。
    风格与 _gen_invite_code 一致（secrets 随机 + 防碰撞循环）。
    """
    import secrets
    for _ in range(50):
        nick = "账本成员 " + secrets.token_hex(2)  # 4 位 hex（token_hex(2)=4字符）
        if excluded_nickname and nick == excluded_nickname:
            continue
        # 账本内唯一校验：查这个用户所在的任意账本，是否已有成员用了该昵称
        dup = conn.execute(
            """
            SELECT 1 FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            JOIN ledgers l ON l.id = lm.ledger_id
            WHERE l.deleted_at IS NULL AND lm.user_id != ? AND u.nickname = ?
            LIMIT 1
            """,
            (user_id, nick),
        ).fetchone()
        if dup is None:
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


def set_nickname(openid: str, nickname: str) -> bool:
    """T005: 设置/更新用户昵称。空白/空昵称不生效（保留现有昵称）。"""
    if not nickname or not nickname.strip():
        return False
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        conn.execute("UPDATE users SET nickname=? WHERE id=?", (nickname.strip(), user_id))
        conn.commit()
        return True


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
    """US3/T021：owner 修改账本名（权限按目标账本判定）。返回 (成功?, 消息)。"""
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能修改账本名"
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
        # 当前账本若指向它 → 回落默认账本
        cur = conn.execute("SELECT current_ledger_id FROM users WHERE id=?", (uid,)).fetchone()
        if cur and cur["current_ledger_id"] == ledger_id:
            default_lid = _get_default_ledger_id(conn, uid)
            conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (default_lid, uid))
        conn.commit()
        return True, "已退出账本（你的历史账目仍保留在这个账本里）"


def admin_delete_ledger(openid: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US10/T044（FR-014）：owner 软删除账本 + 成员当前账本回落默认账本。

    - 软删除（打 deleted_at），账目保留可追溯
    - **该账本所有成员的 current_ledger_id 回落各自的默认账本**（D6）
    返回 (成功?, 消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能删除账本"
    with _connect() as conn:
        now = datetime.now(SHANGHAI).isoformat()
        conn.execute("UPDATE ledgers SET deleted_at=? WHERE id=?", (now, ledger_id))
        # 成员回落默认账本（D6）
        members = conn.execute(
            "SELECT user_id FROM ledger_members WHERE ledger_id=?", (ledger_id,)
        ).fetchall()
        for m in members:
            uid = m["user_id"]
            cur = conn.execute("SELECT current_ledger_id FROM users WHERE id=?", (uid,)).fetchone()
            if cur and cur["current_ledger_id"] == ledger_id:
                default_lid = _get_default_ledger_id(conn, uid)
                conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (default_lid, uid))
        conn.commit()
        return True, "账本已删除（软删除，账目保留可追溯；成员已回落到默认账本）"
