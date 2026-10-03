"""记账：原子批量写入 / 明细查询 / 按类型聚合。"""
from typing import Optional
from datetime import datetime
from datetime import timedelta
from dataclasses import dataclass
from . import SHANGHAI, _connect
from .ledgers import is_ledger_deleted


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
                    (t.type, t.amount, t.category, t.note or "",
                     # happened_at 兜底 now：LLM 不传时间 = "现在"。绝不能落空串——
                     # 空串账目会从一切查询窗口消失（isodate 比较永远为 False），
                     # 而工具回复仍称"已记"→ 静默丢数据（created_at 同行已有 or now 兜底，对齐）
                     t.happened_at or now,
                     t.created_at or now, ledger_id, created_by_user_id),
                )
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("insert_many_for_ledger 失败: %s", e, exc_info=True)
        return False
    finally:
        conn.close()


def _date_bounds(date_from: str, date_to: str) -> tuple[str, str]:
    """FR-002（spec 004）：日期窗口归一（半开区间）——明细与聚合必须共用，禁止各写一份。

    date_from 当天 00:00 起；date_to 次日 00:00 止（含 date_to 全天）。
    """
    dt_from = datetime.fromisoformat(date_from).replace(
        tzinfo=SHANGHAI, hour=0, minute=0, second=0
    )
    dt_to = datetime.fromisoformat(date_to).replace(
        tzinfo=SHANGHAI, hour=0, minute=0, second=0
    ) + timedelta(days=1)
    return dt_from.isoformat(), dt_to.isoformat()


def query_by_ledger(
    ledger_id: int,
    date_from: str,
    date_to: str,
    category: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 20,
    order_by: str = "recent",
) -> list[dict]:
    """按账本隔离的查询。只查指定 ledger 的记录。

    US5/T027：附加 `created_by_nickname`（记账人昵称）——账目共享时展示"谁记的"。
    **绝不返回 openid**（constitution 原则 II）。
    FR-005（spec 004）：`order_by="recent"`（默认，时间倒序，保持旧语义）|
    `"top_amount"`（金额倒序，并列时按时间倒序）。
    **本函数只产明细**——合计请用 sum_by_ledger（无 LIMIT，不受本函数截断影响）。
    """
    dt_from, dt_to = _date_bounds(date_from, date_to)

    sql = (
        "SELECT t.*, u.nickname AS created_by_nickname "
        "FROM transactions t "
        "LEFT JOIN users u ON u.id = t.created_by_user_id "
        "WHERE t.ledger_id = ? AND t.happened_at >= ? AND t.happened_at < ?"
    )
    args: list = [ledger_id, dt_from, dt_to]

    if category:
        sql += " AND t.category = ?"
        args.append(category)
    if type_filter:
        sql += " AND t.type = ?"
        args.append(type_filter)
    if order_by == "top_amount":
        sql += " ORDER BY t.amount DESC, t.happened_at DESC LIMIT ?"
    else:
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


def sum_by_ledger(
    ledger_id: int,
    date_from: str,
    date_to: str,
    category: Optional[str] = None,
    type_filter: Optional[str] = None,
) -> dict:
    """FR-001（spec 004）：按类型聚合的**完整**合计——**无 LIMIT**，与明细条数彻底解耦。

    条件与 query_by_ledger 逐字一致（共用 _date_bounds，FR-002），防止口径漂移。
    返回 {"expense": {"count": n, "total": s}, "income": {...}}；
    空账本时各为 0（FR-004：不返回 NULL/缺键）。
    """
    dt_from, dt_to = _date_bounds(date_from, date_to)
    sql = (
        "SELECT type, COUNT(*) AS n, SUM(amount) AS s FROM transactions "
        "WHERE ledger_id = ? AND happened_at >= ? AND happened_at < ?"
    )
    args: list = [ledger_id, dt_from, dt_to]
    if category:
        sql += " AND category = ?"
        args.append(category)
    if type_filter:
        sql += " AND type = ?"
        args.append(type_filter)
    sql += " GROUP BY type"

    out: dict = {"expense": {"count": 0, "total": 0.0}, "income": {"count": 0, "total": 0.0}}
    with _connect() as conn:
        for r in conn.execute(sql, args).fetchall():
            if r["type"] in out:
                # round(2)：REAL 求和会出现 1249.999… 这类浮点尾差，金额展示前先归整
                out[r["type"]] = {"count": r["n"], "total": round(float(r["s"] or 0.0), 2)}
    return out
