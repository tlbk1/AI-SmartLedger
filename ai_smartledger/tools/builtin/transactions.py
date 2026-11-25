"""builtin/transactions — 交易域工具：查账（只读）/ 记账（整批事务）。"""
from typing import Optional

from langchain_core.tools import tool

from ... import db


def build(openid: str) -> list:
    """生成交易域工具（openid 闭包注入）。"""

    @tool
    def query_transactions(
        date_from: str,
        date_to: str,
        category: Optional[str] = None,
        type_filter: Optional[str] = None,
        limit: int = 5,
        order_by: str = "top_amount",
    ) -> str:
        """查询账单（只读），返回 JSON 信封：
        - totals：按类型聚合的**完整**合计（支出/收入分开），**不受 limit 影响**——
          回答"花了多少"类问题**必须**用 totals，**禁止**根据 records 自行求和；
        - records：金额最大的 ≤limit 笔明细（带记账人昵称），**仅供浏览**——只列了部分，
          与合计对不上是正常的；
        - total_count：命中的总笔数；order_by：本次明细排序。
        date_from/date_to 必填 YYYY-MM-DD；category/type_filter 可选。
        order_by：top_amount=金额倒序（默认，用于核对合计）| recent=时间倒序（"最近买了啥"）。
        limit 默认 5（上限 50）。"""
        ledger_id = db.get_user_ledger_id(openid)
        if ledger_id is None:
            return "你还没有加入任何账本，请先创建或加入一个账本。"
        import json
        limit = max(1, min(int(limit), 50))          # FR-006：钳制 [1,50]，防越界/撑爆上下文
        if order_by not in ("recent", "top_amount"):
            order_by = "top_amount"
        rows = db.query_by_ledger(
            ledger_id=ledger_id,
            date_from=date_from,
            date_to=date_to,
            category=category,
            type_filter=type_filter,
            limit=limit,
            order_by=order_by,
        )
        # FR-001/FR-008：合计来自 SQL 聚合（无 LIMIT），与上面截断的明细彻底解耦
        totals = db.sum_by_ledger(
            ledger_id, date_from, date_to, category=category, type_filter=type_filter,
        )
        payload: dict = {
            "records": rows,
            "totals": totals,
            "total_count": totals["expense"]["count"] + totals["income"]["count"],
            "order_by": order_by,
        }
        # T050（FR-014）/FR-008：已删账本 → **同一信封** + 只读标记（两种情形键结构一致）
        if db.is_ledger_deleted(ledger_id):
            info = db.get_ledger_info(ledger_id) or {"name": "该账本"}
            payload["ledger_deleted"] = True
            payload["ledger_name"] = info["name"]
            payload["notice"] = (
                f"⚠️ 账本「{info['name']}」已被删除，以下为历史账目（只读，不能再记账）。"
                "请在回复中明确提示用户。"
            )
        return json.dumps(payload, ensure_ascii=False)

    @tool
    def record_transactions(transactions: list) -> str:
        """一次性记录一笔或多笔账单（整批事务，全成全不记）。
        transactions 每项: type(expense/income), amount, category, note(可选), happened_at(可选)。
        空批次、非对象、缺金额或金额不可转数字会被拒绝并返回提示。
        确认信息才会记到账。用户未加入账本会提示先加。"""
        ledger_id = db.get_user_ledger_id(openid)
        if ledger_id is None:
            return "你还没有加入任何账本，请先创建或加入一个账本。"
        # T050（FR-014）：已删除账本只读——不能再记账
        if db.is_ledger_deleted(ledger_id):
            info = db.get_ledger_info(ledger_id) or {"name": "该账本"}
            return (
                f"不能记账：账本「{info['name']}」已被删除（只读，仅能查看历史账目）。"
                "请先切换到其他账本，或创建新账本。"
            )
        user_id = db.get_or_create_user(openid)
        if not transactions:
            return "没有要记的账目：transactions 为空。请向用户确认要记的内容和金额后重试。"
        txns = []
        for idx, t in enumerate(transactions, 1):
            if not isinstance(t, dict):
                return (
                    f"记账失败：第 {idx} 笔不是有效账目"
                    "（需要含 type/amount/category 的对象），请核对后重试。"
                )
            try:
                amount = float(t["amount"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return (
                    f"记账失败：第 {idx} 笔金额无效（{t.get('amount')!r}），"
                    "请向用户确认具体金额后重试。"
                )
            txns.append(
                db.Transaction(
                    type=str(t.get("type", "expense")),
                    amount=amount,
                    category=str(t.get("category", "其他")),
                    note=str(t.get("note", "")),
                    happened_at=str(t.get("happened_at") or "").strip(),
                )
            )
        ok = db.insert_many_for_ledger(ledger_id, user_id, txns)
        return f"✓ 已记 {len(txns)} 笔" if ok else "没记上，请重试"

    return [query_transactions, record_transactions]
