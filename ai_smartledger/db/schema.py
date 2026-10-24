"""DDL 与初始化：建表 / 补列 / 索引 / 存量回填（最终形态 + 幂等，无版本框架）。"""
import sqlite3
from . import _connect


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


def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    """若表里还没有某列，则添加（幂等，兼容旧库）。"""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
