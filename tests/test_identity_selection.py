"""spec 003 identity-and-selection 测试：锚点/回落（Batch1）。"""
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


def _default_ledger_id_of(openid: str):
    uid = db.get_or_create_user(openid)
    with db._connect() as conn:
        row = conn.execute(
            "SELECT default_ledger_id FROM users WHERE id=?", (uid,)
        ).fetchone()
        return row["default_ledger_id"] if row else None


def _current_ledger_id_of(openid: str):
    uid = db.get_or_create_user(openid)
    with db._connect() as conn:
        row = conn.execute(
            "SELECT current_ledger_id FROM users WHERE id=?", (uid,)
        ).fetchone()
        return row["current_ledger_id"] if row else None


def _shared_ledger_with_member():
    """owner 建账本「我们家」+ B 加入（B 昵称 小王）。返回 (owner, b, lid)。"""
    db.get_or_create_user("o_owner")
    ok, code = db.create_ledger("o_owner", "我们家")
    lid = db.get_user_ledger_id("o_owner")
    db.set_nickname("o_b", "小王")
    db.apply_join("o_b", code)
    db.approve_join("o_owner", "小王")
    return "o_owner", "o_b", lid


# ---- FR-012：默认账本由指向关系确定 ----

def test_default_pointer_set_on_create(iso):
    """FR-002/FR-012：新用户初始化时 default_ledger_id = current = 「我的账本」。"""
    db.get_or_create_user("o_fresh")
    d = _default_ledger_id_of("o_fresh")
    c = _current_ledger_id_of("o_fresh")
    assert d is not None and d == c
    info = db.get_ledger_info(d)
    assert info["name"] == "我的账本"


def test_default_pointer_survives_rename(iso):
    """FR-012：改名不影响指向——改完后 is_default_ledger 仍指向同一本。"""
    db.get_or_create_user("o_fresh")
    d = _default_ledger_id_of("o_fresh")
    ok, _ = db.admin_rename_ledger("o_fresh", "我的小账本", d)
    assert ok
    assert _default_ledger_id_of("o_fresh") == d
    assert db.is_default_ledger("o_fresh", d) is True
    assert db.is_default_ledger("o_fresh", d + 1) is False


# ---- FR-032/033/034：回落不落空 ----

def test_removed_member_falls_back_to_own_renamed_default(iso):
    """FR-032/FR-012：被移除者回落到**自己改名后的默认账本**（指针识别，非名字）。"""
    owner, b, lid = _shared_ledger_with_member()
    # B 的默认账本改名（旧逻辑按名字找「我的账本」会在这里断链）
    b_default = _default_ledger_id_of(b)
    ok, _ = db.admin_rename_ledger(b, "我的小账本", b_default)
    assert ok
    db.switch_ledger(b, "我们家")
    assert _current_ledger_id_of(b) == lid
    ok, msg = db.admin_remove_member(owner, "小王", lid)
    assert ok, msg
    assert _current_ledger_id_of(b) == b_default, "被移除者应回落到自己（改名后的）默认账本"


def test_leave_falls_back_to_default_not_others(iso):
    """FR-032/FR-034：退出后回落自己的默认账本，绝不落在别人的共享账本。"""
    owner, b, lid = _shared_ledger_with_member()
    db.switch_ledger(b, "我们家")
    b_default = _default_ledger_id_of(b)
    ok, msg = db.leave_ledger(b, lid)
    assert ok, msg
    assert _current_ledger_id_of(b) == b_default
    assert _current_ledger_id_of(b) != lid


def test_owner_current_settles_on_delete(iso):
    """FR-033（评审修订）：owner 删除自己正在用的账本 → 当前账本立即回落默认。"""
    owner, b, lid = _shared_ledger_with_member()
    db.switch_ledger(owner, "我们家")
    owner_default = _default_ledger_id_of(owner)
    ok, msg = db.admin_delete_ledger(owner, lid)
    assert ok, msg
    assert _current_ledger_id_of(owner) == owner_default
    # 成员同样回落
    assert _current_ledger_id_of(b) == _default_ledger_id_of(b)


