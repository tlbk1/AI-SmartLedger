"""内存状态与队列：幂等去重 / 对话记忆 / 澄清状态 / 待补发通知。"""
import threading
import time
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
from dataclasses import field
from . import SHANGHAI, _connect


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



_seen_cache: _TTLCache = _TTLCache()


_pending_cache: _TTLCache = _TTLCache()


_chat_history_cache: _TTLCache = _TTLCache()


_CHAT_HISTORY_MAX = 20


_CHAT_HISTORY_TTL = 1800


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
