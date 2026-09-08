<!--
SYNC IMPACT REPORT
Version change: none (initial) → 1.0.0
Added sections:
  - Core Principles I-V: Nickname identity, uniqueness, default nickname, mutability, per-ledger scope
  - Section 2: Security & Data Safety (in-ledger isolation, identity injection safety, owner-only management, soft delete)
  - Section 3: Development Workflow (no test regression, centralized model default, clean PYTHONPATH)
Removed sections: none
Follow-up TODOs: none
-->

# AI-SmartLedger Constitution

**Version**: 1.0.0 | **Ratified**: 2026-09-04 | **Last Amended**: 2026-09-04

## Core Principles

### I. Nickname as In-Ledger Identity
用户昵称（nickname）是成员在**账本内**的展示名与识别标识，用于成员列表、按昵称定位成员、按昵称移除成员。nickname **绝不暴露真实身份**——对外展示一律用 nickname，**禁止显示 openid**（含 openid 前缀/截断等兜底）。openid 是敏感身份，只允许在服务端内部用于身份定位。

### II. Nickname Uniqueness Within a Ledger
同一账本内，成员的 nickname **必须唯一**。新增或修改昵称时，若与账本内现有成员冲突，系统必须拒绝并提示换一个（或强制加后缀去重），绝不允许同名成员并存——否则「按昵称移除/定位」会产生歧义，导致误操作。

### III. Default Nickname (Auto-Provisioned)
用户未主动设置昵称时，系统为其自动生成一个**默认昵称**（像微信号一样），格式为「账本成员 + 随机后缀」（如「账本成员 a3f9」）。默认昵称是**用户级**的（一个用户一个默认昵称，非每账本各一个）。nickname 永不为空——任何展示成员的地方，都用 nickname（默认或自设），**不得 fallback 显示 openid**。默认昵称在用户主动设置后即被替换。

### IV. Nickname Mutable, Non-History-Shifting
用户可随时修改自己的昵称，改后立即生效（成员列表、按昵称定位/移除都用新昵称）。修改昵称**不追溯历史**——不改变既有账目的归属，只影响当前展示与后续识别。

### V. Nickname Scope Is Per-Ledger
昵称是**账本内**概念，**跨账本允许重名**。不要求全局唯一；同一用户在不同账本可有不同昵称（或各自默认昵称）。唯一性约束仅在**同一账本内**生效。

## Section 2: Security & Data Safety

### 2.1 In-Ledger Isolation (NON-NEGOTIABLE)
记账/查账**必须带 `ledger_id`**（用 `insert_many_for_ledger` / `query_by_ledger`），严禁用会写/查「无主账」(ledger_id=NULL) 的旧 `insert_many` / `query`。任何账本的数据只能被该账本成员看到。

### 2.2 Identity Injection Safety
openid 身份只能通过**闭包注入**（`make_tools(openid)`）传给工具，**严禁**放进 LLM 工具 schema 或用户消息。LLM 不得看到或修改身份；`admin_remove_member` 等管理操作按 nickname 查人，绝不依赖 LLM 传来的身份。

### 2.3 Owner-Only Management
管理操作（移除成员 / 改账本名 / 删账本）**仅账本 owner 可执行**，member 调用必须被拒绝。

### 2.4 Soft Delete
账本删除采用**软删除**（`ledgers.deleted_at`），账目永不物理删除，可追溯。禁止把事务的 `ledger_id` 置 NULL（会产生不可见的孤儿数据）。

## Section 3: Development Workflow

### 3.1 Tests Must Not Regress
改动不得破坏既有测试（`tests/test_wechat_mock.py`，当前 18 用例）。新增功能必须补对应测试。

### 3.2 Model Default Is Centralized
LLM 模型默认值统一从 `config.DEFAULT_MODEL` 读取，各文件不得硬编码。

### 3.3 Run With Clean PYTHONPATH
项目内跑 Python 时，运行命令须清空 `PYTHONPATH`（`PYTHONPATH= .venv/Scripts/python.exe ...`），避免误用外部包。

## Governance

- Constitution 优先于任何临时做法；如需偏离，必须先在 `.specify/memory/constitution.md` 记录并说明理由。
- 版本由语义化版本（SemVer）管理：MAJOR = 原则删除/重定义；MINOR = 新增原则/章节；PATCH = 措辞澄清。
- 所有实现须对照本 constitution 校验合规；本文件为 `.specify/memory/constitution.md`，是运行时权威来源。
