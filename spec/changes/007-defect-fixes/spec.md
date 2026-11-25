# 行为规格：007-defect-fixes

## ADDED Requirements

### REQ-007_DEFECT_FIXES-001：记账工具拒绝畸形入参且不写库

`record_transactions` 必须对 LLM 传入的 `transactions` 做逐项校验；任何畸形入参必须返回明确的中文提示（要求模型向用户确认后重试），且**不得写入任何账目**、不得回复「已记 N 笔」。

#### Scenario：空列表不得假成功

- **前置条件**：用户已在某账本中，账本内账目数为 N。
- **当**：模型调用 `record_transactions([])`。
- **则**：返回提示说明没有可记的账目并要求向用户确认金额；账本内账目数仍为 N（查库确认）。

#### Scenario：缺 amount 或金额不可转数字

- **前置条件**：用户已在某账本中，账本内账目数为 N。
- **当**：模型调用 `record_transactions`，任一笔缺少 `amount` 或值为 `"12元"`/`None`/空串等不可转 float 的内容。
- **则**：返回提示指出第几笔金额无效并给出原值，要求向用户确认具体金额；账本内账目数仍为 N（查库确认）；不得抛出异常。

#### Scenario：元素不是对象

- **前置条件**：用户已在某账本中，账本内账目数为 N。
- **当**：模型调用 `record_transactions`，元素为字符串/数字等非 dict。
- **则**：返回提示指出第几笔不是有效账目（需含 type/amount/category）；账本内账目数仍为 N（查库确认）；不得抛出异常。

#### Scenario：合法入参行为与基线一致

- **前置条件**：用户已在某账本中。
- **当**：模型调用 `record_transactions`，传入合法的一笔或多笔（type/category 缺省、note 缺省、happened_at 缺省或空串）。
- **则**：正常写入并回复「✓ 已记 N 笔」；缺省字段与既有兜底（type=expense、category=其他、note 空、happened_at 空串→now）逐字一致。

### REQ-007_DEFECT_FIXES-002：主链回复只取最终 AI 消息

`run_agent` 必须仅接受「结果消息列表的最后一条」作为候选回复，且该消息必须是 AI 类型（`type == "ai"`）且 content 非空；否则必须返回 `AGENT_FAILURE` 哨兵（由 router 走兜底链），不得回退取历史中的 human/tool/更早的 AI 消息。

#### Scenario：正常路径不变

- **前置条件**：agent 循环正常结束，最后一条为有内容的 AI 消息。
- **当**：`run_agent` 提取回复。
- **则**：返回该内容，并按原逻辑把本轮 user + assistant 追加进聊天历史。

#### Scenario：最后一条 AI 内容为空

- **前置条件**：结果消息最后一条为 AI 但 content 为空（如只带 tool_calls），历史中存在 human 消息与更早的 AI 消息。
- **当**：`run_agent` 提取回复。
- **则**：返回 `AGENT_FAILURE`；不得返回 human 消息文本、不得返回更早的 AI 文本；不追加聊天历史。

#### Scenario：最后一条为工具消息或其他类型

- **前置条件**：结果消息最后一条为 tool（或 human）类型。
- **当**：`run_agent` 提取回复。
- **则**：返回 `AGENT_FAILURE`；不追加聊天历史。

### REQ-007_DEFECT_FIXES-003：既有行为不回归

本次改动之外，现有 117 个用例的断言必须零改动；e2e 用户旅程 16/16 与 evals 8 场景保持通过。

#### Scenario：全量回归

- **前置条件**：实现完成。
- **当**：运行全量 pytest、e2e 脚本与 promptfoo evals。
- **则**：pytest 117 + 新增全绿且现有四个测试文件 diff 为空；e2e 16/16；evals 8/8。

## CHANGED Requirements

### REQ-007_DEFECT_FIXES-004：record_transactions 的入参契约收紧

记录（`docs/specs/002-shared-ledger`、`006` 工具文档串）中隐含「任何 list 都会被尽力写入」的旧契约，自本变更起改为：畸形批次整批拒绝、给可纠正提示、零写入。工具 docstring 必须同步说明拒绝条件（模型据此在调用前自检），其余描述保持不变。

#### Scenario：模型可见的契约说明同步

- **前置条件**：实现完成。
- **当**：读取 `record_transactions` 的工具描述。
- **则**：描述中含「空批次/缺金额/金额无效会被拒绝并返回提示」的表述；其余文案与基线一致。

## REMOVED Requirements

### REQ-007_DEFECT_FIXES-005：F-b（admin 工具空账本判空）不实现

ocr 缺陷评审提出的「`resolve_admin_ledger` 返回 `(None, None)` 时 admin 工具应向 db 层传 None 前先判空」**不予实现**，理由（证据）：

1. db 层四个 admin 函数均以 `ledger_id: Optional[int] = None` 接收，内部已实现「None → 回退当前账本；仍为 None → `return False, "你没有加入任何账本"`」，工具层透传得到的是准确中文提示而非异常（`db/members.py:198/277/297/357`）。
2. 该分支实际不可达：`run_agent` 每个请求先 `db.get_or_create_user(openid)`，用户必然拥有默认账本。
3. 在工具层再加判空会造成两层重复校验与文案分叉，增加维护面而无用户收益。

#### Scenario：驳回结论可复核

- **前置条件**：阅读本规格与 `db/members.py` 对应函数。
- **当**：对无账本状态调用四个 admin 工具（或构造该状态）。
- **则**：返回「操作失败：你没有加入任何账本」一类的明确文案，不抛异常、不产生误导性成功。
