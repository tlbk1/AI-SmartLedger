# Research & Decisions: shared-ledger

## Decision Summary

| # | Decision | Rationale | Alternatives Considered |
|---|---|---|---|
| D1 | **新增 `join_requests` 表**（id, ledger_id, user_id, status, created_at, UNIQUE(ledger_id,user_id)）| 承载"待审批"状态，不改现有表语义；UNIQUE 保证幂等（FR-010）| 在 ledger_members 加 status 字段（污染成员表语义，成员≠申请）|
| D2 | **权限判定改签名**：`is_ledger_admin(openid, ledger_id=None)`，显式传账本；不传时回退当前账本（兼容）| 修"当前账本误判"缺陷（FR-004）——用户可能在 L1 是 owner、L2 是 member | 只靠调用方先 switch_ledger（脆弱，用户忘了切就误判）|
| D3 | **账目共享**：查询天然不过滤 created_by（现状即所有成员可见全部账目）；**新增记账人昵称关联**展示 | FR-011：账目本来就共享，只差"显示谁记的"；transactions 已有 created_by_user_id | 只在查账时 join users 取 nickname（选这个，最小改动）|
| D4 | **审批通知"尽力而为"**：owner 同意前→推 owner；同意后→推申请人。推送失败入 undelivered；**兜底**：任何对话时检查并提示待审批 | 客服消息 48h 限制（见 spec Assumptions）；保证审批不卡死 | 只靠推送（48h 外失效，审批卡死）|
| D5 | **口令重置**：生成新口令 + 把该账本 `status='pending'` 的申请全部置 `expired` | spec US9 场景3；避免旧口令下提交的申请在新口令下仍有效 | 重置只换口令、不动作废申请（留下语义混乱）|
| D6 | **删账本后回落**：软删除账本 + 把该账本成员的 `current_ledger_id` 重指到其默认账本 | FR-014；保证用户不会停在"已删除账本"上 | 只标记删除、不管 current_ledger（用户卡在死账本）|
| D7 | **owner 保护**：移除成员/退出时校验目标是 owner → 拒绝 | Edge Case：owner 不可被移除/退出 | 允许移除 owner（会产生无主账本）|
| D8 | **单账本免确认**：管理操作时若用户只有一个账本，直接执行；多账本才要求指定 | spec US7 场景3（体验优化）| 一律要求确认（单账本也问，啰嗦）|

## Resolved Unknowns

- **推送不到 owner 怎么办？** → D4 兜底：owner 任何对话都检查待审批并提示。
- **权限怎么判定才不误判？** → D2：显式传目标账本。
- **账目共享要不要改数据？** → D3：不用，本来就共享，只需展示记账人。
- **口令重置要不要管旧申请？** → D5：作废（置 expired）。
- **删账本后成员停在哪？** → D6：回落默认账本。

## Constitution Compliance

I 隔离✅ / II 身份✅ / VI 权限(D2)✅ / VII 审批(D1+D4)✅ / 2.1 口令(D5)✅ / 2.2 成员变更(D7)✅ / 2.4 管理目标(D8)✅ / 2.5 兜底(D4)✅ / 4.1 软删除(D6)✅ / 4.2 幂等(D1 UNIQUE)✅
