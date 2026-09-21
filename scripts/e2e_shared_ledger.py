"""端到端验证：共账功能（spec 002-shared-ledger）V1-V16。

用法（项目根）:
    PYTHONPATH= .venv/Scripts/python.exe scripts/e2e_shared_ledger.py

在**临时库**上跑完整用户旅程（建账本→申请→审批→共享记账→权限→移除→退出→
重置口令→删账本→已删账本只读→owner 兜底提示），跑完自动清理，不碰 ledger.db。
对应 tasks.md T046/T048/T049/T050 与 quickstart.md 的验证场景。
"""
import sys
import pathlib
import tempfile
import shutil
import sqlite3

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

tmp = tempfile.mkdtemp()

import db

db.DB_PATH = pathlib.Path(tmp) / "e2e.db"
db.init()

ok_all = True


def check(name, cond, detail=""):
    global ok_all
    mark = "✅" if cond else "❌"
    if not cond:
        ok_all = False
    print(f"{mark} {name} {detail}")


# ── V1 新用户首次对话 → 默认账本自动创建 + 默认昵称即时生成 ──
uid_a = db.get_or_create_user("o_A")
lid_default_a = db.get_user_ledger_id("o_A")
check("V1 默认账本自动创建", lid_default_a is not None)
_c = sqlite3.connect(str(db.DB_PATH))
_nick = _c.execute("SELECT nickname FROM users WHERE id=?", (uid_a,)).fetchone()[0]
_c.close()
check("V1 默认昵称即时生成", (_nick or "").startswith("账本成员"), f"(昵称={_nick})")

db.set_nickname("o_A", "老板")

# ── V2 建账本得口令 ──
ok, code = db.create_ledger("o_A", "我们家")
check("V2 建账本返回口令", ok and len(code) == 6)

# ── V3 B 凭口令申请 ──
uid_b = db.get_or_create_user("o_B")
_c = sqlite3.connect(str(db.DB_PATH))
_nick_b = _c.execute("SELECT nickname FROM users WHERE id=?", (uid_b,)).fetchone()[0]
_c.close()
check("V3b 新用户B默认昵称", (_nick_b or "").startswith("账本成员"), f"(昵称={_nick_b})")
db.set_nickname("o_B", "小王")
lid = db.get_user_ledger_id("o_A")
ok, msg, _ = db.apply_join("o_B", code)
check("V3 申请提交成功", ok)
check("V3 状态=pending", db.get_my_join_status("o_B", lid) == "pending")

# ── V4 pending 期间看不到账本数据 ──
check("V4 pending 不是成员", not db.is_ledger_member("o_B", lid))

# ── V5 owner 查看待审批（申请人昵称非空，可按名同意）──
pending = db.list_pending_joins("o_A")
check("V5 owner 见到待审批", any(p["nickname"] == "小王" for p in pending),
      f"({[p['nickname'] for p in pending]})")

# ── V6 owner 同意 → 成为 member ──
ok, msg = db.approve_join("o_A", "小王")
check("V6 同意成功", ok)
check("V6 B 已是 member", db.is_ledger_member("o_B", lid))

# ── V7 B 查申请状态=已通过 ──
check("V7 状态=approved", db.get_my_join_status("o_B", lid) == "approved")

# ── V8 共享账目 + 记账人 ──
db.insert_many_for_ledger(lid, uid_a, [db.Transaction("expense", 100.0, "餐饮", "2026-09-10T12:00:00+08:00", "家庭聚餐")])
db.insert_many_for_ledger(lid, uid_b, [db.Transaction("expense", 50.0, "交通", "2026-09-10T18:00:00+08:00", "打车")])
rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30")
names = {r["created_by_nickname"] for r in rows}
check("V8 两人账目共享可见", len(rows) == 2)
check("V8 记账人昵称正确", names == {"老板", "小王"}, f"({names})")
check("V8 无 openid 泄露", all("openid" not in r for r in rows))

# ── V9 member 无管理权 ──
db.switch_ledger("o_B", "我们家")
ok, msg = db.admin_remove_member("o_B", "老板")
check("V9 member 不能移除人", not ok and "管理员" in msg)

# ── V10 owner 移除 member，账目保留，且 current 回落（隔离泄漏回归）──
ok, msg = db.admin_remove_member("o_A", "小王")
rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30")
check("V10 移除成功", ok and not db.is_ledger_member("o_B", lid))
check("V10 历史账目保留", len(rows) == 2)
check("V10 被移除者 current 回落默认账本", db.get_user_ledger_id("o_B") != lid,
      f"(当前={db.get_user_ledger_id('o_B')})")

# ── V11 member 重新加入 → 退出 ──
ok, msg, _ = db.apply_join("o_B", code)
check("V11 被移除后可重新申请", ok, msg)
ok, msg = db.approve_join("o_A", "小王")
check("V11 重新同意成功", ok, msg)
db.switch_ledger("o_B", "我们家")
ok, msg = db.leave_ledger("o_B")
check("V11 member 退出成功", ok and not db.is_ledger_member("o_B", lid))
ok, msg = db.leave_ledger("o_A")
check("V11 owner 不能退出", not ok and "管理员" in msg)

# ── V12 重置口令 → pending 作废 ──
db.apply_join("o_C", code)
ok, new_code = db.reset_invite_code("o_A")
check("V12 新口令≠旧口令", ok and new_code != code)
check("V12 旧申请作废", db.get_my_join_status("o_C", lid) == "expired")
ok2, _, _ = db.apply_join("o_D", code)
check("V12 旧口令失效", not ok2)

