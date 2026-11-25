# 007-defect-fixes — Design

日期：2026-11-25
工作区：`~/worktrees/wt-007-defect-fixes`（基于新 main，已合入 007 规格）
来源规格：`spec/changes/007-defect-fixes/{proposal,spec,design}.md`

## 目标

实现两个真实缺陷，F-b 维持驳回：

- **F-a**：`record_transactions` 对 LLM 入参零校验。空列表会回「✓ 已记 0 笔」但不写库；缺 `amount`/金额不可转数字/元素非 dict 会抛异常，整轮走兜底。修法：工具层逐项校验，畸形入参返回中文提示且**零写入**；合法入参行为逐字不变。
- **F-c**：`run_agent` 从后往前找第一条非 tool 消息，可能取到用户原话或上一轮旧回复。修法：只接受「最后一条消息是 AI 且 content 非空」；否则返回 `AGENT_FAILURE`，不写聊天历史。
- **F-b**：admin 工具空账本判空是误报，不实现。

## 非目标

- 不引入金额业务校验（负数/零/上限）。
- 不改 db 层 `insert_many_for_ledger([]) -> True` 的内部契约。
- 不改提示词、工具 schema、其他 16 个工具。

## 测试

- 新增 `tests/test_defect_fixes.py`：F-a 四类畸形入参 + 合法入参 + 查库零写入；F-c `_extract_reply` 四种末条类型 + `run_agent` 失败不写历史。
- `tests/test_ledger_list.py` 现有 `_extract_reply` 用例同步新语义。
- 现有 138 个测试断言不改（除上述提取语义的本地用例）。

## 验证

- 全量 `pytest -q`；`ruff` 可用则跑。
- 能跑则跑 `scripts/e2e_shared_ledger.py` 与 evals；环境不支持时在验证记录里注明。
- 不涉及 DB schema/数据迁移。
