# 变更提案：008-miniprogram-api

## 问题与背景

当前唯一入口是公众号：`/wechat` 收 XML → `router.process_message` 编排 → ReAct 主链/兜底链 → XML 回复。小程序需要 JSON 接口，且必须与公众号共用同一套账本、成员、交易数据与业务逻辑。

本变更只做**服务端接入层**：新增 `/api/*` 三个接口，全部复用现有编排与域函数，不重写任何业务规则；小程序前端（miniprogram/）不在本次范围。

**身份问题（本 spec 必须回答的设计问题）**：公众号与小程序 appid 不同，同一用户的 openid 不同，现会被识别成两个 users 行。方案与取舍见 design.md「身份与跨端绑定方案」，本次实现不含绑定（三接口以调用方传入的 `user_id` 为身份）。

## 目标

1. 新增 `ai_smartledger/api/` 包，挂载 `/api` 前缀，在 `main.py` 注册；`/wechat`、`/health` 零改动。
2. 三个接口：
   - `GET /api/ping`：连通性与服务版本。
   - `POST /api/record {user_id, text}`：经 `router.process_message` 完成「解析 → 入库 → 回复」，返回结构化结果（本次新增账目的金额/分类/收支/时间 + 当前账本）。
   - `GET /api/ledger?user_id=`：当前账本、当前用户角色、成员列表。
3. 分层纪律：`api/` 只做参数校验、调用编排层与**只读**域函数、组装 JSON；不得直接调用 db 写函数，不得复制任何业务规则。
4. 给出跨端身份绑定的两个方案与取舍、对 users/ledger_members 的表结构影响、存量公众号用户兼容路径。

## 非目标

- 不做小程序前端、不做登录/鉴权体系、不做手机号或绑定码的落地实现（方案见 design，落地留 009）。
- 不改 ReAct 主链、17 个工具、db 层既有函数、提示词、`/wechat` 协议。
- 不引入新依赖（只用已有 FastAPI/httpx）。
- 不新增/修改任何数据库表（本次零 schema 变更）。
- 不做限流、签名、CORS 策略（部署层处理）。

## 影响范围

- 新增：`ai_smartledger/api/__init__.py`、`ai_smartledger/api/routes.py`；`ai_smartledger/__init__.py` 增加 `__version__`；`main.py` 增加 include_router 一行；`tests/test_miniprogram_api.py`。
- 不改：router.py、agents/、tools/、db/、wechat.py、notify.py、prompts.py。

## 风险

1. **无鉴权**：`user_id` 由调用方提供，暴露公网时可被冒充操作他人账本。缓解：本次定位内网/可信调用；design 中给出部署建议（网关白名单/后续 009 登录）。
2. **结构化结果识别方式**：`/api/record` 用「调用前后账本事务 id 差集」识别本次新增（零 db 改动）。局限：需要宽窗口快照；极端并发下可能混入他人同期写入。design 记录并给后续优化方案。
3. **`/api/record` 会经编排层触发写操作（含自动建用户）**，属既有行为复用；`/api/ledger` 对未知用户只读返回空，不建用户（守住「api 不直接写」）。
4. 小程序 openid 与公众号 openid 不同 → 同一人两套数据（本 spec 只给方案，不落地）。

## 验收标准

1. 117 个既有单元测试、e2e 16 项、evals 8 场景全部保持通过。
2. 同一句文本（如「午饭35」）分别经 `/wechat` 与 `/api/record` 处理（同一 user_id、同一隔离库），落库结果一致：账目行数与字段一致、金额/分类/收支一致。
3. 静态检查：`ai_smartledger/api/` 内不出现 db 写函数调用（insert/create/update/delete/get_or_create_user/set_nickname 等），只允许读函数与 `router.process_message`。
4. `/wechat` 与 `/health` 行为零变化（既有测试覆盖即证）。
5. `GET /api/ping` 返回 200 且含版本；`/api/ledger` 对未知用户不产生任何写入（查库确认 users 行数不变）。

## 待决问题（请求批准规格时一并确认）

1. **绑定方案选型**：design 给出选项 1 手机号 / 选项 2 绑定码的完整取舍，建议 009 落地选项 2（理由：真正解决跨端 + 存量零迁移；手机号方案在公众号侧拿不到号码，只能识别小程序内部同一人）。本次不实现，仅回答。
2. **`/api/record` 增量识别**：本次用零改动的「id 差集」方案；若你允许在 db 层新增一个只读函数（不改既有函数），可去掉快照成本与并发窗口，请在批准时指明。
3. **版本号来源**：新增 `ai_smartledger.__version__ = "0.8.0"` 常量，`/api/ping` 返回之。

## 流程备注

- 007（缺陷修复）规格已起草未批准，已挂起在 `fix/007-defect-fixes` 分支（状态随分支隔离）；本变更从 `refactor/006-agent-layers` 切出，不依赖 007。
- 006 处于「已归档未推送」，`state.json` 的 phase 由 `ready-to-push` 调回 `ready` 以开本变更（受控例外，006 归档记录保留，可回退）。
- 本次不推送分支、不合并（用户指令）。
