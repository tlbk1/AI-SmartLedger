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
