# Quickstart Validation: shared-ledger

## Prerequisites

- 项目 venv：`.venv/Scripts/python.exe`
- 跑命令前**清空 PYTHONPATH**：`PYTHONPATH= .venv/Scripts/python.exe ...`

## Setup

```bash
cd E:\360MoveData\Users\www\Desktop\AI-SmartLedger
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q     # 基线 23 用例
```

## Validation Scenarios (from spec acceptance)

### V1 申请加入 → 待审批，看不到数据（US1/AC1, FR-001）
```python
# 用户B 用账本A的口令申请
db.apply_join("o_B", invite_code_of_A)
assert db.get_my_join_status("o_B", ledger_A) == "pending"
# 关键: B 此刻查账本A数据 → 看不到（不是成员）
assert db.query_by_ledger(ledger_A, ...) 对 B 不可达 / B.get_user_ledger_id() != ledger_A
```

### V2 无效口令 / 重复申请（US1/AC2,AC3, FR-010）
- 无效口令 → 返回"口令不存在"，不产生申请
- 重复提交 → 仍只有一条 pending（幂等）

### V3 申请人查自己的申请状态（US1/AC4, FR-015）
```python
assert db.get_my_join_status("o_B", ledger_A) in ("pending", "approved")
```

### V4 owner 同意 → 成为 member（US2/AC1, FR-002）
```python
db.approve_join("o_A", "账本成员xxxx")   # owner 按申请人昵称同意
assert db.is_ledger_member("o_B", ledger_A)
```

### V5 非 owner 不能审批（US2/AC2, FR-003）
- member 调用 approve_join → 被拒

### V6 owner 查全部待审批（US2/AC3, FR-009）
```python
pend = db.list_pending_joins("o_A")   # 含申请人 nickname
```

### V7 权限按目标账本判定（US3/AC3, FR-004）
- X 在 L1 是 owner、L2 是 member → X 对 L2 的管理操作被拒

### V8 移除成员保留历史账目（US4/AC1, FR-005）
```python
# B 在账本A记一笔 → owner 移除 B
assert not db.is_ledger_member("o_B", ledger_A)
assert B记的那笔仍在 query_by_ledger(ledger_A) 结果里
```

### V9 账目共享 + 显示记账人（US5, FR-011, SC-006/007）
```python
# A、B 同账本各记一笔 → 任一成员查账能看到两笔
rows = db.query_by_ledger(ledger, ...)
assert len(rows) == 2
# 每笔带记账人昵称，且不含 openid
assert "created_by_nickname" in rows[0]
assert "openid" not in str(rows)
```

### V10 member 退出（US8, FR-012）
- member 退出 → 不再是成员，历史账目保留
- owner 退出 → 被拒

### V11 口令重置作废旧申请（US9/AC3, FR-013）
```python
db.apply_join("o_B", old_code)          # pending
new_code = db.reset_invite_code("o_A")  # owner 重置
assert db.get_my_join_status("o_B", ledger_A) == "expired"
assert db.apply_join("o_B", old_code) 失败   # 旧口令失效
```

### V12 删账本 → 成员回落 + 历史可查 + 提示已删除（US10, FR-014）
```python
db.admin_delete_ledger("o_A")
assert db.get_user_ledger_id("o_B") == B的默认账本   # 回落
rows = db.query_by_ledger(ledger_A, ...)            # 历史仍可查
assert 账本.deleted_at is not None                  # 带"已删除"提示依据
```

### V13 单账本免确认（US7/AC3）
- 用户只有一个账本 → 管理操作直接执行，不需确认

## Acceptance via pytest

新增测试（`tests/test_wechat_mock.py`，用 `isolated_db` fixture）命名建议：
- `test_apply_join_pending` / `test_invalid_invite_code` / `test_duplicate_apply_idempotent`
- `test_applicant_can_query_status`
- `test_approve_join_makes_member` / `test_non_owner_cannot_approve`
- `test_pending_list_for_owner`
- `test_admin_checked_by_target_ledger`
- `test_remove_member_keeps_transactions`
- `test_shared_visible_and_shows_creator_nickname`
- `test_member_can_leave` / `test_owner_cannot_leave`
- `test_reset_code_expires_pending`
- `test_delete_ledger_falls_back_to_default`

运行：`PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q`，须全绿且原 23 用例不回归。

## Definition of Done

- [ ] V1–V13 全部通过
- [ ] 原 23 测试不回归
- [ ] 审批前申请人看不到账本数据（SC-001）
- [ ] member 管理操作 100% 被拒（SC-002）
- [ ] 移除/退出后历史账目 100% 保留（SC-003）
- [ ] 查账含记账人昵称、不含 openid（SC-007）
- [ ] 删账本后成员 100% 回落默认账本（SC-008）
