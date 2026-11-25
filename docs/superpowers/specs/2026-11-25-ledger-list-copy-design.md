# 账本列表文案 + 微信禁用 Markdown — Design

日期：2026-11-25
范围：`/opt/ledger` 分支 `fix/006-ledger-list-copy`
非目标：不改记账/查账/账本权限行为，不改数据库结构。

## 已确认的产品决定

- 列表格式：`你的账本：` + 一行一个 `- #编号 名称（当前 · N 人）`
- 单人账本只显示人数，不显示成员姓名；不显示角色、「默认」标记
- 已删除账本加 `已删除·只读`
- 口令默认不显示；用户明确问「口令/邀请码」时，owner 的账本才显示 `口令 xxx`
- 结尾固定保留一句：`要再建一个，说「建个账本 XXX」就行。`
- Agent 必须原样转述该列表：不加粗、不改结构、不插空行、不补成员名单
- 全局规则：微信不渲染 Markdown，回复里不要用 `**`、`#` 标题、反引号

## 实现

- `db.get_my_ledgers` 加 `member_count`（同一条 SQL 子查询，不做 N+1）
- `agent.format_ledger_list(ledgers, show_invite=False)` 生成最终文本
- `get_my_ledgers(show, show_invite=False)` 调用格式化函数
- `prompts.py` 增加“原样转述 + show_invite + 禁用 Markdown”规则

## 测试与验证

- 新测试：单人/多人/已删除/show_invite 四种格式、`member_count`、工具默认输出
- 更新旧测试：口令默认隐藏，问口令才显示，已删账本始终不显示
- 全量 `pytest -q`；`ruff` 新文件
- 实机 HTTP：「我的账本」「口令」「建个账本」检查列表、口令和 Markdown

## 工作区

沿用上次用户选择：在当前 clone 新建分支 `fix/006-ledger-list-copy`，不新建 worktree。
