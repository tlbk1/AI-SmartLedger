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
    ok, msg = db.apply_join("o_applicant", code)
    assert ok, msg
    assert db.get_my_join_status("o_applicant", lid) == "pending"


# ---- T007: 无效口令不产生申请 ----
def test_invalid_invite_code(iso):
    _setup_owner_with_ledger()
    ok, msg = db.apply_join("o_applicant", "bogus123")
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
    ok2, msg2 = db.apply_join("o_other", code)
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