# ---- FR-013：默认账本删除后自动重建 ----

def test_delete_default_ledger_rebuilds(iso):
    """FR-013/方案b：删除默认账本 → 自动重建空「我的账本」+ 指向更新 + 当前切换。"""
    db.get_or_create_user("o_fresh")
    old_default = _default_ledger_id_of("o_fresh")
    ok, msg = db.admin_delete_ledger("o_fresh", old_default)
    assert ok, msg
    assert "重建" in msg
    new_default = _default_ledger_id_of("o_fresh")
    assert new_default is not None and new_default != old_default, "指向应更新到新账本"
    info = db.get_ledger_info(new_default)
    assert info["name"] == "我的账本" and info["is_deleted"] is False
    assert _current_ledger_id_of("o_fresh") == new_default, "当前账本应切到重建的默认账本"
    # 旧账本软删除、账目可追溯
    assert db.is_ledger_deleted(old_default) is True


def test_delete_non_default_no_rebuild(iso):
    """FR-013：删除非默认的共享账本 → 不重建，消息为回落口径。"""
    owner, b, lid = _shared_ledger_with_member()
    ok, msg = db.admin_delete_ledger(owner, lid)
    assert ok, msg
    assert "回落" in msg and "重建" not in msg
    assert _default_ledger_id_of(owner) != lid


def test_double_delete_rejected_timestamp_kept(iso):
    """FR-029：重复删除被拒，原删除时间不被覆盖。"""
    owner, b, lid = _shared_ledger_with_member()
    ok, _ = db.admin_delete_ledger(owner, lid)
    assert ok
    import sqlite3
    c = sqlite3.connect(str(db.DB_PATH))
    ts1 = c.execute("SELECT deleted_at FROM ledgers WHERE id=?", (lid,)).fetchone()[0]
    c.close()
    ok2, msg2 = db.admin_delete_ledger(owner, lid)
    assert not ok2
    assert "已被删除" in msg2
    c = sqlite3.connect(str(db.DB_PATH))
    ts2 = c.execute("SELECT deleted_at FROM ledgers WHERE id=?", (lid,)).fetchone()[0]
    c.close()
    assert ts1 == ts2, "原删除时间不应被覆盖"


# ---- FR-035：读时兜底链 ----

def test_fallback_chain_default_first_not_recent(iso):
    """FR-035：current 指针缺失时优先默认账本，而非「最近加入」
    （否则会静默落进别人的共享账本——评审问题2 的原始缺陷）。"""
    owner, b, lid = _shared_ledger_with_member()
    b_default = _default_ledger_id_of(b)
    # 构造脏数据：仅清空 current 指针（default 指针完好；B 加入共享账本晚于自己的默认账本）
    uid = db.get_or_create_user(b)
    with db._connect() as conn:
        conn.execute("UPDATE users SET current_ledger_id=NULL WHERE id=?", (uid,))
        conn.commit()
    # get_or_create_user 的回填走兜底链：应选默认账本（自己的），而非最近加入的共享账本
    assert _current_ledger_id_of(b) == b_default
    assert db.get_user_ledger_id(b) == b_default


def test_fallback_chain_dead_default_then_recent(iso):
    """FR-035：默认锚点失效（悬空）时退最近加入的未删除账本。"""
    owner, b, lid = _shared_ledger_with_member()
    uid = db.get_or_create_user(b)
    with db._connect() as conn:
        # 指针全部悬空（default 指向一个不存在的账本 id）
        conn.execute(
            "UPDATE users SET current_ledger_id=NULL, default_ledger_id=99999 WHERE id=?",
            (uid,),
        )
        conn.commit()
    assert db.get_user_ledger_id(b) == lid, "默认失效应退最近加入的未删除账本"


def test_current_pointer_never_null_after_events(iso):
    """FR-034：被移除/退出/删除事件后，当前账本始终有有效取值。"""
    owner, b, lid = _shared_ledger_with_member()
    db.switch_ledger(b, "我们家")
    db.admin_remove_member(owner, "小王", lid)
    assert _current_ledger_id_of(b) is not None
    db.get_or_create_user("o_owner")
    ok, code = db.create_ledger("o_owner", "第二本")
    lid2 = db.get_user_ledger_id("o_owner")
    db.set_nickname("o_b2", "小王二号")
    db.apply_join("o_b2", code)
    db.approve_join("o_owner", "小王二号")
    db.switch_ledger("o_b2", "第二本")
    db.admin_delete_ledger(owner, lid2)
    assert _current_ledger_id_of("o_b2") is not None


