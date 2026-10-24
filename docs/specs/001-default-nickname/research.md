# Research & Decisions: default-nickname

## Decision Summary

| # | Decision | Rationale | Alternatives Considered |
|---|---|---|---|
| D1 | 默认昵称后缀用 **4 位随机 hex**（`secrets` 或 `uuid4().hex[:4]`） | 冲突概率低（16^4=65536），简短易读；用户已确认 4 位足够 | 更长 hex（更防撞但更长）；纯数字（可预测）|
| D2 | 默认昵称**落库到 `users.nickname`**（生成时写入，非仅展示时拼） | 保持一致（`set_nickname` 也是写这里），"用户级一个"自然成立 | 仅展示时临时拼（不落库，但每次重新随机会跳变，违背"用户级一个"）|
| D3 | 默认昵称**首次展示时生成并落库**（不做批量迁移） | 旧用户 nickname 为空 → 首次展示按需生成；不做全表迁移，简单、不碰旧数据 | 初始化批量迁移（复杂度高，且改全表数据有风险）|
| D4 | 不区分"默认/自设"昵称（不加 is_default 标记） | 默认昵称就是 nickname 的值，用户设置直接覆盖。符合"默认/自设都在 nickname 字段" | 加 is_default_nickname 标记（更精细，但增加字段和复杂度，MVP 不必）|
| D5 | 唯一性校验：生成时若与账本内现有昵称冲突，**重生成直到唯一** | 满足 constitution II（账本内唯一）| 拒绝并报错（体验差）；加数字后缀（非纯随机）|
| D6 | `list_ledger_members` **不返回 openid**（改 SELECT） | 从源头杜绝 openid 落出，agent 层无法 fallback | 保留 openid 但 agent 层不显示（源头仍可能泄露，不如断绝）|

## Resolved Unknowns

- **旧数据（nickname 为空）怎么处理？** → D3：首次展示时生成落库。不做批量迁移。
- **默认昵称算不算正式昵称？** → D4：算，就是 nickname 的值，用户设置直接覆盖。
- **撞名怎么办？** → D5：重生成直到账本内唯一。
- **要不要保留 openid 返回值？** → D6：不返回，从源头断绝。

## Constitution Compliance

- 原则 I（禁止显示 openid）：D6 从源头移除 openid，✅
- 原则 II（账本内唯一）：D5 唯一校验，✅
- 原则 III（默认昵称用户级一个/永不为空）：D2/D3 落库 + 首次生成，✅
- 原则 IV（可改不追溯）：D4 直接覆盖，✅
