# 账本列表文案 + 微信禁用 Markdown — Plan

设计：`docs/superpowers/specs/2026-11-25-ledger-list-copy-design.md`
执行方式：内联（executing-plans）。每个任务开始前 `gate.py check`。

## 任务 1：member_count（TDD）

- 测试：`tests/test_ledger_list.py::test_db_member_count`——owner 建账本、申请人加入、owner 同意后 `member_count == 2`
- 实现：`db/ledgers.py` 的 `get_my_ledgers` SQL 加子查询
  `(SELECT COUNT(*) FROM ledger_members lm2 WHERE lm2.ledger_id = l.id) AS member_count`
- 验证：单测 + 全量 pytest

## 任务 2：列表格式化（TDD）

- 测试：`tests/test_ledger_list.py` 的 `test_single_ledger_exact` / `test_multi_and_non_current` / `test_deleted_marker` / `test_invite_hidden_by_default_shown_on_request`
- 实现：`agent.format_ledger_list(ledgers, show_invite=False)`
  - 头 `你的账本：`
  - 每行 `- #id 名称（当前 · N 人[ · 已删除·只读][ · 口令 xxx]）`
  - 尾 `要再建一个，说「建个账本 XXX」就行。`
- 验证：单测 + 全量 pytest

## 任务 3：工具接线

- `get_my_ledgers(show, show_invite=False)` 调 `format_ledger_list`
- 测试：`test_tool_output_defaults` 默认无口令、含建账本提示
- 更新旧测试 `test_my_ledgers_tool_hides_code_for_deleted`：默认隐藏、问才显示、已删始终不显示

## 任务 4：提示词规则（TDD）

- 测试：`tests/test_prompts.py` 断言 `build_agent_prompt(now)` 含“原样转述”和“不渲染 Markdown”规则
- 实现：`prompts.py`
  - 规则 5：列表原样转述、不加粗/不插空行/保留建账本提示、问口令传 `show_invite=True`
  - 规则区新增：微信不渲染 Markdown，不要用 `**`、`#` 标题、反引号
- 验证：单测 + 全量 pytest

## 任务 5：实机验证

- 重启服务，HTTP 发「我的账本」→ 精确列表 + 建账本提示
- 发「口令」→ 显示口令，提示保留
- 发「建个账本 家用」→ 回复无 Markdown 星号
- 清理测试用户，服务保持健康

## 任务 6：精修与审查

- 调用 `code-refinement`
- 全量 `gate.py verify --cmd "pytest -q" --cmd "ruff check tests/test_ledger_list.py tests/test_prompts.py prompts.py"`
- OCR + 独立子代理盲审；结果写 `.delivery-gate/review.md` 4/4
- 有修复 → 重跑全量验证

## 任务 7：提交

- `gate.py commit-check`
- `git add` 本次明确文件，提交（pre-commit gitleaks）
- 完成后走 `finishing-a-development-branch`
