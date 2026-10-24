# Data Model: default-nickname

## Entities

### User (`users` 表，已存在)

| Field | Type | Constraint | Notes |
|---|---|---|---|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| openid | TEXT | UNIQUE NOT NULL | 微信唯一标识；**永不对外展示**（原则 I）|
| nickname | TEXT | 可为空，但**从不以空对外展示** | 存默认昵称（如"账本成员 a3f9"）或自设昵称 |
| created_at | TEXT | NOT NULL | |

> 不改表结构——`users.nickname` 已存在。只加"生成默认昵称 + 落库"逻辑。

## Key Rules (from spec + constitution)

### R1: 默认昵称生成
- 形式：`账本成员` + 4 位随机 hex（如 `账本成员 a3f9`）
- 生成时：若 nickname 为空（视为从未设置）→ 生成并**写入** `users.nickname`
- 用户级一个：一个用户一个默认昵称（存 `users.nickname`，跨账本相同）

### R2: 账本内唯一
- 生成默认昵称时，检查同账本内其他成员的 nickname；冲突 → 重生成直到唯一
- 自设昵称 `set_nickname` 时同样校验账本内唯一（原则 II）

### R3: 自设替换默认
- `set_nickname(openid, "小王")` → 直接覆盖 `users.nickname`，不做区分标记

### R4: 空昵称不生效
- nickname 为空白/空字符串 → 视为未设置，保留默认昵称（或触发默认昵称生成）

### R5: 不显示 openid
- `list_ledger_members` 的 SELECT **不再返回 openid**（根治）
- 任何展示成员处只用 nickname（默认或自设）

## State Transitions

```
nickname = NULL ("从未设置")
    │  首次展示 / 校验时触发
    ▼
nickname = "账本成员 a3f9" (默认昵称, 已落库)
    │  set_nickname("小王")
    ▼
nickname = "小王" (自设, 覆盖默认)
```

- NULL → 默认昵称：展示时按需生成并落库（D3）
- 默认昵称 → 自设：`set_nickname` 直接覆盖（D4，不加标记）
- 自设昵称 → 改自设：`set_nickname` 再次覆盖
