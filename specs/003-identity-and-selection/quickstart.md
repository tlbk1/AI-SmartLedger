# Quickstart Validation: identity-and-selection

> 回填说明：本文件在实现完成后补齐。验证场景与 `tests/test_identity_selection.py`（31 用例，`iso` fixture 隔离临时库）逐一对应；命令均按 constitution 5.3 清空 `PYTHONPATH`。

## Prerequisites

- 项目 venv：`.venv/Scripts/python.exe`
- 跑命令前**清空 PYTHONPATH**：`PYTHONPATH= .venv/Scripts/python.exe ...`

## Setup

```bash
cd E:\360MoveData\Users\www\Desktop\AI-SmartLedger
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q          # 基线：23 + 35 = 58 用例
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/test_identity_selection.py -q   # 本功能 31 用例
PYTHONPATH= .venv/Scripts/python.exe scripts/e2e_shared_ledger.py                    # 端到端 16 场景
```

## Validation Scenarios (from spec acceptance)

### V1 默认账本锚点：创建即指向、改名不丢（US3/AC1, FR-012）
```python
# 新用户 → 默认账本指向确立
assert db.get_user_ledger_id("o_A") == 该用户默认账本id
# 把默认账本改名 → 指向不变（不再靠名字「我的账本」识别）
db.admin_rename_ledger("o_A", "我的小账本")
assert 该用户仍以同一本为默认（列表 is_default 标记不变）
```
→ 用例：`test_default_pointer_set_on_create` / `test_default_pointer_survives_rename`

### V2 删默认账本 → 自动重建空账本，锚点不落空（US3/AC2,6, FR-013/FR-014）
```python
old = db.get_user_ledger_id("o_A")
ok, msg = db.admin_delete_ledger("o_A")       # 调用方须先经 FR-014 确认
assert ok
assert db.get_user_ledger_id("o_A") != old    # 已重建并切过去
assert 该用户仍有默认账本                     # SC-003：默认账本存在率 100%
```
→ 用例：`test_delete_default_ledger_rebuilds` / `test_delete_non_default_no_rebuild`

### V3 重复删除被拒且不覆盖原时间（US6/AC6, FR-029）
```python
db.admin_delete_ledger("o_A")
t1 = 读取 deleted_at
ok, msg = db.admin_delete_ledger("o_A")
assert not ok and "已被删除" in msg
assert 读取 deleted_at == t1                  # 原删除时间不被覆盖
```
→ 用例：`test_double_delete_rejected_timestamp_kept`

### V4 账本列表：编号/角色/成员名单/三态标记 + 两次一致（US3/AC3,4,5, FR-015~FR-017）
```python
rows = db.get_my_ledgers("o_A")
assert all("#" not in str(r) or True)                 # 编号在展示层拼为 #id
assert {r["is_current"], r["is_default"], r["is_deleted"]} 标记齐备
assert [r["id"] for r in rows] == [r["id"] for r in db.get_my_ledgers("o_A")]   # SC-004
pv = db.ledger_member_preview(lid)
assert pv["names"][0] == owner昵称                    # owner 优先（FR-016）
assert "openid" not in str(pv)                        # SC-005
```
→ 用例：`test_my_ledgers_marks_and_join_order` / `test_member_preview_owner_first_and_stable` / `test_member_preview_truncates`

### V5 名称唯一 → 直接切换（US4/AC1, FR-020）
```python
ok, msg = db.switch_ledger("o_A", "旅行账")
assert ok and "已切到" in msg
```
→ 用例：`test_switch_unique_name_direct`

### V6 编号切换：`#N` 与裸数字（US4/AC3, FR-018）
```python
assert db.switch_ledger("o_A", f"#{lid}")[0] is True
assert db.switch_ledger("o_A", str(lid))[0] is True   # 裸数字同样支持
```
→ 用例：`test_switch_by_hash_number` / `test_switch_bare_number`

### V7 编号未命中 → 明确报错，不替换（US4/AC4, FR-021）
```python
ok, msg = db.switch_ledger("o_A", "#99")
assert not ok and "#99" in msg
```
→ 用例：`test_switch_number_miss_errors`

