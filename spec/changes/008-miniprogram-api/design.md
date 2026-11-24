# 技术设计：008-miniprogram-api

## 当前状态

- 唯一入口：`main.py` 的 `GET/POST /wechat`（验签、XML、4.5s 窗口、超时转客服消息）。
- 编排：`router.process_message(openid, content) -> str`——补投未送达 → `agents.react_agent.run_agent` → 哨兵 `AGENT_FAILURE` 时走 `agents.fallback_agent` 兜底链。**返回值只有自然语言文本**，无结构化数据。
- 身份模型：`users(id, openid UNIQUE NOT NULL, nickname, created_at, current_ledger_id)`；`ledger_members(ledger_id, user_id, role, UNIQUE(ledger_id,user_id))`；`transactions(..., ledger_id, created_by_user_id)`。业务表全部按 **user_id** 关联，不存 openid。
- 可复用只读域函数：`get_user_ledger_id`、`get_ledger_info`（id/name/is_deleted）、`get_my_ledgers`（含 role/is_current）、`list_ledger_members`、`query_by_ledger`（返回行含 id，已剔除 openid 与 created_by_user_id）、`sum_by_ledger`。

## 方案与数据流

### 挂载

```
ai_smartledger/api/__init__.py   # api_router = APIRouter()；from . import routes
ai_smartledger/api/routes.py     # 三个端点
main.py                          # from .api import api_router; app.include_router(api_router, prefix="/api")
```

`main.py` 已有 `from . import router`（编排层），故 api 的变量名用 `api_router` 避免覆盖。`/wechat`、`/health` 定义与顺序不变。

### 三个接口契约

**GET /api/ping** → 200

```json
{"ok": true, "service": "ai-smartledger", "version": "0.8.0", "time": "2026-10-29T21:50:00+08:00"}
```

不触库、不调外部。`version` 取 `ai_smartledger.__version__`（新增常量）。

**POST /api/record** 请求 `{"user_id": "...", "text": "..."}` → 200

```json
{"ok": true,
 "reply": "✓ 已记 1 笔（当前账本：我的账本）",
 "count": 1,
 "recorded": [{"id": 12, "type": "expense", "amount": 35.0, "category": "餐饮",
               "note": "午饭", "happened_at": "2026-10-29T12:30:00+08:00",
               "created_by_nickname": "小王"}],
 "ledger": {"id": 1, "name": "我的账本", "role": "owner"}}
```

处理顺序：
1. 校验 `user_id`/`text` 非空，否则 400 `{"ok": false, "error": "..."}`（不调编排层）。
2. **快照**：`before_ids` = 该用户**所有既有账本**的事务 id 集合（`get_my_ledgers` → 各账本 `query_by_ledger(1970-01-01, 明天, limit=SNAPSHOT_LIMIT)` 取 id）。覆盖所有账本是为了兜住「本条消息里切换账本并记账」的路径。
3. `reply = router.process_message(user_id, text)`（同步；内部含建用户、记账、查询、闲聊与兜底链）。
4. 取 `ledger_id = get_user_ledger_id(user_id)`；`rows = query_by_ledger(ledger_id, 宽窗口, limit=SNAPSHOT_LIMIT, order_by="recent")`；`recorded = [r for r in rows if r["id"] not in before_ids]`。
5. `role` 从 `get_my_ledgers(user_id)` 中 `is_current` 那条取；组装响应。

**GET /api/ledger?user_id=** → 200

```json
{"ok": true,
 "ledger": {"id": 1, "name": "我的账本", "is_deleted": false, "role": "owner"},
 "members": [{"id": 1, "nickname": "小王", "role": "owner"}]}
```

`user_id` 缺失 → 400。未知用户 → 200 + `{"ok": true, "ledger": null, "members": []}`，**不调用 `get_or_create_user`**（守住「api 不直接写」）。`members` 直接取 `list_ledger_members(user_id)`（该函数保证不含 openid）。

### 分层纪律的落地

- `api/routes.py` 只 import：`router.process_message` 与上述只读域函数、`__version__`、时间工具。
- 写操作全部在 `router`/`agent`/工具内部发生；api 层零写调用（验收用静态检查）。
- 业务规则（分类集合、金额口径、合计逻辑、权限判定）一律不复制。

## 接口与兼容性

- 新增路由前缀 `/api`，FastAPI 路径与既有 `/wechat`、`/health` 不冲突；后两者代码与响应不变。
- 响应字段只用业务键（id/name/role/amount/category/...），**不含 openid**；`query_by_ledger` 与 `list_ledger_members` 已在域层保证。
- 时间：`ping.time` 与账目 `happened_at` 均为 Asia/Shanghai ISO 串（与既有存储一致）。

## 配置、常量与依赖注入

- `__version__ = "0.8.0"`：服务版本标识（`ai_smartledger/__init__.py`），非环境差异值。
- 快照窗口：`SNAPSHOT_FROM = "1970-01-01"`、`SNAPSHOT_LIMIT = 100_000` 命名常量（附注释：个人账本量级远低于此；超限时改用 db 只读增量函数）。
- 无新增环境变量、URL、密钥；不引入新依赖（FastAPI/httpx 已有）。

## 身份与跨端绑定方案（本 spec 必须回答的设计问题）

**问题**：公众号与小程序 appid 不同 → 同一自然人两个 openid → `users` 两行 → 两套账本/成员关系。

**关键结构事实**：所有业务表按 `user_id` 关联；只要两个 openid 映射到同一 `users.id`，`ledger_members`/`transactions` **零改动**即可共享数据。因此方案的差异只在「如何把第二个身份挂到同一行」以及「存量用户如何提供匹配源」。

### 选项 1：手机号（用户建议）

