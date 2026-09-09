<!--
SYNC IMPACT REPORT
Version change: 1.0.0 → 1.1.0 (MINOR: 结构调整 + 新增核心原则)
Modified principles:
  - I: 由 Nickname as In-Ledger Identity 重定义为 In-Ledger Isolation (记账/查账必须带 ledger_id)
  - II: 由 Nickname Uniqueness 重定义为 Identity Injection Safety (openid 闭包注入, 不暴露)
  - III: 新增 Record Integrity (记账完整性与校验, 不写脏数据)
  - IV: 新增 LLM Extraction Fallback (LLM 抽取失败兜底)
  - V: 新增 Agent Behavior (记账确认/信息不足反问)
Added sections:
  - Section 2: Nickname 约定 (归并原 I-V 的 nickname 原则, 不再占5条Core)
  - Section 3: Data Lifecycle & Discipline (软删除/幂等/YAGNI)
  - Section 4: Development Workflow (原 Section 3)
Removed sections: none
Follow-up TODOs: none
-->

# AI-SmartLedger Constitution

**Version**: 1.1.0 | **Ratified**: 2026-09-04 | **Last Amended**: 2026-09-04

## Core Principles (NON-NEGOTIABLE — 项目级铁律)

### I. In-Ledger Isolation
记账/查账**必须带 `ledger_id`**（用 `insert_many_for_ledger` / `query_by_ledger`），严禁用会写/查「无主账」(ledger_id=NULL) 的旧 `insert_many` / `query`。任何账本的数据只能被该账本成员看到——**账本隔离是数据安全的核心**。

### II. Identity Injection Safety
用户的 openid 身份只能通过**闭包注入**（`make_tools(openid)`）传给工具，**严禁**放进 LLM 工具 schema 或用户消息。LLM 不得看到或修改身份；管理操作按 nickname 查人，绝不依赖 LLM 传来的身份。**openid 永不对外展示**（含前缀/截断兜底）。

### III. Record Integrity
写入的账目必须**完整且合法**——金额须为有效正数、分类须在预设清单内、type 只能是 expense/income。严禁写入脏数据（空金额、非法分类、NULL ledger_id）。记账必须是**原子的**（整批全成或全不成）。

### IV. LLM Extraction Fallback
LLM 从自然语言抽取账目**不可靠**，必须兜底：抽取失败/缺金额 → 不发错误回执，而是**反问澄清**（ask_clarify）或提示重发；**绝不在兜底路径写信无主账**。兜底路径写操作已收敛为一条（agent 工具），记账失败必须明确告知用户"没记上"。

### V. Agent Behavior
Agent 记账/查账后**必须确认**（回复用户结果+当前账本名）；信息不足时**必须反问**（ask_clarify）而不是猜；不确定用户在干嘛时友好提示用途。**不做超出用户意图的操作**（YAGNI，防过度设计）。

## Section 2: Nickname 约定

昵称（nickname）是成员在账本内的展示名与识别标识，用于成员列表、按昵称定位/移除成员。

- **禁止显示 openid**：对外展示一律用 nickname，绝不 fallback 显示 openid（含截断前缀）。`list_ledger_members` 的 SELECT **不返回 openid**（从源头杜绝）。
- **账本内唯一**：同一账本内 nickname 必须唯一，冲突时拒绝或重生成（`_gen_default_nickname` 防碰撞）。
- **默认昵称**：未设置时自动生成「账本成员 + 4位随机hex」（如"账本成员 a3f9"），**用户级一个**（各账本相同），落库到 `users.nickname`，永不为空。
- **可改不追溯**：用户 `set_nickname` 覆盖默认，立即生效；空/空白昵称不生效；改名不改变账目归属。
- **账本内作用域**：跨账本允许重名，唯一性仅在账本内生效。

## Section 3: Data Lifecycle & Discipline

### 3.1 Soft Delete
账本删除采用**软删除**（`ledgers.deleted_at`），账目永不物理删除、可追溯。禁止把事务的 `ledger_id` 置 NULL（会产生不可见的孤儿数据）。

### 3.2 Idempotency (去重/幂等)
微信回调可能重复（msgid 幂等去重），**同一消息不能重复记账**。用 `_seen_cache`（msgid → True）保证同一 msgid 只处理一次。

### 3.3 YAGNI (不过度设计)
**只做用户明确要求的，不自行添加功能**。新增功能须先写规格（spec）并获用户确认，**不擅自扩大范围**。宁可少做，不做多余——过度设计是"跑偏"的根源。

### 3.4 Owner-Only Management
管理操作（移除成员 / 改账本名 / 删账本）**仅账本 owner 可执行**，member 调用必须被拒绝。

## Section 4: Development Workflow

### 4.1 Tests Must Not Regress
改动不得破坏既有测试（`tests/test_wechat_mock.py`）。新增功能必须补对应测试，保持原有用例全绿。

### 4.2 Model Default Is Centralized
LLM 模型默认值统一从 `config.DEFAULT_MODEL` 读取，各文件不得硬编码。

### 4.3 Run With Clean PYTHONPATH
项目内跑 Python 时，运行命令须清空 `PYTHONPATH`（`PYTHONPATH= .venv/Scripts/python.exe ...`），避免误用外部包。

## Governance

- 本 Constitution 优先于任何临时做法；如需偏离，必须先在 `.specify/memory/constitution.md` 记录并说明理由。
- 版本由语义化版本（SemVer）管理：MAJOR = 原则删除/重定义；MINOR = 新增原则/章节；PATCH = 措辞澄清。
- 所有实现须对照本 constitution 校验合规（尤其是 `/speckit-plan` 的 Constitution Check）。本文件是运行时权威来源。
