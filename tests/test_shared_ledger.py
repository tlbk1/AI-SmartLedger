"""US1 申请加入（审批制）测试：T006-T009。"""
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


def _setup_owner_with_ledger():
    """建 owner + 账本，返回 (owner_openid, invite_code, ledger_id)。"""
    db.get_or_create_user("o_owner")
    ok, code = db.create_ledger("o_owner", "我们家")
    lid = db.get_user_ledger_id("o_owner")
    return "o_owner", code, lid


# ---- T006: 申请后 status=pending ----
def test_apply_join_pending(iso):
    owner, code, lid = _setup_owner_with_ledger()
    ok, msg, _ = db.apply_join("o_applicant", code)
    assert ok, msg
    assert db.get_my_join_status("o_applicant", lid) == "pending"


# ---- T007: 无效口令不产生申请 ----
def test_invalid_invite_code(iso):
    _setup_owner_with_ledger()
    ok, msg, _ = db.apply_join("o_applicant", "bogus123")
    assert not ok
    assert "口令" in msg


# ---- T008: 重复申请幂等 ----
def test_duplicate_apply_idempotent(iso):
    owner, code, lid = _setup_owner_with_ledger()
    db.apply_join("o_applicant", code)
    db.apply_join("o_applicant", code)  # 再申请
    import sqlite3
    c = sqlite3.connect(str(db.DB_PATH)); c.row_factory = sqlite3.Row
    n = c.execute(
        "SELECT COUNT(*) n FROM join_requests WHERE ledger_id=? AND user_id=("
        "SELECT id FROM users WHERE openid='o_applicant')", (lid,)
    ).fetchone()["n"]
    c.close()
    assert n == 1, "重复申请应只有一条"


# ---- T009: pending 时看不到账本数据 ----
def test_applicant_cannot_see_ledger_data(iso):
    owner, code, lid = _setup_owner_with_ledger()
    db.apply_join("o_applicant", code)
    # 申请人不是成员
    assert not db.is_ledger_member("o_applicant", lid)
    # 申请人的当前账本不是这个账本（看不到）
    assert db.get_user_ledger_id("o_applicant") != lid


# ════════ US2: owner 同意 ════════

# ---- T012: owner 查待审批（含 nickname）----
def test_pending_list_for_owner(iso):
    owner, code, lid = _setup_owner_with_ledger()
    db.set_nickname("o_applicant", "小王")
    db.apply_join("o_applicant", code)
    pending = db.list_pending_joins(owner)
    assert len(pending) == 1
    assert pending[0]["nickname"] == "小王"
    # 不含 openid
    assert "openid" not in pending[0]


# ---- T013: 同意后成为 member ----
def test_approve_join_makes_member(iso):
    owner, code, lid = _setup_owner_with_ledger()
    db.set_nickname("o_applicant", "小王")
    db.apply_join("o_applicant", code)
    ok, msg = db.approve_join(owner, "小王")
    assert ok, msg
    assert db.is_ledger_member("o_applicant", lid)
    assert db.get_my_join_status("o_applicant", lid) == "approved"


# ---- T014: 非 owner 不能审批 ----
def test_non_owner_cannot_approve(iso):
    owner, code, lid = _setup_owner_with_ledger()
    # 让 o_other 也进账本当 member（直接插入模拟）
    import sqlite3
    db.get_or_create_user("o_other")
    c = sqlite3.connect(str(db.DB_PATH))
    c.execute("INSERT INTO ledger_members (ledger_id,user_id,role,joined_at) "
              "SELECT ?, id, 'member', '2026-01-01' FROM users WHERE openid='o_other'", (lid,))
    c.commit(); c.close()
    db.switch_ledger("o_other", "我们家")
    ok, msg = db.approve_join("o_other", "小王")
    assert not ok
    assert "管理员" in msg


# ════════ US3: 权限边界 ════════

# ---- T019: 权限按目标账本判定 ----
def test_admin_checked_by_target_ledger(iso):
    # owner 在 L1 是 owner；另建 L2 让他当 member
    db.get_or_create_user("o_owner")
    ok, code1 = db.create_ledger("o_owner", "账本A")
    l1 = db.get_user_ledger_id("o_owner")
    # 另一个用户建 L2，把 o_owner 拉进去当 member
    db.get_or_create_user("o_other")
    ok, code2 = db.create_ledger("o_other", "账本B")
    db.set_nickname("o_owner", "老王")
    db.apply_join("o_owner", code2)
    db.approve_join("o_other", "老王")
    l2 = db.get_user_ledger_id("o_other")
    # o_owner 在 L2 是 member → 对 L2 不是 admin
    assert db.is_ledger_admin("o_owner", l1) is True
    assert db.is_ledger_admin("o_owner", l2) is False


