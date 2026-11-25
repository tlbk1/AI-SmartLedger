# 变更提案：007-defect-fixes

## 问题与背景

spec 006（分层重构）的验证阶段，ocr 外部评审与独立复审共同发现三处缺陷候选。用户指令「那三个缺陷修一下」。经逐条核验（含 db 层实现与可达性证据）：

- **F-a（记账入参未校验）— 真实缺陷，本次修**。`tools/builtin/transactions.py::record_transactions` 对 LLM 传来的 `transactions` 不做任何校验：
  - 空列表 → `db.insert_many_for_ledger` 直接 `return True`（`db/transactions.py` 首个分支）→ 工具回「✓ 已记 0 笔」。**用户以为记账成功，实际什么都没写入**——与 004 修过的「静默丢数据」同类。
  - 缺 `amount` 键 → `KeyError`；`amount` 为 `"12元"`/`None`/空串 → `float()` 抛 `ValueError`/`TypeError`；元素非 dict → `AttributeError`。异常逃出工具，ReAct 循环拿不到可纠正的提示，只能整轮失败走兜底链（用户收到「没记上，请重发一次」，但模型本可自行修正）。
- **F-c（回复提取可回退到非 AI 消息）— 真实缺陷，本次修**。`agents/react_agent.py::run_agent` 取回复时从后往前找第一条「非 tool 且有 content」的消息；若最后一条 AI 消息 content 为空（只带 tool_calls），会继续前翻取到 human（用户自己的话）或更早的 AI 消息（上一轮回复）。用户可能收到自己的原话或被重复的旧回复。
- **F-b（admin 工具空账本透传 None）— 经核验为误报，不修**。证据：db 层四个 admin 函数（`admin_remove_member`/`admin_rename_ledger`/`reset_invite_code`/`admin_delete_ledger`，`db/members.py`）签名均为 `ledger_id: Optional[int] = None`，每个都内置「None → 回退当前账本；仍为 None → `return False, "你没有加入任何账本"`」。工具层透传 None 得到的是清晰中文提示，不是异常；且该分支实际不可达——`run_agent` 每个请求先 `db.get_or_create_user(openid)`，用户必然有默认账本。工具层再加一层判空会造成双份校验与文案分叉，收益为负。

## 目标

1. `record_transactions` 对四类畸形入参（空列表、非 dict 元素、缺 amount、amount 不可转数字）返回明确中文提示且**不写库**；合法入参行为与基线逐字一致。
2. `run_agent` 只接受「最后一条消息是 AI 且有非空 content」作为回复；否则返回 `AGENT_FAILURE` 哨兵交给兜底链，不再回退取历史消息。正常路径行为不变。
3. 新增测试覆盖上述两处（RED-GREEN）；现有 117 用例断言零改动，e2e 16/16、evals 8/8 保持。

## 非目标

- 不修 F-b（见上，误报；理由写入规格）。
- 不引入金额业务校验（负数/零/上限）——超出缺陷范围，另议。
- 不改 `insert_many_for_ledger` 的空列表语义（db 层 `return True` 是「无事可做」的内部契约，调用方负责拒绝空批次；改 db 层会波及所有调用方）。
- 不改 `content` 可能为 list（多模态）时的类型处理——基线既有行为，本次不动。
- 不改兜底链、提示词、工具 schema 的对外描述。

## 影响范围

- 代码：`ai_smartledger/tools/builtin/transactions.py`（record_transactions 入参校验）、`ai_smartledger/agents/react_agent.py`（回复提取条件）。
- 测试：新增 `tests/test_defect_fixes.py`（不改现有四个文件）。
- 文档：本变更规格 + features/ 相应更新。

## 风险

1. 入参校验改变模型可见的反馈 → 可能影响回复措辞：evals 8 场景复跑验证；新提示文案明确「请向用户确认金额」，与 `AGENT_SYSTEM_PROMPT` 的澄清规则一致。
2. 回复提取收紧后，原本「侥幸取到上一条 AI」的异常场景改为走兜底链 → 用户收到兜底文案而非错误回复；用专门的失败测试锁定。
3. 007 建立在**尚未合并**的 006 分支之上（`fix/007-defect-fixes` 从 `refactor/006-agent-layers` 切出），两个分支需按序合并。

## 验收标准

1. 新增测试先行 RED、实现后 GREEN：空列表/非 dict/缺 amount/坏 amount 四类均返回提示且**查库确认零写入**（写入类断言查库，不看文案）。
2. 回复提取：最后为 AI 有内容 → 返回该内容并写历史；最后为 AI 空内容 / tool / human → 返回 `AGENT_FAILURE` 且不写历史。
3. 全量 `pytest -q`：117 + 新增全绿；pyright 0 错误。
4. e2e 16/16；evals 8/8（三次两绿口径）。
5. 现有四个测试文件 diff 为空（断言零改动）。

## 流程备注

- 007 创建时 006 处于 `ready-to-push`（已归档未推送），wu5 无「多变更排队」能力。经用户「那三个缺陷修一下」的直接指令，对 `spec/state.json` 的 phase 做一次受控例外（`ready-to-push` → `ready`，提交 fe0752b），006 的归档记录与 `last_completed` 原样保留，可经 git 回退。
