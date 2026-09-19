# Data Model: identity-and-selection

> 回填说明：与 `db.py::init()` 的实际建表/补列/索引逻辑一致。

## New Table

### `undelivered_notices`（待补发通知，FR-041）

| Field | Type | Constraint | Notes |
|---|---|---|---|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 决定补发顺序（id ASC） |
| openid | TEXT | NOT NULL | 目标用户 |
| text | TEXT | NOT NULL | 要补发的通知文本 |
| created_at | TEXT | NOT NULL | ISO 8601（Asia/Shanghai） |

**用途**：客服消息推送失败或通知生成异常时落库，替代 002 的进程内存队列 → 重启不丢、下次对话 `drain_undelivered` 补发并清空。

**操作**：`enqueue_undelivered(openid, text)` / `drain_undelivered(openid) -> list[str]`（取走即删）。

## New Column

### `users.default_ledger_id`（FR-012）

| Field | Type | Notes |
|---|---|---|
| default_ledger_id | INTEGER | 默认账本的**指向**（锚点）。NULL = 未回填（仅存量瞬时态） |

**幂等补列**：`_add_column_if_missing(conn, "users", "default_ledger_id", "INTEGER")`（兼容旧库）。

**存量回填**（`init()` 内，仅在 `default_ledger_id IS NULL` 时执行）：
1. 优先：该用户为 owner 且名为「我的账本」且未删除的最早账本；
2. 否则：该用户最近加入（`ledger_members.id DESC`）的未删除账本。

**规则**：
- 账本**改名不影响指向**（FR-012）；
- 默认账本被删除时 → `_create_default_ledger` 重建一个空的「我的账本」并更新指向（FR-013，须先经 FR-014 确认）；
- 因为 `users` 表被后续 `default_ledger_id` 依赖，**所有** `uid` 级规则（回落、重建）都以本列为准。

## New Index

### `idx_users_nickname`（FR-009，唯一）

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_nickname ON users(nickname)
```

**并发语义**：两个会话同时 `set_nickname` 同一未占用昵称 → 至多一个成功，另一个捕获 `sqlite3.IntegrityError` 返回失败（FR-009）。

**存量前提**（FR-010）：若存量存在重名/NULL 空名，建索引会抛 `IntegrityError` → 代码降级为 **warning**（不崩溃），并提示运行 `cleanup_duplicate_nicknames()`。清理策略：重名组保留**最早注册者**（id 最小），其余改为新生成的唯一默认昵称；NULL/空名一并补生成。

## Existing Tables (复用, 本次语义变化)

### `users`
`id / openid(UNIQUE) / nickname / created_at / current_ledger_id / `**`default_ledger_id`**
- `nickname`：**全局唯一**（Section 3 由"账本内唯一"收紧），展示名 + 被引用键
- `current_ledger_id`：**永不为空**（FR-034）；可由显式切换指向**已删除账本**（合法只读，FR-036，兜底逻辑不复写）
- `default_ledger_id`：回落锚点（**新增**）

### `ledgers`
`id / name(允许重名) / owner_user_id / invite_code(UNIQUE) / created_at / deleted_at`
- `name`：**群名模型**，允许重名 → 名字不是唯一键，修复点在**选择机制**（FR-019）
- 展示层编号 = `id` 的 `#N` 形式，**不落库**（FR-015）
- `deleted_at`：软删除；已删账本**仍出现在列表**（带标记）并**占用编号**

### `ledger_members`
`id / ledger_id / user_id / role(owner|member) / joined_at` + `UNIQUE(ledger_id, user_id)`
- 成员名单稳定顺序 = `owner 优先，其余按 lm.id ASC`（FR-016），`_members_preview` 截断并返回总人数
- 复审：`get_my_ledgers` 也按 `lm.id ASC` 排序 → 两次查看顺序一致（FR-015）

### `join_requests`
`id / ledger_id / user_id / status / created_at` + `UNIQUE(ledger_id, user_id)`
- `list_pending_joins(openid, ledger_id=None)` 支持**按指定账本**查询（FR-026）
- `approve_join(openid, applicant_nickname, ledger_id=None)` 支持跨账本审批（FR-023）

### `transactions`
`id / type / amount / category / note / happened_at / created_at / ledger_id / created_by_user_id`
- 记账人展示：`query_by_ledger` **LEFT JOIN users** 取 `created_by_user_id` 的**当前** `nickname`（FR-032），**不带 openid**
- 写入到已删账本被拒（FR-030，工具层 + 数据层双重防御）

## Key Rules

### R1 身份与默认账本初始化（FR-002, FR-012, FR-013）
- `subscribe` 事件 → `get_or_create_user(openid)`（幂等，UNIQUE openid 兜底）
- 首次创建用户即 `_create_default_ledger`（名为「我的账本」、该用户为 owner）+ 设 `current_ledger_id` = `default_ledger_id`
- 兜底：关注事件丢失 / 历史存量用户 → 首条消息 `run_agent` 入口先 `get_or_create_user`，体验不中断（FR-002）

