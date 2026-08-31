# 共账 × Agent 设计方案（口令制）

> 目标：让两人（或多人）共用账本，用 openid 区分「谁记的」，口令制加入账本。
> 沿现有 agent 循环展开：给 agent 增加「账本管理」工具，openid 作为工具参数传入（数据隔离可靠）。

---

## 一、核心概念

- **user**：一个人（靠微信 openid 唯一标识）
- **ledger**：一个账本（如「我们家」）
- **ledger_members**：谁在哪个账本（核心，一个账本可有多个用户 = 共账）

改造后：任何一笔交易都属于**某个账本**，且记录**是谁（user_id）记的**。

---

## 二、数据模型（新增 + 改造，平滑演进）

### 现有 `transactions` 表 —— 加 2 个关联字段（可空，兼容旧数据）

```sql
ALTER TABLE transactions ADD COLUMN ledger_id INTEGER;      -- 所属账本（NULL = 旧数据，归默认账本）
ALTER TABLE transactions ADD COLUMN created_by_user_id INTEGER;  -- 谁记的（NULL = 旧数据）
```

### 新增 3 张表

```sql
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    openid     TEXT UNIQUE NOT NULL,      -- 微信唯一标识
    nickname   TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledgers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,            -- 账本名，如「我们家」
    owner_user_id INTEGER,                -- 创建者
    invite_code TEXT UNIQUE,              -- 邀请口令（口令制核心）
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_members (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_id INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    role      TEXT DEFAULT 'member',      -- owner / member
    UNIQUE(ledger_id, user_id)            -- 一人在一个账本最多一条
);
```

---

## 三、agent 新增工具（openid 作为参数传入）

现在 agent 工具：`query_transactions` / `record_transactions` / `ask_clarify`。
共账后：**每个工具都接收 openid**，用于定位用户 → 找到所属账本 → 数据隔离。

### 新增的账本管理工具

| 工具 | 签名 | 作用 |
|---|---|---|
| `create_ledger` | `(openid, name) -> str` | 建账本，自动把创建者设为 owner，返回邀请口令 |
| `join_ledger` | `(openid, invite_code) -> str` | 凭口令加入账本，成为 member |
| `get_my_ledgers` | `(openid) -> str` | 列出我加入的所有账本 |

### 改造现有工具（加 openid 参数）

```python
@tool
def record_transactions(openid: str, transactions: list) -> str:
    """记账。自动用 openid 定位用户所属账本。"""

@tool
def query_transactions(openid: str, date_from, date_to, ...) -> str:
    """查账。用 openid 定位用户所属账本。"""
```

### openid 传递方式

- **工具参数带 openid**（主，可靠）：每个账本/记账/查账工具都接收 openid，执行代码真正能定位用户。
- **注入提示词**（辅，让 agent 更自然）：在 system prompt 里提"当前用户 openid = {openid}"，但**不依赖它做隔离**。

---

## 四、交互流程（口令制）

### 场景1：建账本
```
用户发：「建个账本叫我们家」
agent → 调 create_ledger(openid, "我们家")
返回：「✅ 已建账本「我们家」，你的邀请口令是 abc123」
```

### 场景2：加入账本
```
朋友发：「加入账本 abc123」
agent → 调 join_ledger(openid, "abc123")
返回：「✅ 你已加入「我们家」，从此咱俩账目记一起」
```

### 场景3：记账（带账本隔离）
```
任一成员发：「午饭35」
agent → 调 record_transactions(openid, [...])
→ 用 openid 定位所属账本，记入该账本 + 标记 created_by_user_id
```

### 场景4：查账（只看自己账本）
```
成员发：「这个月花了多少」
agent → 调 query_transactions(openid, ...)
→ 只查 openid 所属账本的记录
```

---

## 五、数据隔离的核心逻辑（db 层）

### 关键函数：openid → 所属账本

```python
def get_user_ledger_id(openid: str) -> int:
    """根据 openid 找用户，返回他所属的账本 id。
    简化：一个用户可能加入多个账本，MVP 先假设主要账本（或最近加入的）。
    """
```

### 记账时隔离
```python
def insert_many(ledger_id, created_by_user_id, txns):
    # 每笔都带上 ledger_id + created_by_user_id
```

### 查账时隔离
```python
def query(ledger_id, date_from, date_to, ...):
    sql = "SELECT * FROM transactions WHERE ledger_id = ? AND ..."
```

---

## 六、与现有 agent 的衔接

- **保留** `ask_clarify`、系统提示
- **新增** 3 个账本工具 + 改造现有 2 个工具（加 openid）
- **prompt** 更新：告诉 agent 有账本工具，如何用（建账本/加账本/记账/查账都要先知道 openid 对应账本）

---

## 七、待确认问题

- [ ] 一个用户加入多个账本时，记账默认归哪个？(MVP: 最近加入的 / 或让 agent 指定)
- [ ] `invite_code` 生成规则（简短易输，如 6 位字母数字）
- [ ] 老数据（ledger_id NULL）迁移：是否归入默认账本？

---

## 八、开发顺序

1. **db.py**：新增 3 张表 + transactions 加字段 + 新增 openid→账本 工具函数
2. **agent.py**：新增 3 个账本工具 + 改造现有工具加 openid
3. **prompt**：更新系统提示，教 agent 用账本工具
4. 测试：建账本 / 加入 / 记账隔离 / 查账隔离
