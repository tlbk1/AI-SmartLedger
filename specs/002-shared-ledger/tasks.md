# Tasks: shared-ledger

**Input**: Design documents from `/specs/002-shared-ledger/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: 包含（constitution 5.1 要求测试不回归 + 补测试）

**Organization**: 按 user story 分组，每故事独立可测。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: US1..US10（对应 spec.md）
- 文件路径明确

---

## Phase 1: Setup

- [ ] T001 确认基线：`PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q`（23 用例绿）

---

## Phase 2: Foundational (阻塞所有故事)

**Purpose**: `join_requests` 表 + 权限判定签名改造 —— 所有故事的前置。

- [ ] T002 [P] 在 `db.py::init()` 新增 `join_requests` 表（id/ledger_id/user_id/status/created_at + UNIQUE(ledger_id,user_id)），幂等建表
- [ ] T003 [P] 在 `db.py` 改 `is_ledger_admin(openid, ledger_id=None)`：显式传账本时按该账本判定；不传回退当前账本（D2）
- [ ] T004 [P] 在 `db.py` 新增 `is_ledger_member(openid, ledger_id)`：判定是否为该账本成员（owner/member 均可）
- [ ] T005 在 `db.py` 新增 `_get_ledger_id_by_invite(code)` + `_get_default_ledger_id(user_id)` 辅助函数（供申请/回落用）

**Checkpoint**: 表结构 + 权限判定就绪。

---

## Phase 3: US1 - 申请加入（P1）🎯 MVP

**Goal**: 凭口令申请 → pending，申请人看不到数据。

- [ ] T006 [P] [US1] 测试 `test_apply_join_pending`（申请后 status=pending）
- [ ] T007 [P] [US1] 测试 `test_invalid_invite_code`（无效口令不产生申请）
- [ ] T008 [P] [US1] 测试 `test_duplicate_apply_idempotent`（重复申请不产生多条）
- [ ] T009 [P] [US1] 测试 `test_applicant_cannot_see_ledger_data`（pending 时查不到账本数据）
- [ ] T010 [US1] 在 `db.py` 实现 `apply_join(openid, invite_code) -> (bool, str)`：校验口令、幂等、插 pending
- [ ] T011 [US1] 在 `db.py` 实现 `get_my_join_status(openid, ledger_id) -> str|None`（FR-015）

**Checkpoint**: US1 独立可测（申请→pending→看不到数据→能查自己状态）。

---

## Phase 4: US2 - owner 同意（P1）

**Goal**: owner 查待审批 + 同意 → 申请人成为 member + 尽力通知。

- [ ] T012 [P] [US2] 测试 `test_pending_list_for_owner`（含申请人 nickname）
- [ ] T013 [P] [US2] 测试 `test_approve_join_makes_member`
- [ ] T014 [P] [US2] 测试 `test_non_owner_cannot_approve`
- [ ] T015 [US2] 在 `db.py` 实现 `list_pending_joins(openid) -> list[dict]`（owner 视角，含申请人 nickname，不含 openid）
- [ ] T016 [US2] 在 `db.py` 实现 `approve_join(owner_openid, applicant_nickname) -> (bool, str)`：校验 owner + pending→approved + 插 member
- [ ] T017 [US2] 在 `agent.py` 加工具 `list_pending` / `approve_join` + prompt 说明
- [ ] T018 [US2] 通知：同意后尽力推送申请人（复用 `send_customer_message`，失败入 undelivered）—— 在调用层（main/agent 返回后）处理

**Checkpoint**: US2 独立可测（owner 同意→成为 member）。

---

## Phase 5: US3 - 权限边界（P1）

**Goal**: 权限严格按目标账本角色。

- [ ] T019 [P] [US3] 测试 `test_admin_checked_by_target_ledger`（X 在 L1 owner、L2 member → 对 L2 被拒）
- [ ] T020 [P] [US3] 测试 `test_member_cannot_manage`（member 调管理操作全被拒）
- [ ] T021 [US3] 改造 `db.py` 所有管理函数（`admin_remove_member`/`admin_rename_ledger`/`admin_delete_ledger`）用 `is_ledger_admin(openid, ledger_id)` 显式判定

**Checkpoint**: 权限按目标账本判定。

---

## Phase 6: US4 - 成员移除（P1）

**Goal**: 移除立即失效 + 历史账目保留。

- [ ] T022 [P] [US4] 测试 `test_remove_member_keeps_transactions`
- [ ] T023 [P] [US4] 测试 `test_cannot_remove_owner`（owner 不可被移除，D7）
- [ ] T024 [US4] 改造 `db.py::admin_remove_member`：删除 ledger_members 行（不动 transactions）+ 拒绝移除 owner

**Checkpoint**: 移除保留账目。

---

## Phase 7: US5 - 账目共享 + 记账人（P1）

**Goal**: 成员看全部账目 + 显示谁记的。

- [ ] T025 [P] [US5] 测试 `test_shared_visible_all_members`（A、B 各记一笔，都能看到两笔）
- [ ] T026 [P] [US5] 测试 `test_query_includes_creator_nickname`（每笔带 created_by_nickname，无 openid）
- [ ] T027 [US5] 改造 `db.py::query_by_ledger`：LEFT JOIN users 取 `created_by_user_id` 的 nickname，返回带 `created_by_nickname` 字段（不含 openid）
- [ ] T028 [US5] 改造 `llm.py::summarize_query_result`：摘要中呈现"谁记的"（若有该字段）

**Checkpoint**: 共享可见 + 记账人展示。

---

## Phase 8: US6 - 默认账本与多账本（P2）

- [ ] T029 [P] [US6] 测试 `test_new_user_has_default_ledger`（已存在行为，补回归测试）
- [ ] T030 [P] [US6] 测试 `test_join_new_ledger_does_not_switch`（加入后当前账本不变）
- [ ] T031 [US6] 校验 `db.create_ledger`/`join_ledger` 行为符合（应无需改，仅确认）

---

## Phase 9: US7 - 管理操作指定账本（P2）

- [ ] T032 [P] [US7] 测试 `test_single_ledger_no_confirm`（单账本免确认，D8）
- [ ] T033 [P] [US7] 测试 `test_multi_ledger_requires_target`（多账本需指定）
- [ ] T034 [US7] 在 `agent.py` prompt 中明确：管理操作须指定账本；单账本直接执行

---

## Phase 10: US8 - member 退出（P2）

- [ ] T035 [P] [US8] 测试 `test_member_can_leave`（退出后非成员，账目保留）
- [ ] T036 [P] [US8] 测试 `test_owner_cannot_leave`
- [ ] T037 [US8] 在 `db.py` 实现 `leave_ledger(openid) -> (bool, str)`：删除自己的 member 行；owner 拒绝

---

## Phase 11: US9 - 口令重置（P2）

- [ ] T038 [P] [US9] 测试 `test_reset_invite_code_expires_pending`
- [ ] T039 [P] [US9] 测试 `test_non_owner_cannot_reset_code`
- [ ] T040 [US9] 在 `db.py` 实现 `reset_invite_code(openid) -> (bool, str)`：校验 owner + 生成新口令 + pending 置 expired（D5）
- [ ] T041 [US9] 在 `agent.py` 加工具 `reset_invite_code` + prompt

---

## Phase 12: US10 - 删账本后成员处理（P2）

- [ ] T042 [P] [US10] 测试 `test_delete_ledger_falls_back_to_default`
- [ ] T043 [P] [US10] 测试 `test_deleted_ledger_history_still_queryable`
- [ ] T044 [US10] 改造 `db.py::admin_delete_ledger`：软删除 + 该账本成员 current_ledger_id 回落默认账本（D6）

---

## Phase 13: Polish

- [ ] T045 [P] 全量回归：`PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q` 全绿（原23+新）
- [ ] T046 按 `quickstart.md` 跑 V1-V13 验证
- [ ] T047 [P] 更新 `docs/共账-agent设计方案.md` 记录审批制/共享账目/口令重置/删账本回落
- [ ] T048 端到端：真实跑一次"申请→owner同意→共同记账→查账看到彼此"（需微信或本地模拟）

---

## Dependencies & Execution Order

### Phase Dependencies
- Setup(T001) → Foundational(T002-T005) → 各 User Story
- Foundational **阻塞所有故事**

### User Story Dependencies
- US1(申请) 与 US2(同意) 配对，US2 依赖 US1 的 pending 数据
- US3(权限) 是 US4/US9/US10 的前置（都用 is_ledger_admin）
- US5(共享) 独立
- US6/US7/US8/US9/US10 相对独立

### Parallel Opportunities
- T002/T003/T004 可并行（同文件不同函数，实际顺序改）
- 每故事的测试任务 [P] 可并行
- US5 / US9 / US10 可由不同人并行

---

## Implementation Strategy

### MVP First
T001→T005（地基）→ T006-T011（US1 申请）→ T012-T018（US2 同意）
→ **STOP 验证**：申请→同意闭环

### Then
US3(权限) → US4(移除) → US5(共享) → US6/US7/US8/US9/US10 → Polish

### Commit Strategy
- 每完成一个故事（或逻辑组）commit，conventional commit。
- **实施时开分支** `feat/002-shared-ledger`（不在 main 直接改）。

---

## Notes

- 本功能新增 1 表；db.py 改动最大；agent.py 加工具；llm.py 摘要加记账人。
- **推送尽力而为**：48h 限制，失败入 undelivered；兜底靠 owner 对话提示。
- **不写 NULL 账目 / 不显示 openid**（constitution I、II）。