# ---- T020: member 不能管理 ----
def test_member_cannot_manage(iso):
    owner, code, lid = _setup_owner_with_ledger()
    db.set_nickname("o_applicant", "小王")
    db.apply_join("o_applicant", code)
    db.approve_join(owner, "小王")
    db.switch_ledger("o_applicant", "我们家")
    ok, msg = db.admin_remove_member("o_applicant", "老板")
    assert not ok
    assert "管理员" in msg


# ════════ US4: 成员移除（保留历史账目）════════

def _ledger_with_member_and_txn():
    """建 owner+账本+B(成员)+B记的一笔账。返回 (owner, B, ledger_id)。"""
    db.get_or_create_user("o_owner")
    ok, code = db.create_ledger("o_owner", "我们家")
    lid = db.get_user_ledger_id("o_owner")
    db.set_nickname("o_b", "小王")
    db.apply_join("o_b", code)
    db.approve_join("o_owner", "小王")
    # B 记一笔（注意 Transaction 字段顺序：type, amount, category, happened_at, note）
    uid_b = db.get_or_create_user("o_b")
    db.insert_many_for_ledger(lid, uid_b, [
        db.Transaction("expense", 88.0, "餐饮", "2026-09-01T12:00:00+08:00", "AA")
    ])
    return "o_owner", "o_b", lid


# ---- T022: 移除保留历史账目 ----
def test_remove_member_keeps_transactions(iso):
    owner, b, lid = _ledger_with_member_and_txn()
    from datetime import datetime as _dt
    ok, msg = db.admin_remove_member(owner, "小王")
    assert ok, msg
    assert not db.is_ledger_member(b, lid)
    # B 的账目仍在
    rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30")
    assert any(r["amount"] == 88.0 for r in rows), "移除成员不应删除其账目"


# ---- T023: owner 不可被移除 ----
def test_cannot_remove_owner(iso):
    owner, b, lid = _ledger_with_member_and_txn()
    db.set_nickname(owner, "老板")
    ok, msg = db.admin_remove_member(owner, "老板")
    assert not ok
    assert "owner" in msg or "管理员" in msg


# ════════ US5: 账目共享可见 + 记账人昵称 ════════

def _two_members_two_txns():
    """A(owner)、B(member) 同账本，各记一笔。返回 (A, B, ledger_id)。"""
    db.get_or_create_user("o_a")
    ok, code = db.create_ledger("o_a", "我们家")
    lid = db.get_user_ledger_id("o_a")
    db.set_nickname("o_a", "老板")
    db.set_nickname("o_b", "小王")
    db.apply_join("o_b", code)
    db.approve_join("o_a", "小王")
    uid_a = db.get_or_create_user("o_a")
    uid_b = db.get_or_create_user("o_b")
    db.insert_many_for_ledger(lid, uid_a, [
        db.Transaction("expense", 35.0, "餐饮", "2026-09-02T12:00:00+08:00", "老板的午饭")
    ])
    db.insert_many_for_ledger(lid, uid_b, [
        db.Transaction("expense", 88.0, "餐饮", "2026-09-02T18:00:00+08:00", "小王的晚饭")
    ])
    return "o_a", "o_b", lid


# ---- T025: 成员能看到全部账目（共享）----
def test_shared_visible_all_members(iso):
    a, b, lid = _two_members_two_txns()
    rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30")
    amounts = {r["amount"] for r in rows}
    assert 35.0 in amounts and 88.0 in amounts, "成员应能看到账本内全部账目（含他人记的）"


# ---- T026: 查账含记账人昵称，不含 openid ----
def test_query_includes_creator_nickname(iso):
    a, b, lid = _two_members_two_txns()
    rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30")
    assert rows
    for r in rows:
        assert "created_by_nickname" in r
        assert "openid" not in r
    names = {r["created_by_nickname"] for r in rows}
    assert "老板" in names and "小王" in names, "应能看出是谁记的"


# ════════ US6: 默认账本与多账本 ════════

