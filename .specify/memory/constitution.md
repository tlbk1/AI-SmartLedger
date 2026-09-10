<!--
SYNC IMPACT REPORT
Version change: 1.1.0 → 1.2.0 (MINOR: 新增共账机制核心原则)
Modified principles: none (I-V 保留)
Added principles:
  - VI. Ledger Ownership & Two-Tier Permission (账本所有权 + owner/member 两级权限)
  - VII. Approval-Based Join (审批制加入: 口令申请 → owner 同意 → 才成为成员)
Added sections:
  - Section 2: 账本与成员机制细则 (口令规则/成员变更/默认账本与多账本/管理目标)
Modified sections:
  - 原 Section 2 (Nickname) → 变为 Section 3
  - 原 Section 3 (Data Lifecycle) → 变为 Section 4
  - 原 Section 4 (Dev Workflow) → 变为 Section 5
Removed sections: none
Follow-up TODOs: none
-->

# AI-SmartLedger Constitution

**Version**: 1.2.0 | **Ratified**: 2026-09-04 | **Last Amended**: 2026-09-04

## Core Principles (NON-NEGOTIABLE — 项目级铁律)

### I. In-Ledger Isolation
记账/查账**必须带 `ledger_id`**（用 `insert_many_for_ledger` / `query_by_ledger`），严禁用会写/查「无主账」(ledger_id=NULL) 的旧 `insert_many` / `query`。任何账本的数据只能被该账本成员看到——**账本隔离是数据安全的核心**。

### II. Identity Injection Safety
用户的 openid 身份只能通过**闭包注入**（`make_tools(openid)`）传给工具，**严禁**放进 LLM 工具 schema 或用户消息。LLM 不得看到或修改身份；管理操作按 nickname 查人，绝不依赖 LLM 传来的身份。**openid 永不对外展示**（含前缀/截断兜底）。

### III. Record Integrity
写入的账目必须**完整且合法**——金额须为有效正数、分类须在预设清单内、type 只能是 expense/income。严禁写入脏数据（空金额、非法分类、NULL ledger_id）。记账必须是**原子的**（整批全成或全不成）。

### IV. LLM Extraction Fallback
LLM 从自然语言抽取账目**不可靠**，必须兜底：抽取失败/缺金额 → 不发错误回执，而是**反问澄清**（ask_clarify）或提示重发；**绝不在兜底路径写无主账**。兜底路径写操作已收敛为一条（agent 工具），记账失败必须明确告知用户"没记上"。

### V. Agent Behavior
Agent 记账/查账后**必须确认**（回复用户结果+当前账本名）；信息不足时**必须反问**（ask_clarify）而不是猜；不确定用户在干嘛时友好提示用途。**不做超出用户意图的操作**（YAGNI，防过度设计）。

### VI. Ledger Ownership & Two-Tier Permission
账本**创建者即 owner**（唯一），其余成员为 **member**；权限**严格两级**：
- **owner 可**：审批加入申请、移除成员、改账本名、删账本
- **member 可**：记账、查账、查看本账本成员列表
**权限判定必须基于"目标账本内该成员的角色"**，不得因用户「当前/最近账本」不同而误判。管理操作一律先校验操作者在该账本内是 owner，member 调用必须被拒绝。

### VII. Approval-Based Join (审批制)
**加入账本必须经 owner 同意**：申请人凭口令提交加入申请 → 进入**待审批**状态 → owner 同意后才成为 member、才能访问该账本数据。审批前申请人**不得**看到账本任何数据。owner 拒绝则申请失效（拒绝申请为后续增强，本期预留 `rejected` 状态值；本期待审批列表只增不减，详见 spec 002）。**待审批申请必须可被 owner 查询与处理**（见 Section 2 兜底）。

## Section 2: 账本与成员机制细则

### 2.1 口令规则
- 每个账本一个**唯一口令**（随机、去易混淆字符），口令用于**识别账本并提交加入申请**。
- 口令**不是加入凭证**——拿到口令只能"申请"，最终是否加入由 owner 决定（原则 VII）。
- 口令随账本创建生成；账本软删除后其口令失效。