流程：小程序 `button open-type="getPhoneNumber"` → 前端把 code 交给服务端 → 服务端调 `POST https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token=...`（httpx 即可，无新依赖）→ 得手机号 → 按 `users.phone` 查/建用户。

表结构影响：
- `users` 新增 `phone TEXT UNIQUE`（可空）；
- 小程序身份需另存：`users.mini_openid TEXT UNIQUE`（可空，最小改动）或新增 `user_identities(user_id, platform, external_id)` 身份表（更规范，改动更大）。
- 存量数据：无需迁移（新列可空）。

存量兼容：**不成立**。公众号侧拿不到用户手机号，存量 `users` 行 `phone` 恒为 NULL，小程序登录时没有匹配源，只能新建用户——同一人仍是两套数据。要成立必须另做「公众号内让用户报手机号」的能力（新增第 18 个工具或提示词规则），超出本次「不改 17 工具、不改提示词」的约束。
环境要求：`getPhoneNumber` 需已认证小程序；个人/测试号不可用。

### 选项 2：绑定码（推荐）

流程：
1. 小程序首次 `wx.login` → 服务端 `code2session` 得 `mini_openid`；生成 6 位一次性绑定码（10 分钟有效）并返回 `{bind_code}`。
2. 用户在**公众号**里发「绑定 123456」。
3. 公众号侧在 `router.process_message` 前置一个精确匹配分支（不进 agent）：命中则调绑定函数把 `mini_openid` 写入该公众号用户的 `users.mini_openid`，回复「已绑定」；不命中照旧走 agent。
4. 之后小程序请求携带 `mini_openid` → `SELECT ... WHERE openid=? OR mini_openid=?` → 同一 `users.id`。

表结构影响：
- 新增 `bind_codes(code TEXT PRIMARY KEY, mini_openid TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT)`；
- `users` 新增 `mini_openid TEXT UNIQUE`（可空）；
- 存量数据：**零迁移**（老用户行不变，绑定只是给该行新列赋值，不触碰任何业务表、无数据合并）。

策略取舍：「先绑后用」可完全避免数据合并（绑定前小程序不建账本、不记账，只拿绑定码）；若允许「小程序独立开户后再绑公众号」，就必须把小程序用户名下的账本/成员/账目改挂到公众号用户，属不可逆数据迁移，本 Skill 明确禁止——因此推荐先绑后用，新用户（从未用过公众号）在绑定流程里引导其先关注公众号完成开户。

公众号侧改动成本：`router.py` 一个前置分支（不动 17 工具、不动提示词、不动主链）。

### 选项 3：unionid（生产标准，当前不可用，不实现）

公众号与小程序绑定同一开放平台账号后，`unionid` 相同 → `users` 增加 `unionid TEXT UNIQUE` 即可自动合并，用户零操作。当前微信测试号不支持绑定开放平台，故不采用；将来具备条件时是首选，且与选项 2 可并存（unionid 优先、绑定码兜底）。

### 汇总

| 方案 | users 改动 | 新表 | 存量迁移 | 解决跨端 | 环境要求 |
|---|---|---|---|---|---|
| 本次（不绑定，仅接口） | 无 | 无 | 无 | 否 | 无 |
| 选项 1 手机号 | +phone、+mini_openid（可空） | 无 | 无（老用户无法匹配） | 仅小程序内部 | 小程序需认证 |
| 选项 2 绑定码 | +mini_openid（可空） | bind_codes | 无 | **是** | 无 |
| 选项 3 unionid | +unionid（可空） | 无 | 回填可选 | 是 | 需绑定开放平台 |

**结论**：本次实现零 schema 变更；建议 009 按选项 2 落地（真正解决跨端、存量零迁移、无新依赖），手机号作为小程序内部账号识别补充；具备条件后升级 unionid。

## 备选方案与取舍

1. **`/api/record` 增量识别**：
   - 采用：调用前后 id 差集（零 db 改动，满足「不改 db 层」）。
   - 备选：在 `db/transactions.py` 新增只读函数 `list_transactions_after(ledger_id, after_id)`——更精确更低成本，但需放宽「不改 db 层」（只增不改）。**列为待决问题 2**。
   - 拒绝：api 层直连 sqlite（违反「全部经由现有域函数」）。
2. **返回结构化结果的方式**：采用「编排层照旧返回文本 + api 读回增量」；拒绝「给 `process_message` 加回调/改工具收集结果」（会改主链或 17 工具）。
3. **`/api/ledger` 对未知用户**：采用 200 + `ledger: null`（前端免异常分支）；拒绝 404（需要额外前端错误处理）与「自动建用户」（违反 api 不写）。
4. **鉴权**：本次不做（用户明确）；以「user_id 由调用方提供」为信任边界，风险写入 proposal。

## 失败模式、安全与回滚

- `process_message` 内部异常已被 router 的哨兵/兜底覆盖，api 另加 try 兜底返回 500 + 通用错误文案（不回显堆栈）。
- 参数校验失败 400，不触库。
- 快照成本：单次调用两次宽窗口查询；个人账本（万笔级）可接受；超 `SNAPSHOT_LIMIT` 时 `recorded` 可能漏识别——作为已知局限记录，待问题 2 解决。
- 并发：同一 user 并发两次 record 时增量可能交叉；单端使用场景可接受，记录为已知局限。
- 安全：无鉴权 → 仅用于内网/可信网关后；响应不含 openid；日志不打印完整 text（沿用 `content[:30]` 风格）。
- 回滚：新增包 + main 一行 + 测试文件，`git revert` 即可；无 schema 变更、无数据迁移。

## 数据迁移

`none`。本变更不新增/修改任何表结构。如需要数据库结构或不可逆数据迁移，停止本 Skill 流程。