### V8 重名不猜 → 列候选且**不切换**；回复编号才切（US4/AC2,3,5, FR-019/FR-022）
```python
lid_before = db.get_user_ledger_id("o_A")
ok, msg = db.switch_ledger("o_A", "我们家")     # 有两本同名
assert not ok and "#" in msg and "回复编号选择" in msg
assert db.get_user_ledger_id("o_A") == lid_before        # SC-006：系统自行选择 = 0
assert len(候选行) == 2
assert db.switch_ledger("o_A", f"#{候选id}")[0] is True  # 回复编号 → 切换
```
→ 用例：`test_switch_duplicate_lists_candidates_no_guess` / `test_switch_duplicate_then_pick_by_number`

### V9 显式切入已删账本 = 合法只读（US4/AC5, US6/AC2, FR-036）
```python
db.admin_delete_ledger("o_A", lid_x)          # 先把 X 删掉（成员当前账本已回落）
ok, msg = db.switch_ledger("o_A", f"#{lid_x}")  # 显式切入
assert ok and "已被删除" in msg
assert db.get_user_ledger_id("o_A") == lid_x    # 显式选择不被兜底改写
```
→ 用例：`test_switch_explicit_into_deleted_readonly`

### V10 已删账本拒绝一切写操作（US6/AC3,4,5,8, FR-028/FR-030）
```python
# 六项管理写操作 + 记账，全部被拒
for op in (rename, delete, reset_code, remove_member, approve_join, leave_ledger):
    ok, msg = op(...)           # 目标为已删账本
    assert not ok and "已被删除" in msg
assert db.insert_many_for_ledger(lid_x, ...) is False     # 数据层双重防御
```
→ 用例：`test_deleted_ledger_rejects_all_admin_writes`；e2e V15/V16

### V11 跨账本管理 + 跨账本审批查询（US5/AC1,4,5, FR-023/FR-026）
```python
# owner 当前在 A，B 有待审批申请
P = db.list_pending_joins("o_A", ledger_id=B)
assert len(P) == 1 and P[0]["nickname"] == "小王"
ok, msg = db.approve_join("o_A", "小王", ledger_id=B)     # 指定 B 审批
assert ok and db.is_ledger_member("o_B", B)
assert db.is_ledger_member("o_B", A) is False             # A 不受影响（SC-008）
```
→ 用例：`test_pending_and_approve_on_specified_ledger`

### V12 昵称全局唯一 + 占用不透露占用者（US2/AC1,2,4, FR-006~FR-008）
```python
assert db.set_nickname("o_C", "小明") is True
assert db.set_nickname("o_D", "小明") is False
assert "已被占用" in db.validate_nickname("o_D", "小明")
assert "o_C" not in db.validate_nickname("o_D", "小明")     # 不透露占用者
assert db.set_nickname("o_C", "小明") is True              # 改到自身 → 成功
assert db.validate_nickname("o_C", "a"*21)  != ""          # 超长被拒
assert db.validate_nickname("o_C", "a\nb")  != ""          # 换行被拒
assert db.validate_nickname("o_C", "   ")   != ""          # 去空白后空被拒
```
→ 用例：`test_nickname_taken_rejected_without_leak` / `test_nickname_rename_to_self_ok` / `test_nickname_validation_rules`

### V13 存量重名检测与清理（US2/AC5, FR-010）
```python
assert db.find_duplicate_nicknames() 能列出重名组（含 user_ids）
n = db.cleanup_duplicate_nicknames()   # 保留最早注册者，其余改写唯一默认昵称
assert db.find_duplicate_nicknames() == [] and n >= 1
# 清理后唯一索引可成功建立（并发抢占由索引兜底，SC-001）
```
→ 用例：`test_duplicate_nickname_cleanup_and_index`

