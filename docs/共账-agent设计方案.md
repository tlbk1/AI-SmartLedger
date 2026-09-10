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

> ⚠️ **安全进化（任务1）**：openid 已从 LLM 收回——改为**工具工厂闭包注入**（`make_tools(openid)`），LLM 的工具 schema 里**不出现 openid**，杜绝身份冒充。此文档的"工具带 openid"是早期表述，当前实现是闭包注入（更安全）。

### nickname（成员昵称）约定

- **默认昵称**：用户未设置时，系统自动生成「账本成员 + 4位随机 hex」（如"账本成员 a3f9"）并**落库**到 `users.nickname`，用户级一个（各账本相同）。
- **绝不显示 openid**：任何展示成员处（成员列表/按昵称移除）只用 nickname（默认或自设），`list_ledger_members` 的 SELECT 不返回 openid——从源头杜绝身份泄露。
- **自设替换**：`set_nickname` 覆盖默认昵称，立即生效；空/空白昵称不生效。
- **账本内唯一**：默认昵称生成时校验账本内冲突，冲突重生成。

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

---

## 九、当前实现（spec 002-shared-ledger 定稿）

> ⚠️ 本章记录**已实现**的设计，**取代**第四节（口令直接加入）与第七节（待确认问题）的早期表述。

### 9.1 加入流程：审批制（口令申请 → owner 同意）

早期设计是"凭口令直接成为 member"。**当前实现改为审批制**（`constitution VII`）：

```
① 申请人：发口令       → apply_join()      → join_requests 落一条 pending（幂等 UNIQUE）
② 系统  ：尽力推送 owner（48h 窗口内；失败静默跳过，不阻塞）
③ owner ：任何一次对话  → 系统自动检查并顺带提示「有 N 条待审批」（兜底保底）
④ owner ：「同意 小王」  → approve_join()   → pending→approved + 写 ledger_members
⑤ 系统  ：尽力推送申请人「已加入」（失败入 undelivered，下次对话补发）
```

关键约束：
- **审批前申请人看不到账本任何数据**（`is_ledger_member` 为假，查账走不到该账本）。
- 重复申请**幂等**（`join_requests` 上 `UNIQUE(ledger_id, user_id)`）。
- 本版**只做"同意"**，不做"拒绝"（`rejected` 为预留状态值，YAGNI）。
- 被移除者**可以重新申请**（不复用旧的 approved 记录判定"已是成员"）。

### 9.2 两级权限：owner vs member

| 操作 | owner | member |
|---|---|---|
| 记账 / 查账 / 看成员列表 / 设昵称 / 切账本 | ✅ | ✅ |
| 审批加入 / 移除成员 / 改账本名 / 删账本 / 重置口令 | ✅ | ❌（工具层拒绝） |
| 退出账本 | ❌（只能删账本，单 owner 模型） | ✅ |

**权限按"目标账本内的角色"判定**（`is_ledger_admin(openid, ledger_id)`）——
X 在 L1 是 owner、在 L2 是 member，则 X 对 L2 的管理操作**被拒**（修掉了早期"按当前账本误判"的缺陷）。

### 9.3 共享账目 + 记账人展示

- 账本是**共享**的：任一成员查账能看到**该账本全部账目**（含他人记的）。
- `query_by_ledger` 用 `LEFT JOIN users` 附上 `created_by_nickname`（**绝不返回 openid**）。
- 摘要（`summarize_query_result`）在涉及"谁记的"时点出记账人昵称。
- 默认昵称**创建用户时即刻生成**（「账本成员 + 4位 hex」，账本内校验唯一），保证 owner 看待审批列表时申请人有名字可按名同意。

### 9.4 成员变更：移除 / 退出（历史账目保留）

- 移除（`admin_remove_member`）/ 退出（`leave_ledger`）只删 `ledger_members` 关系行，
  **不动 `transactions`**——被移除者的历史账目仍留在账本内、归属不变。
- 被移除/退出者 `current_ledger_id` **回落到其默认账本**（否则会"悬空指向"原账本，形成隔离泄漏）。
- owner **不可被移除、不可退出**（单 owner 模型）。

### 9.5 口令重置

`reset_invite_code(openid, ledger_id=None)`：生成新口令 → 旧口令立即失效 →
该账本**所有 pending 申请置 `expired`**（作废），不再出现在待审批列表。

### 9.6 删账本（软删除）与"已删账本只读"

- 删账本 = **软删除**（`ledgers.deleted_at`），账目一条不删。
- 删除时：该账本所有成员的 `current_ledger_id` **回落各自默认账本**。
- **已删账本仍可查看历史**：仍在"我的账本"列表里（标记 `已删除`）、可切进去（只读），
  查账结果带 `ledger_deleted` 标记 + 「该账本已被删除」提示。
- **只读**：向已删账本记账在**工具层**（友好拒绝）和 **db 层**（`insert_many_for_ledger` 拦截）双重拒绝。
- 已删账本的口令**失效**（不能用旧口令申请）。

### 9.7 通知策略：尽力而为 + 兜底保底

微信客服消息**只能发给 48 小时内互动过的用户**，所以推送不可靠：

- 推送一律 **try + 失败入 `undelivered`**，绝不阻塞主回复（丢后台线程）。
- **兜底**：owner 每次对话，`run_agent` 自动检查待审批并注入提示，让 agent 顺带提一句
  （`agent._pending_joins_hint`）——不依赖 owner 主动查询。

### 9.8 安全与一致性要点（实现细节）

- **openid 走工具工厂闭包**：`make_tools(openid)`，LLM 的工具 schema 里**没有 openid**（防 prompt injection 冒充身份）。
- **幂等建表/加列**：`init()` 用 `_add_column_if_missing`，升级不炸老库。
- **防孤儿数据**：`ledger_id` 为 NULL 的写入被拒（`insert_many_for_ledger`）。
- **UNIQUE 约束**：`join_requests(ledger_id, user_id)` 保证申请幂等。

### 9.9 验证

| 验证 | 结果 |
|---|---|
| 单元/集成测试（`pytest tests/ -q`） | **56 passed** |
| 端到端场景（`scripts/e2e_shared_ledger.py` V1–V16） | **16/16 通过** |
| `hermes verify`（bootstrap / test / readiness） | **三阶段全绿** |

自动化验证入口：

```bash
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q
PYTHONPATH= .venv/Scripts/python.exe scripts/e2e_shared_ledger.py
```

### 9.10 与早期章节的差异

| 早期表述 | 当前实现 |
|---|---|
| 第四节：`join_ledger` 凭口令**直接加入** | 改为**申请**（pending），owner 同意后才成为 member |
| 第三节：工具签名带 `openid` 参数 | 改为**闭包注入**（`make_tools(openid)`），schema 无 openid |
| 第七节：待确认问题 | 已定：多账本→默认账本+显式切换；口令→6 位字母数字随机；旧 NULL 数据→已清理 |
