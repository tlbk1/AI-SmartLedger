# 系统结构基线

## 清单

- `agent.py`
- `config.py`
- `db/__init__.py`
- `db/ledgers.py`
- `db/members.py`
- `db/notices.py`
- `db/schema.py`
- `db/transactions.py`
- `db/users.py`
- `docs/viz/gen_data.py`
- `docs/viz/graph_data.js`
- `docs/viz/vendor/cose-base.min.js`
- `docs/viz/vendor/cytoscape-fcose.min.js`
- `docs/viz/vendor/cytoscape.min.js`
- `evals/provider.py`
- `graph.py`
- `llm.py`
- `main.py`
- `prompts.py`
- `scripts/e2e_shared_ledger.py`
- `tests/__init__.py`
- `tests/test_identity_selection.py`
- `tests/test_query_accuracy.py`
- `tests/test_shared_ledger.py`
- `tests/test_wechat_mock.py`
- `wechat.py`

## 入口

- `main.py`（FastAPI app；`start_dev.bat` 以 `uvicorn main:app --host 0.0.0.0 --port 8000` 启动 + cpolar 隧道）
- 辅助脚本：`scripts/e2e_shared_ledger.py`（e2e 用户旅程）、`evals/provider.py`（promptfoo 驱动 `graph.process_message`）、`docs/viz/gen_data.py`（代码依赖图数据生成）

## 模块关系与数据流

根目录平铺 7 个模块 + `db/` 域包（基线时点：2026-10-25，分支 feat/005-code-structure；行数 wc -l 实测）：

- `main.py`（235）：FastAPI。GET/POST `/wechat`——签名校验、XML 解析、MsgId 幂等（`db.mark_seen`）；4.5s `asyncio.wait_for(shield(...))` 包住线程内执行的 `graph.process_message`，超时后 done-callback 走 `_do_push_customer` 慢路径（同步 `wechat.send_customer_message`，失败落 `db.enqueue_undelivered`）。关注事件欢迎语 `_build_welcome`；`/health`。
- `graph.py`（129）：`process_message(openid, content) -> str` 编排入口：先 `db.drain_undelivered` 补投，再 `agent.run_agent`；结果等于哨兵 `AGENT_FAILURE` 时走 `_dispatch_fallback → _classify_and_route`（记录→拒绝重试；查询→`llm.extract_query_params` + `db.query_by_ledger`/`sum_by_ledger` + `llm.summarize_query_result`；闲聊→`llm.chat_reply`）。已无 StateGraph，名字是历史遗留；`import llm` 为调用时延迟导入。
- `agent.py`（495）：ReAct 主链。`_get_model()` 懒建 `langchain_openai.ChatOpenAI`（默认 base_url 指向已下线的 ccc.szprize.cn/v1，见 risks）；`make_tools(openid)` 工厂产 17 个 `@tool` 闭包（查/记账、建/切/列账本、邀请/审批/待审/退出、成员列表、昵称、管理员三件套、邀请码重置、澄清）；`run_agent()`：`db.get_or_create_user`、`AGENT_SYSTEM_PROMPT.format(now=...)` + `_pending_joins_hint`、`create_react_agent`（recursion_limit 12）、聊天历史 `db.get/append_chat_history`；任何异常返回 `AGENT_FAILURE` 哨兵。第 189/302 行两处工具闭包内 `from main import _do_push_customer`（延迟导入，全库唯一循环依赖边）。
- `llm.py`（461）：兜底层（仅主链失败时使用）。openai SDK 客户端（同样硬编码旧网关默认值）、`QueryParams`/`ExtractedTxn`/`ExtractionResult` 数据类、`TOOL_SCHEMA`、自有 `SYSTEM_PROMPT`，以及 `classify_intent`/`extract_transactions`/`extract_query_params`/`summarize_query_result`/`chat_reply`。
- `prompts.py`（66）：`AGENT_SYSTEM_PROMPT`（005 已独立成文件）；兜底层的 SYSTEM_PROMPT 仍在 llm.py 内。
- `wechat.py`（210）：协议适配器——SHA1 验签、`parse_message → WeChatMessage`、`build_text_reply`、`send_customer_message`、access_token 内存缓存；`WECHAT_*` 各模块 import 时自读环境变量。
- `config.py`（6）：`DEFAULT_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")`。
- `db/`（约 1450，6 域 + `__init__`）：schema（DDL + 幂等列回填）、users、ledgers、members、transactions（含 SQL 聚合）、notices（幂等/聊天历史/澄清态/未送达队列）。域内依赖：ledgers→users，members→users+ledgers，transactions→ledgers；统一 `from . import SHANGHAI, _connect`。`__init__` 持 `DB_PATH`/`_connect` 并 re-export 全部公开名（monkeypatch 契约，见其 docstring）。

导入边（import 时）：main→{db, graph, wechat}；graph→{db, agent}；agent→{db, prompts}；llm→{openai, dotenv}；wechat→{httpx, defusedxml}。循环/延迟边：agent⤳main（延迟导入 ×2）、graph⤳llm（延迟导入）。