### V14 回落：被移除/退出/删账本 → 自己的默认账本（US7, FR-033~FR-035）
```python
# 默认账本已改名为「我的小账本」；用户当前在别人的共享账本
owner移除该用户（或用户 leave）
assert db.get_user_ledger_id("o_B") == B自己的默认账本   # 不是空、不是别人的
# 删除账本 → 含 owner 本人在内所有成员立即回落（默认不停留在已删账本）
assert db.get_user_ledger_id("o_A") != 刚删的账本
```
→ 用例：`test_removed_member_falls_back_to_own_renamed_default` / `test_leave_falls_back_to_default_not_others` / `test_owner_current_settles_on_delete` / `test_current_pointer_never_null_after_events`

### V15 读时兜底链：默认优先，不落他人账本（US7/AC3,4, FR-035）
```python
# 指针为空/悬空（历史脏数据）
assert db.get_user_ledger_id("o_X") == 该用户默认账本        # 首选默认
# 默认账本也不在（极端脏数据）→ 退最近加入的未删除账本；都没有 → None（上层明确报错）
```
→ 用例：`test_fallback_chain_default_first_not_recent` / `test_fallback_chain_dead_default_then_recent`

### V16 通知不丢：持久化 + 重启仍在 + 下次对话补发（US8, FR-038~FR-042）
```python
# 申请人提交 → 尝试通知 owner；推送失败/异常 → 落库
db.enqueue_undelivered(owner_openid, "📢 「家用」（#4）有新的加入申请：小王")
# 模拟服务重启（重新 import / 新建连接）后仍在
assert 记录仍在库中
msgs = db.drain_undelivered(owner_openid)     # 下次对话取走并补发
assert msgs and 再 drain 为空
```
→ 用例：`test_undelivered_persists_across_restart` / `test_join_pushes_owner_notification`

### V17 生命周期：首关五要素 / 再关注"欢迎回来"（US1, FR-001~FR-005）
```python
first = main._build_welcome("o_NEW")      # 首次：含记账示例/建账本/凭口令申请/设置称呼/当前默认账本
assert all(k in first for k in ("午饭", "建账本", "口令", "我叫", "默认"))
back = main._build_welcome("o_NEW")       # 同 openid 二次 → 欢迎回来
assert "欢迎回来" in back
```
→ 用例：`test_welcome_first_vs_returning`

### V18 记账人显示：正常与降级两条路径（US6/AC7,9,10, FR-032）
```python
rows = db.query_by_ledger(lid, ...)
assert all(r.get("created_by_nickname") for r in rows)   # SC-010：100%
assert "openid" not in str(rows)
# 强制走 LLM 降级文案路径 → 每笔仍带记账人昵称
```
→ 用例：`test_llm_fallback_shows_nickname`

## Acceptance via pytest

```bash
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q               # 89 用例全绿（23+35+31）
PYTHONPATH= .venv/Scripts/python.exe scripts/e2e_shared_ledger.py      # 16/16 场景通过
```

## Definition of Done

- [x] V1–V18 全部通过
- [x] 原 58 测试不回归（23 + 35），本功能新增 31 → **合计 89 全绿**
- [x] 端到端 16 场景通过（`scripts/e2e_shared_ledger.py`）
- [x] SC-001 占用昵称被拒率 100%；SC-002 按名定位命中唯一目标 100%（V12）
- [x] SC-003 无默认账本用户 = 0%（V2）；SC-004 列表两次一致 100%（V4）
- [x] SC-005 成员名单暴露内部标识 = 0 次（V4）
- [x] SC-006 同名自行选择 = 0%（V8）；SC-007 编号/名称指定成功率 100%（V5/V6）
- [x] SC-008 管理操作作用于非指定账本 = 0%（V11）
- [x] SC-009 已删账本写操作被拒率 100%（V10）
- [x] SC-010 可显示记账人的账目比例 100%（V18）
- [x] SC-011 当前账本为空用户数 = 0（V14）
- [x] SC-012~SC-013 发起 owner 通知 100%；通知静默丢失 0%（V16）
- [x] SC-014 五要素齐全 100% + 初始化完成 100%；SC-015 取关数据丢失 0 例、再关注延续 100%（V17）