# ════════ US4：编号/名称选择，重名不猜（FR-018~FR-022）════════

def _two_ledgers_same_name():
    db.get_or_create_user("o_u")
    db.create_ledger("o_u", "我们家")
    lid1 = db.get_user_ledger_id("o_u")
    db.create_ledger("o_u", "我们家")
    lid2 = db.get_user_ledger_id("o_u")
    assert lid1 != lid2
    return lid1, lid2


def test_switch_unique_name_direct(iso):
    db.get_or_create_user("o_u")
    db.create_ledger("o_u", "旅行账")
    lid2 = db.get_user_ledger_id("o_u")
    db.create_ledger("o_u", "家用")
    ok, msg = db.switch_ledger("o_u", "旅行账")
    assert ok and f"#{lid2}" in msg
    assert _current_ledger_id_of("o_u") == lid2


def test_switch_by_hash_number(iso):
    lid1, lid2 = _two_ledgers_same_name()
    ok, msg = db.switch_ledger("o_u", f"#{lid1}")
    assert ok, msg
    assert _current_ledger_id_of("o_u") == lid1


def test_switch_bare_number(iso):
    lid1, lid2 = _two_ledgers_same_name()
    ok, msg = db.switch_ledger("o_u", str(lid1))
    assert ok, msg
    assert _current_ledger_id_of("o_u") == lid1


def test_switch_number_miss_errors(iso):
    _two_ledgers_same_name()
    ok, msg = db.switch_ledger("o_u", "#999")
    assert not ok and "没有编号为 #999" in msg


def test_switch_duplicate_lists_candidates_no_guess(iso):
    lid1, lid2 = _two_ledgers_same_name()
    before = _current_ledger_id_of("o_u")
    ok, msg = db.switch_ledger("o_u", "我们家")
    assert not ok, "重名时不得切换"
    assert f"#{lid1}" in msg and f"#{lid2}" in msg and "回复编号" in msg
    assert _current_ledger_id_of("o_u") == before, "候选阶段当前账本不变"


def test_switch_duplicate_then_pick_by_number(iso):
    lid1, _ = _two_ledgers_same_name()
    db.switch_ledger("o_u", "我们家")  # 触发候选
    ok, msg = db.switch_ledger("o_u", f"#{lid1}")
    assert ok
    assert _current_ledger_id_of("o_u") == lid1


def test_switch_explicit_into_deleted_readonly(iso):
    """FR-036：显式切入已删除账本合法（只读，带提示）。"""
    db.get_or_create_user("o_u")
    db.create_ledger("o_u", "旧账")
    lid = db.get_user_ledger_id("o_u")
    db.admin_delete_ledger("o_u", lid)
    # 删除后当前账本已回落（重建的新默认），显式切回去是合法只读
    ok, msg = db.switch_ledger("o_u", f"#{lid}")
    assert ok and "已被删除" in msg
    assert _current_ledger_id_of("o_u") == lid


# ════════ US3：列表标记 + 成员名单（FR-015~FR-017）════════

def test_my_ledgers_marks_and_join_order(iso):
    owner, b, lid = _shared_ledger_with_member()
    db.switch_ledger(b, "我们家")
    mine = db.get_my_ledgers(b)
    assert mine[0]["is_default"] is True and mine[0]["is_current"] is False
    assert mine[1]["id"] == lid and mine[1]["is_current"] is True
    # 编号即内部 id，已删的保留
    db.admin_delete_ledger(owner, lid)
    mine = db.get_my_ledgers(b)
    assert mine[1]["is_deleted"] is True and mine[1]["id"] == lid


