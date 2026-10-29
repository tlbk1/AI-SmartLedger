# 行为规格：006-agent-layers

## ADDED Requirements

### REQ-006_AGENT_LAYERS-001：包结构总览

源码必须收入 `ai_smartledger/` 包：`core/`（config.py、llm.py、prompts.py）、`agents/`（react_agent.py、fallback_agent.py）、`tools/`（__init__.py、builtin/）、`db/`（六域包原样）与包内顶层 router.py、wechat.py、notify.py、main.py；仓库根目录不得再有平铺的 agent.py、llm.py、graph.py、prompts.py、config.py、wechat.py、main.py。

#### Scenario：目标树成立

- **前置条件**：检出 refactor/006-agent-layers，工作区干净。
- **当**：列出 `ai_smartledger/` 与仓库根目录的文件。
- **则**：上述包结构存在；根目录除 tests/、evals/、scripts/、docs/、spec/ 等非源码目录外无散置 .py 业务模块。

### REQ-006_AGENT_LAYERS-002：core 层

`core/config.py` 只承载 `DEFAULT_MODEL`（语义与基线一致）；`core/prompts.py` 承载 `AGENT_SYSTEM_PROMPT` 与兜底层 `SYSTEM_PROMPT` 两个常量；`core/llm.py` 提供 `gateway()` 作为网关配置（base_url/api_key/model 解析）的唯一来源，供 react_agent 与 fallback_agent 内部调用。

#### Scenario：网关配置单一来源

- **前置条件**：ai_smartledger 包可用。
- **当**：检索 base_url 默认值与 `LLM_MODEL`/`LLM_BASE_URL` 的解析代码。
- **则**：解析逻辑仅存在于 `core/llm.py`，react_agent 与 fallback_agent 通过 `gateway()` 获取，不再各自重复 `os.environ.get("LLM_BASE_URL", ...)`。

### REQ-006_AGENT_LAYERS-003：agents 层

`agents/react_agent.py` 承载 `run_agent`、`_pending_joins_hint`、`AGENT_FAILURE` 哨兵、`_get_model`，并必须在模块顶层 re-export `AGENT_SYSTEM_PROMPT` 与 `make_tools`；`agents/fallback_agent.py` 承载兜底链五函数（classify_intent / extract_transactions / extract_query_params / summarize_query_result / chat_reply）、`QueryParams`/`ExtractedTxn`/`ExtractionResult` 数据类、`TOOL_SCHEMA` 与 `_get_client`。`_get_model`/`_get_client` 不得迁出各自模块。

#### Scenario：patch 目标在新位置有效

- **前置条件**：测试套件仅将 `import agent`/`import llm` 等改为新路径别名导入。
- **当**：`monkeypatch.setattr` 作用于 react_agent.AGENT_SYSTEM_PROMPT、react_agent.make_tools、fallback_agent._get_client、fallback_agent.classify_intent 等历史 patch 目标。
- **则**：替换生效，行为与基线测试一致。

### REQ-006_AGENT_LAYERS-004：tools 层

`tools/__init__.py` 提供 `make_tools(openid)` 组装入口并承载 `ask_clarify`；`tools/builtin/` 按域拆分其余 16 个工具（transactions：query/record；ledgers：create/get_my_ledgers/switch；members：join/my_join_status/list_pending/approve/leave/list_members/set_nickname；admin：admin_remove_member/admin_rename_ledger/admin_delete_ledger/reset_invite_code）。实现时按闭包实读允许 ±1 归组调整，但工具总数与名称集合不得变化。

#### Scenario：工具集合守恒

- **前置条件**：任一测试用 openid。
- **当**：分别收集迁移前 `agent.make_tools(openid)` 与迁移后 `tools.make_tools(openid)` 的工具名集合。
- **则**：两集合相等，元素个数 17。

### REQ-006_AGENT_LAYERS-005：db 原样迁入

`db/` 六域包整体移入 `ai_smartledger/db/`，内部实现零改动；`DB_PATH` 与 `_connect` 必须留在 `db/__init__.py`（monkeypatch 契约，见其 docstring）。

#### Scenario：隔离库契约不破

- **前置条件**：测试 `iso` fixture 以 `monkeypatch.setattr(db, "DB_PATH", tmp)` 替换库路径。
- **当**：任一 db 域函数执行 `_connect()`。
- **则**：连接落在替换后的测试库上，行为与基线一致。

### REQ-006_AGENT_LAYERS-006：router

`router.py`（← graph.py）承载 `process_message`、`_dispatch_fallback`、`_classify_and_route`，编排顺序与兜底语义与基线逐字一致；确认无引用后删除 dead 的 `GraphState` TypedDict。

#### Scenario：编排行为不变

- **前置条件**：消息处理链路可用。
- **当**：同一消息输入分别走基线与新结构的 `process_message`。
- **则**：主链成功路径、哨兵→兜底链路径（记录拒绝/查询聚合/闲聊）的输出一致。

### REQ-006_AGENT_LAYERS-007：notify 解循环依赖

新建 `notify.py` 承载原 `main._do_push_customer` 的推送慢路径（`wechat.send_customer_message` 失败→`db.enqueue_undelivered`）；工具闭包内两处 `from main import _do_push_customer` 必须改为对 notify 的顶层正向导入；`main.py` 的超时 done-callback 改调 notify。

#### Scenario：反向依赖清零

- **前置条件**：重构完成。
- **当**：在 `ai_smartledger/` 内 grep `from main import` 与 `import graph`。
- **则**：零命中；agent 相关模块不再依赖应用入口。

### REQ-006_AGENT_LAYERS-008：入口与脚本

`start_dev.bat` 改为 `uvicorn ai_smartledger.main:app`；`main.py` 的 `uvicorn.run(...)` 字符串同步；tests×4、evals/provider.py、scripts/e2e_shared_ledger.py 的 import 行更新为新路径（可别名保原名）。

#### Scenario：服务可启动

- **前置条件**：环境变量就绪（.env 存在）。
- **当**：以 `uvicorn ai_smartledger.main:app` 启动并请求 `GET /health`。
- **则**：HTTP 200。

### REQ-006_AGENT_LAYERS-009：契约保持总则

全部测试文件断言零改动（仅 import 区域调整）；wechat 的 `WECHAT_*` 环境读取保持在 wechat.py 模块级；本变更不新增任何 hardcode（既有旧网关默认值按原样搬运，修正归独立变更）。

#### Scenario：测试 diff 审查

- **前置条件**：重构完成。
- **当**：审查 `git diff` 中 tests/ 四个文件的变更。
- **则**：仅 import 行与必要的模块名替换，无断言、无 fixture、无用例结构变化。

## CHANGED Requirements

（无——本变更不修改任何既有需求行为；所有 CHANGED 均为结构位移与 import 路径，见 ADDED 条目。）

## REMOVED Requirements

### REQ-006_AGENT_LAYERS-010：根目录平铺模块退役

仓库根目录的 agent.py、llm.py、graph.py、prompts.py、config.py 平铺模块与 `main._do_push_customer` 符号、dead `GraphState` TypedDict、agent⤳main 延迟导入边全部移除（经 git mv 保历史）。

#### Scenario：旧符号不可再导入

- **前置条件**：重构完成，工作区干净。
- **当**：以仓库根为 cwd 尝试 `import agent` / `import llm` / `import graph` / `from main import _do_push_customer`。
- **则**：均 ImportError（或 ModuleNotFoundError），新路径导入正常。
