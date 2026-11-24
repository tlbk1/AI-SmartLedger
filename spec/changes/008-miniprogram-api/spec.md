# 行为规格：008-miniprogram-api

## ADDED Requirements

### REQ-008_MINIPROGRAM_API-001：连通性与版本接口

系统必须提供 `GET /api/ping`，返回 200 与 JSON：`ok`（true）、`service`（固定标识）、`version`（服务版本常量）、`time`（Asia/Shanghai ISO 时间）。该接口不得触达数据库或外部服务。

#### Scenario：服务在线

- **前置条件**：服务已启动。
- **当**：请求 `GET /api/ping`。
- **则**：200，`ok=true`，`version` 非空，`service` 为固定标识；无数据库写入。

### REQ-008_MINIPROGRAM_API-002：文本记账接口

系统必须提供 `POST /api/record`，请求体 `{user_id: str, text: str}`。处理必须**经 `router.process_message(user_id, text)`** 完成解析与入库（复用主链/兜底链，不重写业务），并在响应中返回结构化结果：`reply`（编排层原文）、`recorded`（本次新增账目数组，元素含 id/type/amount/category/note/happened_at/created_by_nickname）、`count`、`ledger`（当前账本 id/name/role）。

#### Scenario：一句记账成功

- **前置条件**：`user_id` 已存在或可被自动建立（复用编排层的既有行为）；当前账本内账目数为 N。
- **当**：`POST /api/record`，`text="午饭35"`。
- **则**：200，`count=1`，`recorded[0]` 的 type=expense、amount=35、category 落在预设分类内；查库确认账目数为 N+1；`reply` 为编排层返回的文本。

#### Scenario：非记账内容不产生账目

- **前置条件**：当前账本内账目数为 N。
- **当**：`POST /api/record`，`text="你好"`（闲聊/查询/反问类）。
- **则**：200，`recorded=[]`、`count=0`，`reply` 为非空文本；查库确认账目数仍为 N。

#### Scenario：参数不合法

- **前置条件**：服务已启动。
- **当**：请求体缺 `user_id` 或 `text`，或二者为空串。
- **则**：400 + JSON 错误说明；不调用编排层、无任何数据库写入。

### REQ-008_MINIPROGRAM_API-003：当前账本与成员接口

系统必须提供 `GET /api/ledger?user_id=`，返回 JSON：`ledger`（当前账本 id/name/is_deleted/role）、`members`（数组，元素含 id/nickname/role）。数据必须来自只读域函数（`get_user_ledger_id`/`get_ledger_info`/`get_my_ledgers`/`list_ledger_members`），不得包含任何 openid。

#### Scenario：既有用户查询

- **前置条件**：`user_id` 已存在且拥有默认账本。
- **当**：`GET /api/ledger?user_id=...`。
- **则**：200，`ledger.id` 与 `ledger.name` 非空、`role=owner`；`members` 至少含本人；响应中不出现 openid。

#### Scenario：未知用户不写库

- **前置条件**：`user_id` 从未出现过，users 表行数为 M。
- **当**：`GET /api/ledger?user_id=...`。
- **则**：200，`ledger=null`、`members=[]`；查库确认 users 行数仍为 M（**不得**调用 `get_or_create_user`）。

### REQ-008_MINIPROGRAM_API-004：挂载隔离与既有入口不变

`/api` 必须以独立路由挂载；`/wechat`（GET 验签、POST 消息）与 `/health` 的行为、路径、响应格式必须零变化。

#### Scenario：新老入口共存

- **前置条件**：服务已启动。
- **当**：分别请求 `/health`、`/wechat`（验签成功/失败）、`/api/ping`。
- **则**：`/health` 响应与基线一致；`/wechat` 响应与基线一致（既有 23 个微信协议用例全绿即证）；`/api/ping` 正常。

### REQ-008_MINIPROGRAM_API-005：复用与分层纪律

`ai_smartledger/api/` 内不得直接调用任何 db 写函数（`insert_many_for_ledger`/`create_ledger`/`apply_join`/`approve_join`/`admin_*`/`set_nickname`/`leave_ledger`/`switch_ledger`/`reset_invite_code`/`get_or_create_user`/`enqueue_undelivered`/`init`）；写操作只能发生在 `router.process_message`（及其内部 agent/工具）中。api 层不得复制任何业务规则（解析、分摊、结算、权限）。

#### Scenario：静态检查

- **前置条件**：实现完成。
- **当**：审视 `ai_smartledger/api/*.py`。
- **则**：仅出现参数校验、`router.process_message`、只读域函数与 JSON 组装；无写函数调用、无 SQL。

### REQ-008_MINIPROGRAM_API-006：既有行为不回归

本次改动不得改变现有业务行为：117 个既有单元测试断言零改动，e2e 16 项与 evals 8 场景保持通过。

#### Scenario：全量回归

- **前置条件**：实现完成。
- **当**：运行全量 pytest、e2e 脚本、promptfoo evals。
- **则**：pytest 117 + 新增全绿且既有测试文件无改动；e2e 16/16；evals 8/8。

### REQ-008_MINIPROGRAM_API-007：跨端身份绑定方案（设计交付，不实现）

本变更必须给出公众号与小程序身份绑定的两个方案及取舍，并说明对 `users`/`ledger_members` 的表结构影响与存量公众号用户的兼容路径。本次实现不得引入任何 schema 变更（零迁移）。

#### Scenario：评审者可据以决策

- **前置条件**：阅读本 spec 与 design.md。
- **当**：评估 009 的实施范围。
- **则**：可明确两个方案各自的流程、表结构改动（是否需要新增列/表、是否需要迁移存量数据）、存量兼容方式与推荐结论。

## CHANGED Requirements

（无。既有接口与业务行为全部保持不变，仅新增 `/api` 路由。）

## REMOVED Requirements

（无。）