def test_member_preview_owner_first_and_stable(iso):
    owner, b, lid = _shared_ledger_with_member()
    db.set_nickname(owner, "老板")
    p1 = db.ledger_member_preview(lid)
    p2 = db.ledger_member_preview(lid)
    assert p1["names"][0] == "老板", "owner 应排第一"
    assert p1 == p2, "两次展示顺序必须一致"


def test_member_preview_truncates(iso):
    owner, b, lid = _shared_ledger_with_member()
    db.set_nickname("o_c", "小张")
    db.set_nickname("o_d", "小李")
    codes = db.get_my_ledgers(owner)
    code = [l for l in codes if l["id"] == lid][0]["invite_code"]
    for o in ("o_c", "o_d"):
        db.apply_join(o, code)
        db.approve_join(owner, "小张" if o == "o_c" else "小李")
    pv = db.ledger_member_preview(lid, limit=2)
    assert pv["total"] == 4 and len(pv["names"]) == 2


# ════════ US5：管理操作指定账本 + 已删守卫（FR-023~FR-027, FR-028~FR-031）════════

def test_pending_and_approve_on_specified_ledger(iso):
    """FR-026：owner 当前在 A，也能查/批 B 的待审批。"""
    db.get_or_create_user("o_owner")
    db.create_ledger("o_owner", "账本A")
    lid_a = db.get_user_ledger_id("o_owner")   # create 后当前=A，须在此刻采集
    db.create_ledger("o_owner", "账本B")
    lid_b = db.get_user_ledger_id("o_owner")
    assert lid_a != lid_b
    db.switch_ledger("o_owner", "账本A")
    assert lid_a == db.get_user_ledger_id("o_owner")
    code_b = [l for l in db.get_my_ledgers("o_owner") if l["id"] == lid_b][0]["invite_code"]
    db.set_nickname("o_app", "小王")
    db.apply_join("o_app", code_b)
    # 不指定 → 查的是当前账本 A（无申请）；指定 B → 能看到
    assert db.list_pending_joins("o_owner") == []
    pend = db.list_pending_joins("o_owner", lid_b)
    assert len(pend) == 1 and pend[0]["nickname"] == "小王"
    ok, msg = db.approve_join("o_owner", "小王", lid_b)
    assert ok, msg
    assert db.is_ledger_member("o_app", lid_b)


def test_deleted_ledger_rejects_all_admin_writes(iso):
    """FR-028：六类写操作在已删除账本上全部被拒。"""
    owner, b, lid = _shared_ledger_with_member()
    code = [l for l in db.get_my_ledgers(owner) if l["id"] == lid][0]["invite_code"]
    db.set_nickname("o_late", "小张")
    db.apply_join("o_late", code)  # 留一条 pending
    ok, _ = db.admin_delete_ledger(owner, lid)
    assert ok
    assert not db.admin_rename_ledger(owner, "新名", lid)[0]
    assert not db.approve_join(owner, "小张", lid)[0]
    assert not db.admin_remove_member(owner, "小王", lid)[0]
    assert not db.reset_invite_code(owner, lid)[0]
    assert not db.leave_ledger(b, lid)[0]
    ok2, msg2 = db.admin_delete_ledger(owner, lid)
    assert not ok2 and "已被删除" in msg2


# ════════ US2：昵称全局唯一（FR-006~FR-010）════════

def test_nickname_taken_rejected_without_leak(iso):
    db.get_or_create_user("o_a")
    assert db.set_nickname("o_a", "小明") is True
    err = db.validate_nickname("o_b", "小明")
    assert "占用" in err
    assert "o_a" not in err, "不得透露占用者"
    assert db.set_nickname("o_b", "小明") is False


def test_nickname_rename_to_self_ok(iso):
    db.get_or_create_user("o_a")
    db.set_nickname("o_a", "小明")
    assert db.set_nickname("o_a", "小明") is True


def test_nickname_validation_rules(iso):
    db.get_or_create_user("o_a")
    assert db.validate_nickname("o_a", "") == "昵称不能为空"
    assert db.validate_nickname("o_a", "   ") == "昵称不能为空"
    assert "换行" in db.validate_nickname("o_a", "小\n王")
    assert "20" in db.validate_nickname("o_a", "x" * 21)
    assert db.validate_nickname("o_a", "y" * 20) == ""


