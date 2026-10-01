"""spec 004 query-accuracy 测试：合计由 SQL 聚合、明细降级为 top-N、两条路径同口径。"""
import sys
import pathlib
import tempfile
import shutil

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import db


@pytest.fixture
def iso(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(db, "DB_PATH", pathlib.Path(tmp) / "t.db")
    db.init()
    yield
    shutil.rmtree(tmp, ignore_errors=True)


def _user_with_ledger(openid="o_u", name="我的账本"):
    db.get_or_create_user(openid)
    if name != "我的账本":
        db.create_ledger(openid, name)
    return db.get_user_ledger_id(openid)


def _seed_25_expense(openid="o_u"):
    """复现场景：25 笔支出、合计 ¥1250（一笔 ¥290 大额在月初）。"""
    lid = _user_with_ledger(openid)
    uid = db.get_or_create_user(openid)
    rows = [db.Transaction("expense", 290.0, "居住", "2026-09-01T09:00:00+08:00", "房租")]
    rows += [
        db.Transaction("expense", 40.0, "餐饮", f"2026-09-{i:02d}T12:00:00+08:00", f"第{i}天")
        for i in range(2, 26)
    ]
    db.insert_many_for_ledger(lid, uid, rows)
    return openid, lid


# ════════ 聚合：合计与明细彻底解耦（FR-001~FR-004 / SC-001/SC-003）════════

def test_sum_counts_all_rows_beyond_any_limit(iso):
    """SC-003 复现归零：25 笔 / ¥1250——聚合不受明细 limit 影响。"""
    openid, lid = _seed_25_expense()
    t = db.sum_by_ledger(lid, "2026-09-01", "2026-09-30")
    assert t["expense"]["count"] == 25
    assert t["expense"]["total"] == 1250.0        # 修复前按 limit=20 会算出 800
    # 对照：按明细（limit=20）求和只有 800 —— 证明两者确实解耦
    rows20 = db.query_by_ledger(lid, "2026-09-01", "2026-09-30", limit=20)
    assert len(rows20) == 20
    assert sum(r["amount"] for r in rows20) == 800.0


def test_sum_empty_ledger_returns_zeros_with_keys(iso):
    """FR-004：空账本时各为 0，且不缺键（不返回 NULL）。"""
    openid, lid = _seed_25_expense()
    t = db.sum_by_ledger(lid, "2026-08-01", "2026-08-31")   # 8 月无账目
    assert t == {"expense": {"count": 0, "total": 0.0}, "income": {"count": 0, "total": 0.0}}


def test_sum_splits_income_and_expense(iso):
    """FR-003 / SC-002：收入不得混入支出。"""
    openid, lid = _seed_25_expense()
    uid = db.get_or_create_user(openid)
    db.insert_many_for_ledger(lid, uid, [
        db.Transaction("income", 5000.0, "工资", "2026-09-05T09:00:00+08:00", "发工资"),
    ])
    t = db.sum_by_ledger(lid, "2026-09-01", "2026-09-30")
    assert t["expense"] == {"count": 25, "total": 1250.0}
    assert t["income"] == {"count": 1, "total": 5000.0}


def test_sum_respects_category_filter(iso):
    """FR-002：聚合与明细同口径（分类过滤生效）。"""
    openid, lid = _seed_25_expense()
    t = db.sum_by_ledger(lid, "2026-09-01", "2026-09-30", category="餐饮")
    assert t["expense"] == {"count": 24, "total": 960.0}    # 24 × 40


def test_sum_and_query_share_date_window(iso):
    """FR-002：聚合与明细共用同一日期窗口（含 date_to 全天，半开区间）。"""
    openid, lid = _seed_25_expense()
    # 上界取 9-25，当天 12:00 的账目必须被包含（明细与聚合一致）
    rows = db.query_by_ledger(lid, "2026-09-25", "2026-09-25", limit=50)
    t = db.sum_by_ledger(lid, "2026-09-25", "2026-09-25")
    assert len(rows) == 1 and t["expense"]["count"] == 1


# ════════ 明细：排序与钳制（FR-005~FR-007 / SC-004）════════

def test_query_top_amount_ordering(iso):
    """FR-005：top_amount 按金额降序（并列按时间倒序）。"""
    openid, lid = _seed_25_expense()
    rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30", limit=5, order_by="top_amount")
    assert [r["amount"] for r in rows] == [290.0, 40.0, 40.0, 40.0, 40.0]
    assert rows[0]["note"] == "房租"


def test_query_recent_is_still_default(iso):
    """FR-005：不传 order_by 时保持旧语义（时间倒序）——旧调用方不回归。"""
    openid, lid = _seed_25_expense()
    rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30", limit=5)
    days = [r["happened_at"][:10] for r in rows]
    assert days == ["2026-09-25", "2026-09-24", "2026-09-23", "2026-09-22", "2026-09-21"]


def test_tool_envelope_decoupled_and_limit_clamped(iso):
    """FR-006/FR-008：工具信封；records 截断而 totals 完整；limit 钳制 [1,50]。"""
    openid = "o_many"
    lid = _user_with_ledger(openid)
    uid = db.get_or_create_user(openid)
    db.insert_many_for_ledger(lid, uid, [
        db.Transaction("expense", float(i + 1), "其他",
                       f"2026-09-01T{(i // 60) % 24:02d}:{(i * 7) % 60:02d}:00+08:00", f"n{i}")
        for i in range(60)
    ])
    import agent, json
    tools = {t.name: t for t in agent.make_tools(openid)}

    data = json.loads(tools["query_transactions"].invoke(
        {"date_from": "2026-09-01", "date_to": "2026-09-30", "limit": 999}
    ))
    assert len(data["records"]) == 50                  # 上限 50（钳制）
    assert data["total_count"] == 60                   # 完整，不受 records 截断影响
    assert data["totals"]["expense"]["total"] == 1830.0  # 1+…+60
    assert data["order_by"] == "top_amount"
    assert data["records"][0]["amount"] == 60.0        # 金额降序

    data0 = json.loads(tools["query_transactions"].invoke(
        {"date_from": "2026-09-01", "date_to": "2026-09-30", "limit": 0}
    ))
    assert len(data0["records"]) == 1                  # 下限 1（钳制，不报错）

    data_r = json.loads(tools["query_transactions"].invoke(
        {"date_from": "2026-09-01", "date_to": "2026-09-30", "limit": 3, "order_by": "recent"}
    ))
    assert data_r["order_by"] == "recent" and len(data_r["records"]) == 3


def test_tool_deleted_ledger_same_envelope(iso):
    """FR-008 / SC-005：已删账本与正常查询键结构一致（同一信封 + 只读标记）。"""
    import agent, json
    db.get_or_create_user("o_owner")
    ok, code = db.create_ledger("o_owner", "我们家")
    lid = db.get_user_ledger_id("o_owner")
    db.set_nickname("o_b", "小王")
    db.apply_join("o_b", code)
    db.approve_join("o_owner", "小王")
    uid_b = db.get_or_create_user("o_b")
    db.insert_many_for_ledger(lid, uid_b, [
        db.Transaction("expense", 50.0, "餐饮", "2026-09-05T12:00:00+08:00", "B记的"),
    ])
    # 正常查询的键结构
    tools_b = {t.name: t for t in agent.make_tools("o_b")}
    normal = json.loads(tools_b["query_transactions"].invoke(
        {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    ))
    core = {"records", "totals", "total_count", "order_by"}
    assert set(normal.keys()) == core
    # 删账本后显式切回去查历史：核心键不变，仅追加只读标记
    db.admin_delete_ledger("o_owner", lid)
    ok_sw, _ = db.switch_ledger("o_b", f"#{lid}")
    assert ok_sw
    deleted = json.loads(tools_b["query_transactions"].invoke(
        {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    ))
    assert set(deleted.keys()) == core | {"ledger_deleted", "ledger_name", "notice"}
    assert deleted["ledger_deleted"] is True and "已被删除" in deleted["notice"]
    assert deleted["totals"]["expense"]["total"] == 50.0    # 已删账本的合计照算
    for k in core:
        assert k in deleted


# ════════ 两条路径同口径（FR-010~FR-012 / US3）════════

def test_graph_fallback_passes_sql_totals(iso, monkeypatch):
    """FR-012：agent 失败走兜底路由时，总结必须拿到 SQL 聚合的 totals（不再心算）。"""
    import graph, llm
    from tools import QueryParams
    openid, lid = _seed_25_expense("o_fb")
    uid = db.get_or_create_user(openid)
    db.insert_many_for_ledger(lid, uid, [
        db.Transaction("income", 5000.0, "工资", "2026-09-05T09:00:00+08:00", "发工资"),
    ])
    monkeypatch.setattr(llm, "classify_intent", lambda content, now: "query")
    monkeypatch.setattr(llm, "extract_query_params", lambda content, now: QueryParams(
        date_from="2026-09-01", date_to="2026-09-30"))
    captured = {}

    def fake_summarize(rows, question, now, totals, ledger_deleted=False):
        captured["totals"] = totals
        captured["rows"] = rows
        return "ok"

    monkeypatch.setattr(llm, "summarize_query_result", fake_summarize)
    out = graph._classify_and_route(
        {"openid": openid, "content": "这个月花了多少", "now": "2026-09-30T20:00:00+08:00"})
    assert out == "ok"
    # totals 是完整聚合（25 笔/1250 + 收入 5000），而不是被 limit 截断的明细之和
    assert captured["totals"]["expense"] == {"count": 25, "total": 1250.0}
    assert captured["totals"]["income"] == {"count": 1, "total": 5000.0}


def test_totals_param_is_required(iso):
    """FR-010：totals 必填——漏传立刻 TypeError，不会悄悄退回心算。"""
    import llm
    with pytest.raises(TypeError):
        llm.summarize_query_result([], "上月花了多少", "2026-09-10T10:00:00+08:00")


# ════════ happened_at 缺省兜底（评审：静默丢数据）════════

def test_record_without_happened_at_defaults_to_now(iso):
    """不传 happened_at → 落库为当前时间，绝不能是空串（空串=从一切查询窗口消失，
    但工具回复仍称"已记"——假成功）。created_at 在 db 层有 or now 兜底，happened_at 对齐。"""
    import agent, json
    db.get_or_create_user("o_nots")
    tools = {t.name: t for t in agent.make_tools("o_nots")}
    out = tools["record_transactions"].invoke(
        {"transactions": [{"type": "expense", "amount": 32.0, "category": "餐饮"}]}
    )
    assert "已记" in out
    lid = db.get_user_ledger_id("o_nots")
    rows = db.query_by_ledger(lid, "2020-01-01", "2040-12-31", limit=50)
    assert len(rows) == 1, "缺省时间的账目必须可查（落到当前时间）"
    assert rows[0]["happened_at"] != "" and "T" in rows[0]["happened_at"]


def test_record_with_explicit_happened_at_kept(iso):
    """显式传时间不被覆盖（兜底只作用于缺省/空白）。"""
    import agent
    db.get_or_create_user("o_exp")
    tools = {t.name: t for t in agent.make_tools("o_exp")}
    tools["record_transactions"].invoke(
        {"transactions": [{"type": "expense", "amount": 50.0, "category": "娱乐",
                           "happened_at": "2026-08-31T19:00:00+08:00"}]}
    )
    lid = db.get_user_ledger_id("o_exp")
    rows = db.query_by_ledger(lid, "2026-08-31", "2026-08-31", limit=10)
    assert len(rows) == 1 and rows[0]["happened_at"].startswith("2026-08-31")


def test_record_blank_happened_at_also_defaulted(iso):
    """LLM 传了空白字符串/None 也要兜底（等价于没传）。"""
    import agent
    db.get_or_create_user("o_blank")
    tools = {t.name: t for t in agent.make_tools("o_blank")}
    out = tools["record_transactions"].invoke(
        {"transactions": [
            {"type": "expense", "amount": 1.0, "category": "其他", "happened_at": "  "},
            {"type": "expense", "amount": 2.0, "category": "其他", "happened_at": None},
        ]}
    )
    assert "已记 2 笔" in out
    lid = db.get_user_ledger_id("o_blank")
    rows = db.query_by_ledger(lid, "2020-01-01", "2040-12-31", limit=10)
    assert len(rows) == 2, "空白时间的账目不得静默丢失"
    assert all(r["happened_at"] for r in rows)