### 2.2 成员变更
- **移除成员**：owner 可按 nickname 移除本账本内成员；移除**立即生效**（该成员不再能看到账本数据）。被移除者的**历史账目保留在账本内**（删除成员≠删除数据），其 `created_by_user_id` 归属不变。
- **改名**：owner 可改账本名。
- **删账本**：owner 可删（**软删除**，见 4.1），账目保留可追溯。
- **退出**：member 可主动退出账本（非 owner），退出后不再可见数据，历史账目同样保留。

### 2.3 默认账本与多账本
- 每个用户**自动拥有一个默认账本**（创建用户时生成，该用户为 owner），保证任何用户任何时刻至少有一个账本——机制同默认昵称。
- 用户**可创建多个账本**，也可加入多个（他人）账本。
- 每个用户有**一个当前账本**（current_ledger_id）；新创建/（经同意）新加入的账本**不自动切换**，由用户显式切换。

### 2.4 管理目标（避免歧义）
- 涉及账本的管理操作（移除成员/改名/删账本/审批），**必须明确作用于哪个账本**——由用户指定，或先确认当前账本；**不得静默依赖"最近账本"**做危险操作。
- 当用户有多个账本时，agent 应确认"你要操作哪个账本"再执行。

### 2.5 待审批的可得性（兜底）
- 公众号为被动响应场景：owner 未必能在申请时立即被通知。系统**必须保证 owner 主动查看时能看到全部待审批申请**（如 owner 发消息时提示"N 条待审批"），不依赖必须主动推送。

## Section 3: Nickname 约定

昵称（nickname）是成员在账本内的展示名与识别标识，用于成员列表、按昵称定位/移除成员。

- **禁止显示 openid**：对外展示一律用 nickname，绝不 fallback 显示 openid（含截断前缀）。`list_ledger_members` 的 SELECT **不返回 openid**（从源头杜绝）。
- **账本内唯一**：同一账本内 nickname 必须唯一，冲突时拒绝或重生成（`_gen_default_nickname` 防碰撞）。
- **默认昵称**：未设置时自动生成「账本成员 + 4位随机hex」（如"账本成员 a3f9"），**用户级一个**（各账本相同），落库到 `users.nickname`，永不为空。
- **可改不追溯**：用户 `set_nickname` 覆盖默认，立即生效；空/空白昵称不生效；改名不改变账目归属。
- **账本内作用域**：跨账本允许重名，唯一性仅在账本内生效。

## Section 4: Data Lifecycle & Discipline

### 4.1 Soft Delete
账本删除采用**软删除**（`ledgers.deleted_at`），账目永不物理删除、可追溯。禁止把事务的 `ledger_id` 置 NULL（会产生不可见的孤儿数据）。

### 4.2 Idempotency (去重/幂等)
微信回调可能重复（msgid 幂等去重），**同一消息不能重复记账**。用 `_seen_cache`（msgid → True）保证同一 msgid 只处理一次。

### 4.3 YAGNI (不过度设计)
**只做用户明确要求的，不自行添加功能**。新增功能须先写规格（spec）并获用户确认，**不擅自扩大范围**。宁可少做，不做多余——过度设计是"跑偏"的根源。

## Section 5: Development Workflow

### 5.1 Tests Must Not Regress
改动不得破坏既有测试（`tests/test_wechat_mock.py`）。新增功能必须补对应测试，保持原有用例全绿。

### 5.2 Model Default Is Centralized
LLM 模型默认值统一从 `config.DEFAULT_MODEL` 读取，各文件不得硬编码。

### 5.3 Run With Clean PYTHONPATH
项目内跑 Python 时，运行命令须清空 `PYTHONPATH`（`PYTHONPATH= .venv/Scripts/python.exe ...`），避免误用外部包。

## Governance

- 本 Constitution 优先于任何临时做法；如需偏离，必须先在 `.specify/memory/constitution.md` 记录并说明理由。
- 版本由语义化版本（SemVer）管理：MAJOR = 原则删除/重定义；MINOR = 新增原则/章节；PATCH = 措辞澄清。
- 所有实现须对照本 constitution 校验合规（尤其是 `/speckit-plan` 的 Constitution Check）。本文件是运行时权威来源。