def test_new_user_has_default_ledger(iso):
    db.get_or_create_user("o_new")
    lid = db.get_user_ledger_id("o_new")
    assert lid is not None, "新用户应自动有默认账本"
    assert db.is_ledger_admin("o_new", lid), "默认账本里新用户应是 owner"


def test_join_new_ledger_does_not_switch(iso):
    db.get_or_create_user("o_a")
    ok, code = db.create_ledger("o_a", "我们家")
    my_default = db.get_user_ledger_id("o_a")   # 刚建的账本是当前
    # 换个用户
    db.get_or_create_user("o_b")
    db.set_nickname("o_b", "小王")
    default_b = db.get_user_ledger_id("o_b")
    db.apply_join("o_b", code)
    db.approve_join("o_a", "小王")
    # 加入后 o_b 当前账本不应自动切换
    assert db.get_user_ledger_id("o_b") == default_b


# ════════ US8: member 主动退出 ════════

def test_member_can_leave(iso):
    owner, b, lid = _ledger_with_member_and_txn()
    db.switch_ledger(b, "我们家")
    ok, msg = db.leave_ledger(b)
    assert ok, msg
    assert not db.is_ledger_member(b, lid)
    # 历史账目保留
    rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30")
    assert any(r["amount"] == 88.0 for r in rows)


def test_owner_cannot_leave(iso):
    db.get_or_create_user("o_a")
    db.create_ledger("o_a", "我们家")
    ok, msg = db.leave_ledger("o_a")
    assert not ok
    assert "管理员" in msg


# ════════ US9: 口令重置 ════════

def test_reset_invite_code_expires_pending(iso):
    owner, code, lid = _setup_owner_with_ledger()
    db.set_nickname("o_applicant", "小王")
    db.apply_join("o_applicant", code)
    assert db.get_my_join_status("o_applicant", lid) == "pending"
    # owner 重置口令
    ok, new_code = db.reset_invite_code(owner)
    assert ok
    assert new_code != code
    # 旧 pending 申请作废
    assert db.get_my_join_status("o_applicant", lid) == "expired"
    # 旧口令失效
    ok2, msg2, _ = db.apply_join("o_other", code)
    assert not ok2


def test_non_owner_cannot_reset_code(iso):
    owner, code, lid = _setup_owner_with_ledger()
    db.set_nickname("o_applicant", "小王")
    db.apply_join("o_applicant", code)
    db.approve_join(owner, "小王")
    db.switch_ledger("o_applicant", "我们家")
    ok, msg = db.reset_invite_code("o_applicant")
    assert not ok
    assert "管理员" in msg


# ════════ US10: 删账本后成员处理 ════════

def test_delete_ledger_falls_back_to_default(iso):
    owner, b, lid = _ledger_with_member_and_txn()
    db.switch_ledger(b, "我们家")
    assert db.get_user_ledger_id(b) == lid
    ok, msg = db.admin_delete_ledger(owner, lid)
    assert ok, msg
    # B 应回落到自己的默认账本
    assert db.get_user_ledger_id(b) != lid
    assert db.get_user_ledger_id(b) is not None


def test_deleted_ledger_history_still_queryable(iso):
    owner, b, lid = _ledger_with_member_and_txn()
    db.admin_delete_ledger(owner, lid)
    # 历史账目仍可查
    rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30")
    assert any(r["amount"] == 88.0 for r in rows)
    # 账本标记为已删除
    import sqlite3
    c = sqlite3.connect(str(db.DB_PATH))
    dt = c.execute("SELECT deleted_at FROM ledgers WHERE id=?", (lid,)).fetchone()[0]
    c.close()
    assert dt is not None


# ════════ US7: 指定账本的管理操作（显式 ledger_id）════════

def test_admin_on_explicit_ledger_not_current(iso):
    """owner 有 A/B 两个账本，当前在 A，也能显式管理 B（D2 显式账本判定）。"""
    db.get_or_create_user("o_owner")
    db.create_ledger("o_owner", "账本A")
    lid_a = db.get_user_ledger_id("o_owner")
    ok, code_b = db.create_ledger("o_owner", "账本B")
    lid_b = db.get_user_ledger_id("o_owner")
    assert lid_b != lid_a
    # owner 当前在 B（最近创建）。给 A 提交一个申请，再切回 A
    db.switch_ledger("o_owner", "账本A")
    db.set_nickname("o_applicant", "小王")
    code_a = db.get_my_ledgers("o_owner")
    # 直接用 A 的口令申请：从库里取 A 的口令
    import sqlite3
    c = sqlite3.connect(str(db.DB_PATH)); c.row_factory = sqlite3.Row
    code_a = c.execute("SELECT invite_code FROM ledgers WHERE id=?", (lid_a,)).fetchone()["invite_code"]
    c.close()
    db.apply_join("o_applicant", code_a)
    # owner 当前账本是 A → 直接同意（单账本语境明确）
    ok, msg = db.approve_join("o_owner", "小王")
    assert ok, msg
    assert db.is_ledger_member("o_applicant", lid_a)
    # 显式管理 B：重置 B 口令（不切账本）
    ok, new_code = db.reset_invite_code("o_owner", lid_b)
    assert ok
    assert new_code != code_b


