# 技术设计：007-defect-fixes

## 当前状态

- F-a：`ai_smartledger/tools/builtin/transactions.py` 的 `record_transactions` 直接构造 `db.Transaction(amount=float(t["amount"]), ...)`，无 isinstance / 键存在 / 数值可转校验；空列表一路走到 `db.insert_many_for_ledger`，该函数首个分支 `if not txns: return True`（`db/transactions.py`），工具据此回「✓ 已记 0 笔」。
- F-c：`ai_smartledger/agents/react_agent.py::run_agent` 末尾：

  ```python
  for m in reversed(result_msgs):
      if getattr(m, "type", "") != "tool" and getattr(m, "content", ""):
          reply = m.content
          break
  ```

  过滤条件排除 tool，但 human 与更早的 ai 消息都可能被选中。
- F-b（不修）：`db/members.py` 四个 admin 函数均 `Optional[int] = None` + 内部回退与「你没有加入任何账本」提示，调用链无异常；分支不可达（run_agent 先 get_or_create_user）。
- 现状测试覆盖：`record_transactions` 只有合法路径用例（test_query_accuracy:222/237/251、test_shared_ledger:485）；`run_agent` 的回复提取零用例（`AGENT_FAILURE` 在 tests/ 中从未出现）。

## 方案与数据流

### F-a：工具层逐项校验（不改 db 层）

在 `record_transactions` 内、构造 `Transaction` 前增加三道闸门，命中即 return（不写库、不抛异常）：

1. `if not transactions: return "没有要记的账目：transactions 为空。请向用户确认要记的内容和金额后重试。"`
2. `isinstance(t, dict)` 不成立 → `"记账失败：第 {idx} 笔不是有效账目（需要含 type/amount/category 的对象），请核对后重试。"`
3. `float(t["amount"])` 捕获 `(KeyError, TypeError, ValueError)` → `"记账失败：第 {idx} 笔金额无效（{t.get('amount')!r}），请向用户确认具体金额后重试。"`

合法项照旧构造：`type=str(t.get("type","expense"))`、`category=str(t.get("category","其他"))`、`note=str(t.get("note",""))`、`happened_at=str(t.get("happened_at") or "").strip()`（空串→db 层 now 兜底，004 已修）。`get_or_create_user` 与 `insert_many_for_ledger` 调用位置不动。

选择在工具层而非 db 层的原因：db 层是所有写路径的通用入口（e2e 脚本、未来定时任务也直接用），`insert_many_for_ledger([]) → True` 是「无事可做」的合法内部契约；「把 LLM 的畸形输入翻译成一句模型能据此自纠的中文」是工具的职责。改 db 层会改动契约并波及无关调用方。

工具 docstring 末尾补一行拒绝条件说明（模型可在调用前自检）；其余描述逐字不动。

### F-c：只认最后一条 AI 消息

```python
result_msgs = result.get("messages", [])
last = result_msgs[-1] if result_msgs else None
reply = last.content if last is not None and getattr(last, "type", "") == "ai" else ""
if not reply:
    logger.warning("agent 未产出最终回复，标记为失败: %s", content[:30])
    return AGENT_FAILURE
```

语义依据：`create_react_agent` 正常结束时的最后一条消息必然是含最终文本的 AIMessage；以 tool 结尾说明循环被截断（recursion_limit）或模型异常，此时没有可信回复。基线「往回捞一条」的本质是猜，猜错会把用户的话或上一轮旧回复发出去。失败信号交给既有 `router.process_message` 的哨兵分支（兜底链），用户得到的是明确兜底文案而不是幻觉回复。

`db.append_chat_history` 保持在成功分支内，语义不变（失败不写历史）。

## 接口与兼容性

- `record_transactions` 的**返回值**仍是 str；合法路径逐字不变；新增拒绝分支的文案是给模型看的中文指令（要求其向用户确认），不改变工具 schema 与参数。
- 工具 docstring 变化仅新增一行拒绝条件；`AGENT_SYSTEM_PROMPT` 的工具说明不含该细节，无需同步。
- `run_agent` 返回类型不变（str / `AGENT_FAILURE` 哨兵）；`router`、`main`、测试对哨兵的既有处理不变。
- db 层、wechat、notify、tools 其余 16 个工具零改动。

## 配置、常量与依赖注入

- 新增三处中文提示文案与一行 docstring：属工具对模型的协议反馈，不随环境/部署/策略变化，与基线风格一致内联；不引入配置项（若提取常量会与既有 30+ 处内联文案风格分叉）。
- 无新增环境变量、路径、URL、魔法数字。
- 测试中 openid/金额为测试输入（合理例外）；`iso` 隔离库 fixture 沿用现有四个测试文件的写法（conftest 收拢仍留债务候选，本变更不做）。

## 备选方案与取舍

1. **F-a 在 db 层校验**：拒绝。db 是通用写入口，空列表返回 True 是合法内部契约；且 db 层给不出「请向用户确认金额」这类模型可执行的指令。
2. **F-a 只修空列表**：拒绝。畸形金额同样会中断循环，且 ocr 与用户可感知路径一致（用户说「修那三个缺陷」指整组入参健壮性）。
3. **F-c 保留「从后往前找 AI」**：拒绝。仍可能取到上一轮旧回复（同样错误）；只有「最后一条」语义与 ReAct 结束状态一致。
4. **F-c 改为取最后一条非 tool 消息（不限类型）**：拒绝。human 消息也会入选，等于回退到用户原话。
5. **F-b 按评审建议在工具层加判空**：拒绝，见 spec REMOVED 的三条证据。

## 失败模式、安全与回滚

- 新校验自身出错的可能性：仅字符串拼接与类型判断，无 IO；异常路径不触及 db。
- 误判合法入参的风险：`float("35")`、`float(35)`、`float("35.5")` 均通过；仅拒绝不可转数值的类型——与基线最终写入所需的类型完全一致，不会拒绝基线能写入的合法值。
- 回复提取收紧导致合法回复被丢弃的风险：正常完成时最后一条必为有内容 AI 消息（e2e 16 项与 evals 8 场景覆盖真实链路）；若出现误判，症状是走兜底链，仍给出可用回复，不静默。
- 安全：不涉及认证/权限/密钥；不新增外部调用。
- 回滚：单分支 `fix/007-defect-fixes`（从 refactor/006-agent-layers 切出）上 revert 即可；db 零改动、无数据迁移。

## 数据迁移

`none`。如需要数据库结构或不可逆数据迁移，停止本 Skill 流程。
