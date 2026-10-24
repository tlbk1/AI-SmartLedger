# features — 当前有效规格（006-agent-layers 归档后的模块结构与行为速览）

> 权威来源：代码 + 测试（117 用例）+ 本目录。基线快照见 `spec/baseline/`（006 接管时点）；
> 变更历史见 `spec/archive/`。

## 模块结构（spec 006 起）

```
ai_smartledger/
├── core/        config.py（DEFAULT_MODEL）· llm.py（gateway() 网关配置唯一来源）·
│                prompts.py（AGENT_SYSTEM_PROMPT 主链 + SYSTEM_PROMPT 兜底）
├── agents/      react_agent.py（ReAct 主链：run_agent/_pending_joins_hint/AGENT_FAILURE/_get_model；
│                re-export AGENT_SYSTEM_PROMPT、make_tools）
│                fallback_agent.py（兜底链五函数 + QueryParams/ExtractedTxn/ExtractionResult + TOOL_SCHEMA）
├── tools/       __init__.py（make_tools 组装 + ask_clarify）
│                builtin/{transactions,ledgers,members,admin}.py（17 工具按域）+ common.py（resolve_admin_ledger）
├── db/          六域包（schema/users/ledgers/members/transactions/notices）；
│                DB_PATH/_connect 留守 __init__（monkeypatch 契约）
├── router.py    process_message 编排（补投 → 主链 → 哨兵兜底三分支）
├── wechat.py    微信协议适配（验签/XML/被动回复/客服消息/token 缓存）
├── notify.py    客服消息推送慢路径（失败→未送达队列）
└── main.py      FastAPI 入口（/wechat GET 验签、POST 消息 4.5s 窗口、/health）

依赖方向：main → router → agents → tools → {db, notify} → {wechat, db}；无反向边。
活库位置：ai_smartledger/db/ledger.db（随包迁移，见 verification.md 遗留债务 D1）。
```

## 一条消息的旅程（006 后路径）

1. `main.py` POST /wechat：验签 → XML → MsgId 幂等 → 4.5s `wait_for(shield(to_thread(router.process_message)))`；超时回空 + done-callback → `notify.push_customer`。
2. `router.process_message`：`db.drain_undelivered` 补投 → `agents.react_agent.run_agent`（`core.prompts.AGENT_SYSTEM_PROMPT` + `_pending_joins_hint`，`tools.make_tools(openid)` 17 工具，recursion_limit 12，聊天历史 `db`）。
3. 哨兵 `AGENT_FAILURE` → `agents.fallback_agent`：意图分类 → 记录拒绝重试 / 查询（`extract_query_params` + `db.query_by_ledger`/`sum_by_ledger` 同口径聚合 + `summarize_query_result`）/ 闲聊 `chat_reply`。
4. 加入/审批：工具闭包 → 守护线程 → `notify.push_customer`（`wechat.send_customer_message` 失败 → `db.enqueue_undelivered`）。

## 质量门禁

- `.venv/Scripts/python.exe -m pytest -q`（PYTHONPATH 清空，117 用例）
- `python scripts/e2e_shared_ledger.py`（16 项用户旅程）
- `evals/` promptfoo（8 场景，见其 README）
- pyright（off 模式 import 检查，配置 pyrightconfig.json）
