# Ponytail 评审修复 — Design

日期：2026-11-25
范围：`/opt/ledger` 当前工作区未提交改动（分支 `feat/005-code-structure`）
非目标：不做记账快路径、不换模型、不改记账/查账/账本权限行为；欢迎语按 spec 003 FR-001 恢复五要素。

## 问题

1. 工作区 87 个文件是 CRLF，HEAD 是 LF；真实内容改动只有 14 个文件。
2. `RotatingFileHandler` 轮转后重新打开文件，不再 chmod，私人日志变宽权限。
3. `AGENT_SYSTEM_PROMPT` 改为三占位符后没有测试；漏传 `help_text=` 会让所有消息走降级。
4. `处理消息` 原样写用户内容，消息里的换行可以伪造日志行。
5. 主 Agent 失败时，兜底闲聊不认「帮助」，新欢迎语的承诺不成立。
6. `prompts.py` 文档只写了 `{now}`，与实际三个占位符不符。

## 方案

1. 新增 `.gitattributes`：`* text=auto eol=lf`、`*.bat text eol=crlf`、`*.ps1 text eol=crlf`；把工作区文本文件转回 LF（`.bat`/`.ps1` 保持 CRLF）。结果是 14 个真改动 + `.gitattributes`。
2. `main.py` 增加 `_PrivateRotatingFileHandler`，在 `_open()` 里 chmod 640；删除现在的一次性 chmod。
3. `prompts.py` 增加纯函数 `build_agent_prompt(now)`；`agent.py` 改用它；测试断言包含 `GREETING_TEXT` 和 `HELP_TEXT`。
4. `main.py` 日志预览改为 `content[:30]!r`，换行被转义。
5. `llm.chat_reply` 开头：内容含「帮助 / 怎么用 / 功能」时直接返回 `HELP_TEXT`，不调 LLM。
6. `prompts.py` 顶部文档列出三个占位符。

## 测试与验证

- 新增 3 个测试：轮转后两个日志文件都是 640；`build_agent_prompt` 内容正确；`chat_reply("帮助")` 不调 LLM 直接返回 `HELP_TEXT`。
- 全量 `pytest -q`；`ruff` 可用则跑，`pyright` 未安装会在验证记录里注明。
- 重启服务后通过 HTTP 发「帮助」和一条带换行的消息，检查日志转义与文件权限。
- 归一化后确认 `git diff --stat` 与 `git diff --ignore-cr-at-eol --stat` 一致。

## 工作区

用户确认：就在 `/opt/ledger` 当前 clone 的 `feat/005` 上修，不新建 worktree。

## 审查后修订（2026-11-25）

- **首次关注欢迎语**：按用户决定恢复 spec 003 FR-001 五要素；聊天问候保持短版。`_build_welcome` 新用户分支返回五要素，老用户仍返回 `WELCOME_BACK_TEXT`。
- **兜底「帮助」**：关键词改为精确匹配（`content.strip()`），避免「这个月怎么用了这么多」误触发；新增负向测试。
- **日志转义**：同一规则扩展到 `快路径回复`、`agent.py`、`graph.py` 的用户/模型文本。
- **提示词导入**：`AGENT_SYSTEM_PROMPT` 从 `agent.py` 移除后，两个测试改从 `prompts` 导入。
- **计划笔误**：Task 4 的 `content[:30]!r` 是无效语法；实现为 `%r` + `content[:30]`。