def test_member_cannot_admin_other_ledger(iso):
    """member 在自己账本无管理权，也不能靠切换身份越权管别的账本。"""
    owner, b, lid = _ledger_with_member_and_txn()
    # B 是 member：不能移除人
    db.switch_ledger(b, "我们家")
    ok, msg = db.admin_remove_member(b, "老板")
    assert not ok
    assert "管理员" in msg


# ════════ e2e 回归：昵称即时生成 + 移除后回落（隔离泄漏）════════

def test_new_user_gets_default_nickname_immediately(iso):
    """e2e V1 回归：新用户创建时昵称非空（owner 能按名审批，constitution III）。"""
    uid = db.get_or_create_user("o_fresh")
    import sqlite3
    c = sqlite3.connect(str(db.DB_PATH))
    nick = c.execute("SELECT nickname FROM users WHERE id=?", (uid,)).fetchone()[0]
    c.close()
    assert (nick or "").startswith("账本成员"), f"创建时应即时生成默认昵称，实际='{nick}'"


def test_removed_member_current_ledger_falls_back(iso):
    """e2e V10 回归：被移除者的 current_ledger_id 应回落默认账本（堵隔离泄漏：
    否则被移除者仍能以原账本为当前账本查账）。"""
    owner, b, lid = _ledger_with_member_and_txn()
    db.switch_ledger(b, "我们家")
    assert db.get_user_ledger_id(b) == lid
    ok, msg = db.admin_remove_member(owner, "小王")
    assert ok, msg
    assert db.get_user_ledger_id(b) != lid, "被移除者当前账本不应再指向原账本"
    assert db.get_user_ledger_id(b) is not None


# ════════ T049: owner 对话时自动提示待审批（兜底保底）════════

def test_pending_hint_empty_for_non_owner(iso):
    """T049（US2/AC4）：非 owner 不产生提示（不能向申请人泄露申请动态）。"""
    _setup_owner_with_ledger()
    db.apply_join("o_applicant", db.get_my_ledgers("o_owner") and _code_of("我们家"))
    import agent
    assert agent._pending_joins_hint("o_applicant") == ""
    assert agent._pending_joins_hint("o_unknown_never_joined") == ""


def test_pending_hint_for_owner_with_pending(iso):
    """T049（US2/AC4）：owner 有待审批 → 提示含条数与申请人昵称。"""
    owner, code, lid = _setup_owner_with_ledger()
    db.set_nickname("o_applicant", "小王")
    db.apply_join("o_applicant", code)
    import agent
    hint = agent._pending_joins_hint(owner)
    assert "待审批" in hint and "1" in hint and "小王" in hint


def test_pending_hint_empty_when_no_pending(iso):
    """T049：owner 无待审批 → 不注入提示（不给 LLM 噪音）。"""
    owner, code, lid = _setup_owner_with_ledger()
    import agent
    assert agent._pending_joins_hint(owner) == ""


# ════════ T050: 已删除账本（历史只读 + 明确提示）════════

def _code_of(ledger_name: str, owner_openid: str = "o_owner") -> str:
    """取某账本的口令（测试辅助）。"""
    for l in db.get_my_ledgers(owner_openid):
        if l["name"] == ledger_name:
            return l["invite_code"]
    raise AssertionError(f"没找到账本 {ledger_name}")


