# Data Model: shared-ledger

## New Table

### `join_requests`（加入申请）

| Field | Type | Constraint | Notes |
|---|---|---|---|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| ledger_id | INTEGER | NOT NULL | 目标账本 |
| user_id | INTEGER | NOT NULL | 申请人 |
| status | TEXT | NOT NULL DEFAULT 'pending' | `pending`/`approved`；`expired`（口令重置作废）；`rejected` 预留 |
| created_at | TEXT | NOT NULL | ISO 8601 |

**约束**：`UNIQUE(ledger_id, user_id)` —— 保证同一人对同一账本只有一条申请（FR-010 幂等）。

**索引建议**：`(ledger_id, status)` 便于查某账本待审批列表。

## Existing Tables (复用, 不改语义)

### `users`
`id / openid(UNIQUE) / nickname / created_at / current_ledger_id`
- current_ledger_id：删账本后回落默认账本（FR-014）

### `ledgers`
`id / name / owner_user_id / invite_code(UNIQUE) / created_at / deleted_at`
- deleted_at：软删除（4.1）
- invite_code：口令重置时更新；旧口令失效

### `ledger_members`
`id / ledger_id / user_id / role(owner|member) / joined_at`
- UNIQUE(ledger_id, user_id)
- 审批通过 = 插入一条 role='member'；移除/退出 = 删除该行（历史账目**不动**）

### `transactions`
`id / type / amount / category / note / happened_at / created_at / ledger_id / created_by_user_id`
- 账目共享：查询不按 created_by 过滤 → 账本内成员都能看到全部（FR-011）
- 记账人展示：JOIN users 取 `created_by_user_id` 的 nickname（**不取 openid**）

## Key Rules

### R1 申请加入（FR-001, FR-010, FR-015）
- 凭口令找账本；无效口令 → 提示不存在
- 已在账本 → 提示已是成员；已在 pending → 幂等不重复
- 插入 `join_requests(status='pending')`
- 申请人**不可**见账本任何数据（未成为 member）
- 申请人可查自己的申请状态

### R2 owner 同意（FR-002, FR-009, FR-016）
- 校验操作者是**该账本** owner（D2）
- `pending` → `approved`，插入 `ledger_members(role='member')`
- 尽力推送通知申请人（失败入 undelivered）
- owner 可查全部待审批（含申请人 nickname）

### R3 权限判定（FR-003, FR-004）
- `is_ledger_admin(openid, ledger_id)`：查该 openid 在该 ledger_id 的 role 是否 owner
- 所有管理操作显式传目标账本

### R4 移除 / 退出（FR-005, FR-012, D7）
- 移除（owner 操作）：删除 ledger_members 行；**目标若是 owner → 拒绝**
- 退出（member 自操作）：删除自己的行；**owner 不能退出** → 拒绝
- 两者都**不动 transactions**（历史账目保留）

### R5 口令重置（FR-013, D5）
- 校验 owner → 生成新 invite_code → 该账本 `pending` 申请全部置 `expired`

### R6 删账本（FR-014, D6）
- 软删除（deleted_at）
- 该账本成员的 current_ledger_id → 回落各自默认账本
- 历史账目仍可查（带"已删除"提示）

## State Transitions

```
join_requests.status:
  (无) --申请--> pending --owner同意--> approved
                     \--口令重置--> expired
                     \--(拒绝, 预留)--> rejected
```

```
ledger_members: 
  (无) --owner同意--> member --移除/退出--> (无，但 transactions 保留)
                   --创建账本--> owner --删账本--> (软删除账本)
```
