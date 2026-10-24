# 风险基线

## 大文件或高复杂度候选

- `agent.py`（495 行，其中约 330 行为 17 个工具闭包）——006 重构对象
- `llm.py`（461 行，兜底链 + 数据类 + TOOL_SCHEMA + 提示词混装）
- `db/members.py`（419 行，权限/审批/管理员逻辑）

## 安全、兼容与未知项

- 无 CI：测试不强制于 push，靠本地人肉执行。
- monkeypatch 契约脆弱：符号搬家会让 patch 静默失效 → 假绿（db 部分已固化在 `db/__init__` docstring；wechat/llm/agent 的 patch 目标未见同等保护）。
- 依赖风险：`requirements.txt` 缺 `langchain-openai`/`langchain-core`，而 `agent.py` 直接 import langchain_openai——新环境按清单装不齐，无法从零复现。
- 旧网关默认值：`agent.py:43`、`llm.py:99` 兜底 base_url 指向已下线的 ccc.szprize.cn/v1；`.env` 缺 `LLM_BASE_URL` 时静默指向死端点。
- 循环依赖：agent⤳main（延迟导入 ×2，agent.py:189/302）。
- 平台：仅 Windows 本地开发（start_dev.bat + cpolar）；Python 版本未钉（实测 3.11.3）。
- 数据安全：活库 `db/ledger.db` 在工作目录（另有根目录遗留 `ledger.db`）；不读取/记录/提交 `.env`。
- 未验证项：pyright/ruff 从未运行，类型健康度未知。