### R2 昵称全局唯一（FR-006~FR-010）
- `validate_nickname(openid, nick)` → `""` 合法 / 可展示错误消息（占用提示**不透露占用者**）
- `set_nickname` 落库；改名到自身当前昵称 → 视为成功；空/空白 → 不生效
- 校验规则：不含 `\r`/`\n`、`strip()` 后非空、长度 ≤ 20

### R3 账本列表（FR-015~FR-017）
- `get_my_ledgers(openid)` → 每本含 `id/name/invite_code/role/joined_at/is_current/is_default/is_deleted`
- 排序：`lm.id ASC`（加入顺序，稳定）；**包含已删除账本**
- 成员名单由 `ledger_member_preview(ledger_id)` → `{names, total}` 单独提供（owner 优先、截断标明总人数）；**展示拼装发生在 `agent.py` 的 `get_my_ledgers` 工具层**（db 层不做字符串拼装）

### R4 选择器解析（FR-018~FR-022）
- `resolve_ledger_selector(openid, selector) -> (ledger_id, err_text)`
  - `#N` / 裸 `N` → 精确匹配该用户已加入账本（含已删）；未命中 → `没有编号为 #N 的账本`
  - 名称唯一 → 直效
  - 名称多命中 → **不返回 id**，返回候选列表文本（编号 + 成员名单，实时重算）
  - 名称不存在 → `你还没有加入叫「X」的账本`
- `switch_ledger` 调它；命中已删账本 → 允许，回带"已被删除（只读）"提示

### R5 已删账本只读（FR-028~FR-030）
- 守卫 `is_ledger_deleted(ledger_id)`（不存在也算 True）嵌入：`admin_rename_ledger` / `admin_delete_ledger` / `reset_invite_code` / `admin_remove_member` / `approve_join` / `leave_ledger`
- 重复删除 → `该账本已被删除（只读），无需重复删除`，**原 `deleted_at` 不被覆盖**
- 记账 → 工具层 + `insert_many_for_ledger` 双重拒绝

### R6 回落（FR-033~FR-036）
- `_settle_current_ledger(conn, uid, dead_lid)`：**仅当** `current_ledger_id == dead_lid` 时改写为 `_fallback_ledger_id(uid, exclude=dead_lid)`
- `_fallback_ledger_id`：`default_ledger_id`（≠ exclude） → 最近加入的未删除账本 → `None`
- `get_user_ledger_id` **尊重显式选择**：指针存在且账本存在（**含已删**）→ 原样返回；否则走兜底链
- 删除账本时对**全部成员（含 owner 本人）**逐一 `_settle_current_ledger`（FR-033）

### R7 管理操作指定账本（FR-023~FR-027）
- 六个管理函数签名统一为 `(openid, <参数>, ledger_id: Optional[int] = None)`；`ledger_id is None` 时回退当前账本
- `agent.py` 侧工具额外接 `ledger_name`（编号/名称），内部经 `resolve_ledger_selector` 解析成 `ledger_id` 再下传
- 权限判定 `is_ledger_admin(openid, ledger_id)` 按**目标账本**角色（FR-027）

### R8 通知不丢（FR-037~FR-042）
- `apply_join` / `approve_join` 后由调用层（`agent.py` 工具内 `_notify_owner` / `_notify`）尝试推送
- 推送失败或生成异常 → `enqueue_undelivered`（FR-039/FR-040），申请流程**不被阻塞**
- owner 兜底：`_pending_joins_hint(openid)` 在 `run_agent` 内自动检查待审批并附加 system prompt 提示（查询异常静默忽略，绝不拖垮对话）
- 用户侧补发：`drain_undelivered` 在下一次对话取走并随回复给出

### R9 生命周期（FR-001~FR-005）
- 首次关注欢迎语 MUST 含五要素：① 记账示例 ② 建账本分享口令 ③ 凭口令申请加入 ④ 建议设置称呼 ⑤ 告知当前处于默认账本
- 再关注（同 openid 已有用户）→ "欢迎回来" + 当前账本概况
- 取关 → **零数据变更**（仅日志）；其为 owner 的共享账本对成员照常可用

## State Transitions

```
users.default_ledger_id:
  (NULL 存量) --init 回填--> 未删除账本（我的账本优先/最近加入）
  指向账本 --改名--> 指向不变
  指向账本 --被删除--> 重建空「我的账本」并改指向（FR-013，需 FR-014 确认）

users.current_ledger_id:
  值 --被移除/退出/所在账本被删--> 回落自己的默认账本（FR-033，永不为 NULL）
  值 --显式切换（含切入已删账本）--> 新值，兜底逻辑不复写（FR-036）
  NULL/悬空（脏数据）--读时--> 默认账本 → 最近加入 → 明确报错（FR-035）

ledgers.deleted_at:
  NULL --owner 删除--> 时间戳（软删除，原值不被二次覆盖）
  已删除 --再次删除--> 拒绝（提示已删除，时间戳不变）

undelivered_notices:
  (空) --推送失败/生成异常--> 落库待补发 --下次对话 drain--> 取走并删除（补发）
```

```
join_requests.status（沿用 002，本次不新增状态）:
  (无) --apply_join--> pending --approve_join--> approved
                                  \--reset_invite_code--> expired
                                  \--(拒绝，预留)--> rejected
```