def _deleted_ledger_with_history():
    """建「临时账」→ B 加入并记一笔 → owner 删账本。返回 (owner, b, lid, 默认账本id)。"""
    owner, code, lid = _setup_owner_with_ledger()
    # B 有自己的默认账本（作为回落目标），先记下来
    db.get_or_create_user("o_b")
    lid_b_default = db.get_user_ledger_id("o_b")
    db.set_nickname("o_b", "小王")
    db.apply_join("o_b", code)
    db.approve_join(owner, "小王")
    db.switch_ledger("o_b", "我们家")
    uid_b = db.get_or_create_user("o_b")
    db.insert_many_for_ledger(lid, uid_b, [
        db.Transaction(type="expense", amount=50.0, category="餐饮",
                       happened_at="2026-09-05T12:00:00+08:00", note="B记的"),
    ])
    ok, msg = db.admin_delete_ledger(owner, lid)
    assert ok, msg
    return owner, "o_b", lid, lid_b_default


def test_deleted_ledger_visible_in_list_and_switchable(iso):
    """T050（FR-014/US10-AC2）：已删账本仍在"我的账本"里（带标记），且可切进去看历史。"""
    owner, b, lid, lid_b_default = _deleted_ledger_with_history()
    # ① 删除后回落默认账本
    assert db.get_user_ledger_id(b) == lid_b_default
    # ② 列表里仍在，且标记 is_deleted
    mine = {l["name"]: l for l in db.get_my_ledgers(b)}
    assert "我们家" in mine, "已删账本应保留在列表（否则历史看不了）"
    assert mine["我们家"]["is_deleted"] is True
    # ③ 可以切进去（只读），并明确提示已删除
    ok, msg = db.switch_ledger(b, "我们家")
    assert ok, msg
    assert "已被删除" in msg
    assert db.get_user_ledger_id(b) == lid
    # ④ 历史账目仍查得到
    rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30")
    assert len(rows) == 1 and rows[0]["amount"] == 50.0
    assert db.is_ledger_deleted(lid) is True


def test_deleted_ledger_rejects_writes(iso):
    """T050（FR-014）：已删账本只读——db 层拒绝写入。"""
    owner, b, lid, _ = _deleted_ledger_with_history()
    ok = db.insert_many_for_ledger(lid, db.get_or_create_user(b), [
        db.Transaction(type="expense", amount=1.0, category="其他",
                       happened_at="2026-09-06T12:00:00+08:00", note="不应写入"),
    ])
    assert ok is False, "已删除账本不应接受新账目"
    assert len(db.query_by_ledger(lid, "2026-09-01", "2026-09-30")) == 1, "账目数不应变化"


def test_agent_tool_refuses_record_into_deleted_ledger(iso):
    """T050：agent 工具层给出友好拒绝（不是静默失败）。"""
    owner, b, lid, _ = _deleted_ledger_with_history()
    db.switch_ledger(b, "我们家")          # 当前账本 = 已删账本
    import agent
    tools = {t.name: t for t in agent.make_tools(b)}
    out = tools["record_transactions"].invoke(
        {"transactions": [{"type": "expense", "amount": 1.0, "category": "其他"}]}
    )
    assert "已被删除" in out and "不能记账" in out


