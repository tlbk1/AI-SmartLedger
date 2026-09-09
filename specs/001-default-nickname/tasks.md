# Tasks: default-nickname

**Input**: Design documents from `/specs/001-default-nickname/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: 包含测试任务（spec 的 User Story 都需要可测，且 constitution 要求"测试不回归"）

**Organization**: Tasks grouped by user story to enable independent implementation/testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1/US2/US3 (from spec.md)
- Include exact file paths

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 本功能无需新建项目/迁移表（users.nickname 已存在）。仅确认基础就绪。

- [ ] T001 确认项目 venv 与测试命令可用：`PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q`（现有 18 用例通过）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 数据层基础——默认昵称生成 + 唯一性校验，是所有用户故事的前置。

- [ ] T002 在 `db.py` 新增默认昵称生成工具函数（如 `_gen_default_nickname`），格式「账本成员」+ 4 位随机 hex（用 `secrets` 或 `uuid4().hex[:4]`）
- [ ] T003 在 `db.py` 新增 `_ensure_nickname(openid)`：若用户 nickname 为空/空白，则生成默认昵称并**写入** `users.nickname`（落库）；含账本内唯一校验（冲突则重生成）
- [ ] T004 在 `db.py` 的 `list_ledger_members` 中：SELECT 改为**不返回 openid**（只取 u.nickname, lm.role），从源头杜绝 openid 落出
- [ ] T005 在 `db.py` 的 `set_nickname` 中：参数为空白/空字符串时**不生效**（返回 False / 忽略），避免写入空昵称

**Checkpoint**: 数据层就绪——默认昵称可生成、唯一性可校验、openid 不再返回、空昵称被拒。

---

## Phase 3: User Story 1 - 默认昵称自动生成 (Priority: P1) 🎯 MVP

**Goal**: 未设置昵称的用户，展示时自动获得「账本成员+随机后缀」默认昵称。

**Independent Test**: 新建无昵称用户 → `list_ledger_members` 返回的 nickname 非空、以"账本成员"开头、非 openid。

### Tests for User Story 1

> 先写测试，确保 FAIL 再实现（TDD）

- [ ] T006 [P] [US1] 测试：`test_default_nickname_generated` in `tests/test_wechat_mock.py`（新建无昵称用户，断言 nickname 以"账本成员"开头且非空）
- [ ] T007 [P] [US1] 测试：`test_same_user_same_default_nickname_across_ledgers`（同一用户两账本，断言昵称相同——用户级一个）

### Implementation for User Story 1

- [ ] T008 [P] [US1] 在 `agent.py` 的 `list_members` 工具中，移除 `m["openid"][:8]` 兜底，改为只用 nickname（若仍无则回退默认昵称）
- [ ] T009 [US1] 在 `list_ledger_members` 返回前调用 `_ensure_nickname` 确保每个成员 nickname 非空（依赖 T003）

**Checkpoint**: User Story 1 独立可测——无昵称用户显示默认昵称。

---

## Phase 4: User Story 3 - 不再显示 openid (Priority: P1)

**Goal**: 任何成员列表/展示处，绝不出现 openid 或 openid 前缀（constitution 原则 I）。

**Independent Test**: 成员列表输出中 openid 出现次数 = 0。

### Tests for User Story 3

- [ ] T010 [P] [US3] 测试：`test_no_openid_in_member_list` in `tests/test_wechat_mock.py`（断言 list_ledger_members 返回 dict 无 `openid` 键）

### Implementation for User Story 3

- [ ] T011 [US3] 确认 `list_ledger_members`（T004 已去 openid）+ `agent.py list_members`（T008 已去 openid 兜底）——两处都不再显示 openid

**Checkpoint**: User Story 3 完成——openid 不再出现在任何展示。

---

## Phase 5: User Story 2 - 主动设置后替换默认昵称 (Priority: P2)

**Goal**: 用户 `set_nickname` 后，昵称被替换为自设值，立即生效。

**Independent Test**: 默认昵称用户调用 set_nickname("小王") → 列表显示"小王"。

### Tests for User Story 2

- [ ] T012 [P] [US2] 测试：`test_set_nickname_replaces_default` in `tests/test_wechat_mock.py`（默认昵称用户 → set_nickname("小王") → 断言列表显示"小王"）
- [ ] T013 [P] [US2] 测试：`test_blank_nickname_ignored` in `tests/test_wechat_mock.py`（set_nickname 传空白 → 断言昵称不生效，保留默认）

### Implementation for User Story 2

- [ ] T014 [US2] 确认 `set_nickname`（T005 校验空）+ `_ensure_nickname` 配合：非空自设直接覆盖默认昵称（依赖 T005）

**Checkpoint**: User Story 2 完成——自设昵称替换默认，空昵称被拒。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 收尾——全量回归 + 验证 + 文档。

- [ ] T015 [P] 运行 `PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q`，确认原有 18 用例 + 新增用例全绿
- [ ] T016 按 `quickstart.md` 跑 V1-V6 验证场景，确认全部通过
- [ ] T017 [P] 更新 `docs/` 相关说明（如涉及 nickname 展示逻辑），记录默认昵称行为

---

## Dependencies & Execution Order

### Phase Dependencies
- Setup (Phase 1) → Foundational (Phase 2) 完成 → 才能开始 User Stories
- User Stories 依赖于 Foundational（T002-T005）
- Polish 依赖所有 story 完成

### User Story Dependencies
- **US1 (P1)**: 依赖 T002-T005
- **US3 (P1)**: 依赖 T004（list_ledger_members 去 openid）
- **US2 (P2)**: 依赖 T005（set_nickname 校验空）+ 替换默认昵称逻辑

### Parallel Opportunities
- T006/T007 可并行（不同测试用例）
- T010 可并行
- T012/T013 可并行

---

## Implementation Strategy

### MVP First (User Story 1)
1. T001 确认基础
2. T002-T005 Foundational（数据层）
3. T006-T009 User Story 1（默认昵称生成）
4. **STOP 验证**：User Story 1 独立可测

### Then
5. User Story 3（去 openid）→ 测试
6. User Story 2（自设替换）→ 测试
7. Polish：全量回归 + quickstart 验证

### Commit Strategy
- 每完成一个逻辑组 commit（如 Foundational 一组、US1 一组），用 conventional commit。

---

## Notes

- 本功能不改数据结构（users.nickname 已存在），无迁移。
- 重点：`list_ledger_members` 从源头去 openid（T004），比 agent 层拦截更彻底。
- 默认昵称落库（_ensure_nickname 写 users.nickname），保证"用户级一个"。
- [P] 任务 = 不同文件/独立，可并行。