def test_duplicate_nickname_cleanup_and_index(iso):
    """FR-010：存量重名检测+清理，清理后唯一索引生效。"""
    db.get_or_create_user("o_1")
    db.get_or_create_user("o_2")
    with db._connect() as conn:
        conn.execute("DROP INDEX IF EXISTS idx_users_nickname")
        conn.execute("UPDATE users SET nickname='重名者' WHERE openid IN ('o_1','o_2')")
        conn.commit()
    dups = db.find_duplicate_nicknames()
    assert len(dups) == 1 and dups[0]["count"] == 2
    renamed = db.cleanup_duplicate_nicknames()
    assert renamed >= 1
    assert db.find_duplicate_nicknames() == []
    # 唯一索引已重建：直接 SQL 撞名也写不进去
    nick2 = [u for u in db.get_my_ledgers("o_2")][:0]  # noqa（占位避免误用）
    import sqlite3
    c = sqlite3.connect(str(db.DB_PATH))
    n2 = c.execute("SELECT nickname FROM users WHERE openid='o_2'").fetchone()[0]
    c.close()
    with pytest.raises(Exception):
        with db._connect() as conn:
            conn.execute("UPDATE users SET nickname=? WHERE openid='o_1'", (n2,))
            conn.commit()


# ════════ US8：通知不丢（FR-038~FR-042）════════

def test_undelivered_persists_across_restart(iso):
    db.enqueue_undelivered("o_x", "待补发A")
    db.enqueue_undelivered("o_x", "待补发B")
    db.init()  # 模拟重启（幂等建表）
    assert db.drain_undelivered("o_x") == ["待补发A", "待补发B"]
    assert db.drain_undelivered("o_x") == []


def test_join_pushes_owner_notification(iso, monkeypatch):
    """FR-038：申请提交后尽力通知 owner；推送失败 → 入待补发（FR-039）。"""
    import time
    import wechat as w
    owner, b, lid = _shared_ledger_with_member()
    code = [l for l in db.get_my_ledgers(owner) if l["id"] == lid][0]["invite_code"]
    monkeypatch.setattr(w, "send_customer_message", lambda *a, **k: False)
    import agent
    tools = {t.name: t for t in agent.make_tools("o_c")}
    db.set_nickname("o_c", "小张")
    out = tools["join_ledger"].invoke({"invite_code": code})
    assert "申请" in out
    deadline = time.time() + 3
    row = None
    while time.time() < deadline:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT text FROM undelivered_notices WHERE openid=?", (owner,)
            ).fetchone()
        if row:
            break
        time.sleep(0.05)
    assert row is not None, "owner 应收到（或入队）申请通知"
    assert "小张" in row["text"] and "我们家" in row["text"]


# ════════ US1：关注/回来欢迎语（FR-001/FR-005）════════

def test_welcome_first_vs_returning(iso):
    import main
    w1 = main._build_welcome("o_brand_new")
    assert "默认账本" in w1            # ⑤ 当前处于默认账本
    assert "建账本" in w1 and "口令" in w1  # ② ③
    assert "称呼" in w1                # ④
    assert "午饭 35" in w1             # ①
    db.get_or_create_user("o_brand_new")
    w2 = main._build_welcome("o_brand_new")
    assert "欢迎回来" in w2


# ════════ US6：降级文案带记账人（FR-032/SC-010）════════

def test_llm_fallback_shows_nickname(iso, monkeypatch):
    import llm

    class _Boom:
        class chat:
            class completions:
                @staticmethod
                def create(*a, **k):
                    raise RuntimeError("llm down")

    monkeypatch.setattr(llm, "_get_client", lambda: _Boom())
    rows = [{
        "type": "expense", "amount": 32.0, "category": "餐饮",
        "happened_at": "2026-09-03T12:00:00+08:00", "created_by_nickname": "老板",
    }]
    out = llm.summarize_query_result(rows, "上月花了多少", "2026-09-10T10:00:00+08:00")
    assert "AI 总结暂时不可用" in out
    assert "老板" in out and "32.00" in out
