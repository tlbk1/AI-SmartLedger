# 技术设计：006-agent-layers

## 当前状态

根目录平铺 7 模块（main 235 / graph 129 / agent 495 / llm 461 / prompts 66 / config 6 / wechat 210）+ `db/` 域包（六域约 1450 行）。import 边：main→{db,graph,wechat}；graph→{db,agent}；agent→{db,prompts}；延迟边 agent⤳main（:189/:302）、graph⤳llm。详细见 `docs/specs/baseline/system-map.md`。

## 方案与数据流

目标树（← 为来源映射，全部 git mv 保历史）：

```
ai_smartledger/
├── core/
│   ├── config.py            # ← config.py（DEFAULT_MODEL 原样）
│   ├── llm.py               # 新增：gateway() 网关配置唯一来源
│   └── prompts.py           # ← prompts.py + llm.SYSTEM_PROMPT（兜底提示词并入）
├── agents/
│   ├── react_agent.py       # ← agent.py 主体（run_agent/_pending_joins_hint/AGENT_FAILURE/_get_model）
│   └── fallback_agent.py    # ← llm.py 兜底链 + 数据类 + TOOL_SCHEMA + _get_client
├── tools/
│   ├── __init__.py          # ← agent.make_tools 组装入口 + ask_clarify
│   └── builtin/
│       ├── transactions.py  # query_transactions / record_transactions
│       ├── ledgers.py       # create_ledger / get_my_ledgers / switch_ledger
│       ├── members.py       # join_ledger / my_join_status / list_pending / approve_join /
│       │                    #   leave_ledger / list_members / set_nickname
│       └── admin.py         # admin_remove_member / admin_rename_ledger / admin_delete_ledger /
│                            #   reset_invite_code
├── db/                      # ← db/ 整体（__init__ 契约零改动）
├── router.py                # ← graph.py（process_message / _dispatch_fallback / _classify_and_route）
├── wechat.py                # ← wechat.py 原样
├── notify.py                # ← main._do_push_customer（推送慢路径）
└── main.py                  # ← main.py（FastAPI 入口）
```

一条消息的新旅程：`main.py`（4.5s 窗口）→ `router.process_message`（补投未送达）→ `agents.react_agent.run_agent`（17 工具经 `tools.make_tools` 组装，读写 `db`）→ 失败哨兵 → `agents.fallback_agent` 兜底链（查询走 `db.query_by_ledger`/`sum_by_ledger`）。加入/审批的对方通知：工具闭包 → `notify.push_customer`（`wechat` 失败 → `db.enqueue_undelivered`）；main 超时 done-callback 同样调 notify。新增依赖边全部单向向下：agents→tools→{db,notify}→{wechat,db}；main→router→agents。

## 接口与兼容性

- `gateway()`：返回小数据类 `GatewayConfig(base_url, api_key, model)`，字段来源 `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`（缺省回退与基线逐字一致）。仅被 `_get_model`（react_agent）与 `_get_client`（fallback_agent）调用。
- re-export 契约（保 patch 目标与断言）：`agents/react_agent.py` 顶层 `from ..core.prompts import AGENT_SYSTEM_PROMPT`、`from ..tools import make_tools`、`AGENT_FAILURE` 原地定义；调用点用裸名（保证 setattr 重绑生效）。
- patch 目标留守清单：`_get_model` 留 react_agent、`_get_client` 留 fallback_agent、`WECHAT_*` 读取留 wechat.py、`DB_PATH`/`_connect` 留 db/__init__。
- 测试导入别名方案：`from ai_smartledger.agents import react_agent as agent`、`from ai_smartledger.agents import fallback_agent as llm`、`from ai_smartledger import router as graph`、`from ai_smartledger import main`（tests/evals/e2e 各自按需）。断言零改动。

## 配置、常量与依赖注入

- 环境差异（网关地址/密钥/模型名）仍走 `LLM_*` 环境变量；`gateway()` 只做解析集中，不引入新配置项。
- 既有旧网关默认值 `https://ccc.szprize.cn/v1` 按原样搬运（属已记录债务，修正为独立变更），本变更不新增、不复制该字面量（两处合一处）。
- 无新增业务常量；17 工具的域归属是实现细节，不引入注册表/配置文件。

## 备选方案与取舍

1. **包一层 vs 根目录平铺分层**：包一层（维护者拍板）——与参考树同款外观，import 前缀一次性改净。
2. **抄全套框架机器 vs 最小分层**：取后者——单 agent 无需基类/注册器/异步执行器，机器文件是 YAGNI（ponytail 取舍，proposal 非目标已列）。
3. **_get_model/_get_client 集中进 core/llm vs 留守**：留守——集中会移动 patch 目标，破坏「断言零改动」验收线；gateway() 只抽共享解析，风险最小。
4. **测试重组 vs 只改 import**：只改 import——spec005 已裁定纯搬文件零收益；重组仍留债务候选。
5. **notify 并入 wechat.py vs 独立模块**：独立——推送慢路径跨 wechat（协议）与 db（队列），并入任一都会造成反向职责。

## 失败模式、安全与回滚

- **拆分漏工具/错域**：拆分前后收集工具名集合比对（17 个），e2e 用户旅程覆盖主要工具路径。
- **patch 失效 → 假绿**：re-export 清单逐条核验 + 测试 diff 审查（REQ-009 场景）。
- **循环依赖残余**：grep 门禁（REQ-007 场景）+ import 冒烟。
- **服务起不来**：uvicorn 冒烟 /health（REQ-008 场景）。
- **安全**：不读取/提交 `.env` 与密钥；不触碰微信协议语义。
- **回滚**：单分支 `refactor/006-agent-layers` 上 git revert 重建提交即可整体回退；db 零改动、无数据迁移，无数据风险。

## 数据迁移

`none`。如需要数据库结构或不可逆数据迁移，停止本 Skill 流程。