# ── V13 删账本 → 成员回落各自默认账本 ──
db.apply_join("o_B", new_code)
db.approve_join("o_A", "小王")
db.switch_ledger("o_B", "我们家")          # B 当前=我们家(lid)
_c = sqlite3.connect(str(db.DB_PATH))
b_default_real = _c.execute("""
    SELECT l.id FROM ledgers l JOIN ledger_members lm ON lm.ledger_id=l.id
    JOIN users u ON u.id=lm.user_id
    WHERE u.openid='o_B' AND l.name='我的账本' AND l.deleted_at IS NULL
""").fetchone()[0]
_c.close()
check("V13 前置: B 当前在 lid", db.get_user_ledger_id("o_B") == lid)
ok, msg = db.admin_delete_ledger("o_A")
check("V13 删账本成功", ok)
check("V13 B 回落默认账本", db.get_user_ledger_id("o_B") == b_default_real,
      f"(当前={db.get_user_ledger_id('o_B')}, 默认={b_default_real})")
rows = db.query_by_ledger(lid, "2026-09-01", "2026-09-30")
check("V13 已删账本历史仍可查", len(rows) >= 2)

# ── V14 T049: owner 对话时自动带待审批提示（兜底保底）──
# 用全新的活账本测（不依赖 V13 删掉的账本）
import agent
db.get_or_create_user("o_F")
db.set_nickname("o_F", "老F")
ok, code_f = db.create_ledger("o_F", "F的账本")
check("V14 前置: F 无待审批", agent._pending_joins_hint("o_F") == "")
db.set_nickname("o_E", "小E")
db.apply_join("o_E", code_f)
_hint = agent._pending_joins_hint("o_F")
check("V14 owner 有待审批→提示含昵称", "待审批" in _hint and "小E" in _hint, f"({_hint[:60]}...)")
# 非 owner 不提示。必须让"空提示"有个会产生提示的真实原因——用普通成员「小G」测：
# owner 在这本账上确实有待审批申请，小G 只是成员，所以提示空是因为权限判定生效，
# 不是因为这本账本来就没有待审批。否则守卫被删掉这行断言照样通过（假绿）。
db.get_or_create_user("o_G")
db.set_nickname("o_G", "小G")
db.apply_join("o_G", code_f)
ok, _ = db.approve_join("o_F", "小G")
_fid, _ferr = db.resolve_ledger_selector("o_F", "F的账本")
check("V14 前置: 小G 已是 F 账本的普通成员", ok and _ferr is None and db.is_ledger_admin("o_G", _fid) is False)
check("V14 前置: F 仍有待审批申请（小E）", db.list_pending_joins("o_F") != [])
# 关键前置：把小G 的【当前账本】切到 F 的账本——_pending_joins_hint 查的是当前账本，
# 而加入别人的账本不会自动切换；不切的话它查的是小G 自己的「我的账本」（本来就没有待审批），
# 权限判定被删掉这行断言照样通过（假绿）。
_ok_sw, _sw_msg = db.switch_ledger("o_G", f"#{_fid}")
check("V14 前置: 小G 当前账本=F 账本（且仍有待审批）",
      _ok_sw and db.get_user_ledger_id("o_G") == _fid, f"({_sw_msg})")
check("V14 非 owner 不提示（不泄露）", agent._pending_joins_hint("o_G") == "")

# ── V15 T050: 已删账本可切进去看历史，且带"已删除"提示 ──
_mine = {l["name"]: l for l in db.get_my_ledgers("o_B")}
check("V15 已删账本仍在列表且标记", _mine.get("我们家", {}).get("is_deleted") is True)
ok, msg = db.switch_ledger("o_B", "我们家")
check("V15 切进已删账本成功+提示", ok and "已被删除" in msg, f"({msg})")
check("V15 当前账本=已删账本", db.get_user_ledger_id("o_B") == lid)
check("V15 is_ledger_deleted 正确", db.is_ledger_deleted(lid) is True)
# 查账工具带 ledger_deleted 标记
_tools = {t.name: t for t in agent.make_tools("o_B")}
import json as _json
_out = _json.loads(_tools["query_transactions"].invoke(
    {"date_from": "2026-09-01", "date_to": "2026-09-30"}))
check("V15 查账结果带已删除标记", _out.get("ledger_deleted") is True and "已被删除" in _out.get("notice", ""))
check("V15 已删账本历史账目可见", len(_out.get("records", [])) >= 2)
# US10/AC3：已删账本的口令失效（不能用旧口令申请）
_ok_join, _msg_join, _ = db.apply_join("o_Z", new_code)
check("V15 已删账本口令失效", not _ok_join, f"({_msg_join})")

# ── V16 T050: 已删账本只读——不能记账（工具层友好拒绝 + db 层拦截）──
_out = _tools["record_transactions"].invoke(
    {"transactions": [{"type": "expense", "amount": 9.9, "category": "其他"}]})
check("V16 工具层拒绝记账", "已被删除" in _out and "不能记账" in _out, f"({_out[:40]})")
_ok_db = db.insert_many_for_ledger(
    lid, uid_b, [db.Transaction("expense", 9.9, "其他", "2026-09-11T12:00:00+08:00", "不应写入")])
check("V16 db 层拒绝写入", _ok_db is False)
check("V16 账目数未变", len(db.query_by_ledger(lid, "2026-09-01", "2026-09-30")) >= 2)

print()
print("═" * 40)
print("🎉 全部 16 个场景通过！" if ok_all else "⚠️ 有场景失败，看上面 ❌")

shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if ok_all else 1)
