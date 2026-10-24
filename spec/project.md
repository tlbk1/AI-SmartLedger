# 项目说明

## 目标与用户

微信公众号（测试号）AI 记账助手：用户在微信里用自然语言记账、查账、管理账本。支持单人记账与多人共账本（邀请码加入、昵称、管理员操作）。终端用户是公众号关注者；维护者是仓库主人本人（逐行评审驱动开发）。

## 范围与非目标

- 范围：WeChat MP 被动回复 + 客服消息通道；ReAct agent（17 个工具）+ 兜底意图链（记录/查询/闲聊）；SQLite(WAL) 存储；LLM 网关接入。
- 非目标：OCR、Web 前端、多币种、正式部署运维、数据库迁移框架。

## 技术栈与入口

Python 3.11 + FastAPI/uvicorn + langgraph `create_react_agent` + langchain_openai + openai SDK + SQLite(WAL) + httpx + defusedxml。入口 `main.py:app`（`start_dev.bat`：`uvicorn main:app --host 0.0.0.0 --port 8000` + cpolar 内网穿透）。

## 运行与质量命令

- 测试：`.venv/Scripts/python.exe -m pytest -q`（约定 PYTHONPATH 清空；117 例，基线实测 12s）
- e2e：`python scripts/e2e_shared_ledger.py`（16 项检查，临时库）
- evals：见 `evals/README`（promptfoo 端到端回复评测，独立评测库，8 场景）
- 类型检查：pyright 未安装/未配置（见 debt）
- Lint：ruff 未配置

## 约束与术语

- 术语：账本 ledger、成员 member、邀请码、账本软删、被动回复（4.5s 窗口）、客服消息、兜底链、`AGENT_FAILURE` 哨兵。
- monkeypatch 契约（搬动符号会静默假绿，详见 risks）：`db.DB_PATH`/`_connect` 必须留在 `db/__init__`；`wechat.WECHAT_TOKEN` 模块级；`llm.classify_intent`/`extract_query_params`/`summarize_query_result`/`_get_client`；`agent.make_tools`/`_pending_joins_hint`/`AGENT_SYSTEM_PROMPT`（测试断言 prompt 文案）。
- 网关：统一 ccc.hwprize.com（hw）；旧 ccc.szprize.cn 已下线（但仍是代码兜底默认值）；evals 评审模型 deepseek-flash。
- 测试写入类断言必须查库状态而非只看回复文案；改动前备份 `db/ledger.db`。