def test_agent_query_tool_marks_deleted_ledger(iso):
    """T050（US10-AC2）：查已删账本时，工具返回带 ledger_deleted 标记 + 提示语。"""
    owner, b, lid, _ = _deleted_ledger_with_history()
    db.switch_ledger(b, "我们家")
    import agent, json
    tools = {t.name: t for t in agent.make_tools(b)}
    out = tools["query_transactions"].invoke(
        {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    )
    data = json.loads(out)
    assert data["ledger_deleted"] is True
    assert "已被删除" in data["notice"]
    assert len(data["records"]) == 1


def test_summarize_deleted_empty_is_deterministic(iso):
    """T050：已删账本 + 无记录 → 确定性文案（不调 LLM）。"""
    import llm
    out = llm.summarize_query_result([], "上月花了多少", "2026-09-10T10:00:00+08:00",
                                     ledger_deleted=True)
    assert "已被删除" in out
    # 普通空结果保持原样
    assert llm.summarize_query_result([], "上月花了多少", "2026-09-10T10:00:00+08:00") == "没有查到相关记录。"


# ════════ T032/T033: US7 管理操作须明确指定账本 ════════
# 说明：US7 的"要不要先问用户"是 LLM 行为，由 prompt 规则驱动；测试从两处断言：
#   ① prompt 里确实有该规则（单账本免确认 / 多账本须指定）
#   ② 就算 LLM 选错账本，db 层按【目标账本】判权限也会拦住（纵深防御）

def test_single_ledger_no_confirm_rule_present(iso):
    """T032（US7/AC3 + D8）：单账本时直接执行——prompt 有该规则，且单账本下管理操作可用。"""
    import agent
    assert "只有一个账本时直接执行" in agent.AGENT_SYSTEM_PROMPT
    owner, code, lid = _setup_owner_with_ledger()
    # 只有一个账本 → 显式指定它就是它，操作成功（无需二次确认）
    assert db.is_ledger_admin(owner, lid) is True
    ok, msg = db.admin_rename_ledger(owner, "咱家", lid)
    assert ok, msg


def test_multi_ledger_requires_target(iso):
    """T033（US7/AC1+AC2 + FR-004）：多账本须指定；指错账本会被 db 层拒绝。"""
    import agent
    # ① prompt 规则存在
    assert "先问" in agent.AGENT_SYSTEM_PROMPT and "哪个账本" in agent.AGENT_SYSTEM_PROMPT
    # ② 用户有 A、B 两个账本
    db.get_or_create_user("o_multi")
    db.create_ledger("o_multi", "账本A")
    lid_a = db.get_user_ledger_id("o_multi")
    ok, _ = db.create_ledger("o_multi", "账本B")
    lid_b = db.get_user_ledger_id("o_multi")
    assert lid_a != lid_b
    # 对 A 改名 → 只有 A 变，B 不受影响（明确作用于目标账本）
    ok, msg = db.admin_rename_ledger("o_multi", "A改名", lid_a)
    assert ok, msg
    names = {l["id"]: l["name"] for l in db.get_my_ledgers("o_multi")}
    assert names[lid_a] == "A改名" and names[lid_b] == "账本B"
    # ③ 别人的账本：X 是 L1 owner，但对 L2 无管理权（按目标账本判角色）
    db.get_or_create_user("o_stranger")
    ok2, other_lid = db.create_ledger("o_stranger", "外人的账本")
    assert db.is_ledger_admin("o_multi", other_lid) is False
    ok3, msg3 = db.admin_delete_ledger("o_multi", other_lid)
    assert not ok3, "对非自己 owner 的账本应拒绝管理操作"


# ════════ FR-038：申请通知必须送达【本次申请】的 owner（评审缺陷回归）════════
#
# 旧实现用 get_my_latest_pending_join（该用户最新一条 pending）定位通知对象，
# 用户先后申请多本账时，重新申请第一本会把通知送给第二本的 owner，第一本的
# owner 永远收不到——「申请后 owner 必可达」失效。

def test_apply_join_returns_applied_ledger_id(iso):
    """FR-038：apply_join 必须回报【本次申请】的账本 id（通知定位的唯一依据）。"""
    owner, code, lid = _setup_owner_with_ledger()
    db.get_or_create_user("o_app")
    ok, _msg, applied_lid = db.apply_join("o_app", code)
    assert ok and applied_lid == lid


def test_apply_join_reapply_targets_the_same_ledger(iso):
    """FR-038：先后申请两本账后重新申请第一本，仍须回报第一本的 id。"""
    owner_a, code_a, lid_a = _setup_owner_with_ledger()
    db.get_or_create_user("o_owner_b")
    db.create_ledger("o_owner_b", "AA 账本")
    lid_b = db.get_user_ledger_id("o_owner_b")
    code_b = [l["invite_code"] for l in db.get_my_ledgers("o_owner_b") if l["id"] == lid_b][0]

    db.get_or_create_user("o_app2")
    assert db.apply_join("o_app2", code_a)[2] == lid_a
    assert db.apply_join("o_app2", code_b)[2] == lid_b
    # 第三次：重新申请第一本（走「已有 pending → 幂等」分支）
    # 旧实现此处会让通知发给 lid_b 的 owner
    ok, _msg, applied_lid = db.apply_join("o_app2", code_a)
    assert ok and applied_lid == lid_a, "重新申请应回报第一本的账本 id"

    # 通知目标由该 id 解析：必须是 A 的 owner，不是 B 的
    assert db.get_ledger_owner_openid(applied_lid) == "o_owner"
    assert db.get_ledger_owner_openid(applied_lid) != "o_owner_b"


def test_apply_join_failure_reports_no_ledger(iso):
    """FR-038：申请失败（口令无效）时账本 id 为 None，通知方不得据此定位。"""
    _setup_owner_with_ledger()
    db.get_or_create_user("o_app3")
    ok, msg, applied_lid = db.apply_join("o_app3", "bogus123")
    assert not ok and applied_lid is None
