# Tasks: identity-and-selection

**Branch**: `feat/002-shared-ledger` | **Input**: specs/003-identity-and-selection/spec.md

> **回填说明**：与 plan.md 同批回填——实现已先行落地（提交 327ebc4，2026-09-17），
> 本清单按**实际产物**回写并全部勾选；Convergence 节为逐条核对（2026-09-17）产生的剩余项。

## Phase 1: 数据层——锚点与回落（US3/US7 基座）

- [x] T001 `db.init()`：新增 `users.default_ledger_id` 列 + 存量回填（优先「我的账本」owner+未删，否则最近加入）per FR-012
- [x] T002 `_get_default_ledger_id` 改为**读指针**（失效/悬空返回 None 走兜底），移除按名字识别 per FR-012（审查③根因）
- [x] T003 `_create_default_ledger` 助手：新用户初始化与默认账本重建共用 per FR-002/FR-013
- [x] T004 `_fallback_ledger_id`：默认 → 最近加入（可 exclude）→ None，修正"最近加入"旧口径 per FR-035（审查②）
- [x] T005 `_settle_current_ledger`：移除/退出/删除三处回落统一走它，含 owner 本人，永不静默落他人账本 per FR-033/FR-034（审查③同款实例×2）
- [x] T006 `admin_delete_ledger` 重写：**打删除标记前**先采集默认归属；默认被删自动重建空账本并改指向（方案b）；重复删除被拒且不覆盖 `deleted_at` per FR-013/FR-029（审查③）
- [x] T007 `get_user_ledger_id` 兜底链改走 `_fallback_ledger_id`；`get_or_create_user` 老用户回填同口径 per FR-035

## Phase 2: 选择、列表、管理、昵称、通知、生命周期（US1~US6/US8）

- [x] T008 `resolve_ledger_selector`：`#N`/裸数字按内部 id 精确匹配（限定本人账本，含已删）；名称唯一→直效；重名→返回候选列表（编号+成员名单，实时重算）per FR-018~FR-022（审查⑥）
- [x] T009 `switch_ledger` 重写为 selector 入口（复用 T008），显式切入已删账本合法只读 per FR-036
- [x] T010 `_members_preview`/`ledger_member_preview`：owner 优先、加入序稳定、截断+总人数，只出昵称 per FR-016/FR-017
- [x] T011 `get_my_ledgers`：加入序 ASC + `is_current`/`is_default`/`is_deleted` 标记 per FR-015
- [x] T012 db 层 `list_pending_joins`/`approve_join` 增加 `ledger_id` 参数（此前审批双函数无入口）per FR-025/FR-026（审查①加深）
- [x] T013 六个写函数加 `is_ledger_deleted` 守卫：approve/remove/rename/reset/leave/delete（delete 为重复删除守卫）per FR-028（审查⑦）
- [x] T014 agent `_resolve_admin_ledger` + 六个管理工具透传 `ledger_name`；approve 通知改按目标账本定位 per FR-022~FR-024
- [x] T015 agent `admin_delete_ledger` 确认闸：默认账本删除必须先返回后果话术取得 confirm='yes' per FR-014
- [x] T016 昵称全局唯一：`validate_nickname`（空/换行/≤20/占用不透露）+ `set_nickname` 校验前置 + `idx_users_nickname` 唯一索引（IntegrityError 兜底并发）per FR-006~FR-009（审查⑤）
- [x] T017 `_nickname_taken`/`_gen_default_nickname` 改全局口径（修正 001 期 docstring 与实现不符）+ `find/cleanup_duplicate_nicknames` 存量检测清理 per FR-006/FR-010
- [x] T018 `undelivered_notices` 持久化表替代进程内存 dict；enqueue/drain 改 sqlite per FR-040/FR-041（审查⑨）
- [x] T019 申请后尽力推送 owner（含昵称/账本名/操作指引）+ 生成异常入待补发；approve 通知异常通道同步补齐 per FR-038/FR-039（审查⑧⑨）
- [x] T020 死代码清理：删除绕过审批的 `db.join_ledger` 与无主查询 `db.query`（tools.py 只留 schema，llm/test 引用迁移）per FR-037 + Assumptions 范围外小项
- [x] T021 main.py 生命周期：subscribe 即初始化（幂等）+ `_build_welcome` 五要素欢迎语/再关注"欢迎回来"；unsubscribe 零数据变更 per FR-001~FR-005（审查⑩/旅程复盘）
- [x] T022 llm 降级文案逐条带记账人昵称 per FR-032（审查⑨-3）
- [x] T023 wechat.py token 过期判断改 `time.monotonic` per Assumptions 范围外小项（审查④）
- [x] T024 AGENT_SYSTEM_PROMPT 全面更新：#N 语义、重名转述规则、管理指定账本、昵称唯一、删默认确认、已删回落说明 per US4~US6/FR-014

## Phase 3: 验证

- [x] T025 新增 `tests/test_identity_selection.py`（31 用例：锚点/回落/重建/重复删/兜底链/selector/候选不猜/列表标记/跨账本审批/已删六守卫/昵称唯一+清理/通知入队/欢迎语/降级记账人）
- [x] T026 旧测试适配：test_wechat_mock 查询迁移 `query_by_ledger` + 补 `isolated_db` 隔离（修掉写真实库的污染源）
- [x] T027 全量验证：pytest **89 passed**（23+35+31）+ e2e 16/16；真实库迁移演练通过（备份 `ledger.db.bak.before_spec003.*`）

## Phase 4: Convergence（2026-09-17 逐条核对）

- [ ] T028 修正 `db.py:728` 过期注释——仍引用已删除的"旧 insert_many / query 签名"，实际两函数均已移除 per Constitution I / FR-037 (partial)
- [x] T029 ~~tasks.md 缺失而 plan.md 声称已回填~~ —— 由本次回填自解（plan.md:7 的表述随之成立） (missing)

## Notes

- FR-023「多账本未指明先确认」由 prompt 规则承载（无代码级强制）——spec US7 明示这是 LLM 行为、由 prompt 驱动，tests 断言 prompt 字符串；**有意设计**，非缺口
- 范围外（有意不做）：拒绝申请、申请自动过期、账本转让/多 owner、跨账本统计、账本分组
- FR-011：constitution v1.3.0 已同步"全局唯一"；spec 001 为历史存档保留不改（Sync Impact 注明）
