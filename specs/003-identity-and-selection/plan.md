# Implementation Plan: identity-and-selection

**Branch**: `feat/002-shared-ledger`（003 在该分支上推进，随 PR #1 一起提交） | **Date**: 2026-09-17（回填） | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-identity-and-selection/spec.md`

> **备注（回填说明）**：本 plan 与配套的 research / data-model / quickstart / tasks 系**实现完成之后补齐**——spec 定稿（b168428/b617737）后代码直接落地（327ebc4），规格产物链未同步生成，故此处回填。文档内容与**实际已实现代码**对齐，不以"计划中"的口径书写；tasks.md 中任务全部标记完成并附实际产物。

## Summary

把 spec 001/002 遗留的**功能交叉处**问题按**用户旅程主轴**收口——「谁」与「哪本」在每次交互都必须确定、可预期。八条主线：

1. **US1 关注与回来**：subscribe 即初始化身份+默认账本（幂等）、五要素欢迎语、首关/再关注差异化、unsubscribe 零数据变更
2. **US2 昵称全局唯一**：UNIQUE 索引 + 前置校验 + 存量重名检测/清理，按名定位永远唯一命中
3. **US3 默认账本锚点化**：`users.default_ledger_id` 指针替代"按名字识别"；列表带编号/角色/成员名单/三态标记
4. **US4 选择不猜**：`#N` 编号精确匹配 + 名称唯一直接生效 + 重名列候选请回复编号
5. **US5 管理指定账本**：六个管理操作全部支持 `ledger_name` 定位（含跨账本审批查询）
6. **US6 已删只读 + 记账人**：六个写操作全部加已删守卫；查账两条路径（正常/降级）都显示记账人当前昵称
7. **US7 回落不落空**：失效事件后 `current_ledger_id` 回落到**自己的**默认账本（含 owner 本人）；读时兜底链 `默认 → 最近加入 → 报错`
8. **US8 通道唯一 + 通知不丢**：移除绕过审批的死入口；通知改持久化 `undelivered_notices`，重启不丢、下次对话补发

## Technical Context

**Language/Version**: Python 3.11（项目 venv）

**Primary Dependencies**: FastAPI（main.py）、langgraph（agent.py，`create_react_agent`）、openai（llm.py）、sqlite3（db.py，原生无 ORM）

**Storage**: SQLite（`ledger.db`）。现有表：`users` / `ledgers` / `ledger_members` / `transactions` / `join_requests`。**新增 `undelivered_notices` 表**；**新增 `users.default_ledger_id` 列**；**新增 `idx_users_nickname` 唯一索引**

**Testing**: pytest。新用例集中在 `tests/test_identity_selection.py`（31 个，`iso` fixture 隔离临时库）；回归面 `test_wechat_mock.py`(23) + `test_shared_ledger.py`(35) → **合计 89**，加端到端 `scripts/e2e_shared_ledger.py`（16 场景）

**Target Platform**: Windows 本地 + 微信公众号（被动响应 + 客服消息推送，48h 窗口）

**Project Type**: web-service + agent 循环

**Performance Goals**: 昵称唯一校验/选择器解析/已删守卫均为本地 SQLite 查询，毫秒级；`resolve_ledger_selector` 单次查询内完成，无额外往返

**Constraints**:
- 客服消息**只能发给 48h 内互动过的用户** → 通知"尽力而为"，兜底靠 owner 对话时自动提示（`_pending_joins_hint`）+ 待补发队列
- 不允许显示 openid；不允许写 `ledger_id=NULL` 的账目
- **不破坏既有 58 个测试**（23 + 35）；`PYTHONPATH` 必须清空
- 存量数据已完成盘点（1 用户 / 1 账本 / 0 账目），约束可直接建立

**Scale/Scope**: 单机 SQLite；新增 1 表 + 1 列 + 1 索引、~14 个 db 函数、1 个选择器解析函数、prompt 大改、2 个新测试文件；代码净增约 1162 行（含测试 475 行）

## Constitution Check

*GATE: MUST pass. Re-check after Phase 1.*

| 原则 | 符合性 | 说明 |
|---|---|---|
| I. In-Ledger Isolation | ✅ | 所有查询仍带 `ledger_id`；选择器命中范围**限定为该用户已加入的账本**（含已删），越界报错（FR-018/FR-021） |
| II. Identity Injection Safety | ✅ | 继续闭包注入 openid（`make_tools(openid)`）；昵称唯一拒绝**不透露占用者**（FR-007）；列表只出昵称（FR-017） |
| III. Record Integrity | ✅ | 不改写入路径；记账人按 `created_by_user_id` 动态关联（FR-032），归属不因改名漂移 |
| IV. LLM Extraction Fallback | ✅ | 不涉及抽取；仅降级文案路径补记账人（FR-032 降级分支） |
| V. Agent Behavior | ✅ | prompt 明确：重名不猜（转述候选等编号）、管理须指定账本、单账本免确认、删默认账本先确认 |
| VI. Ledger Ownership & Two-Tier Permission | ✅ | 权限按**目标账本**角色判定（FR-027，沿用 002 D2，本次补齐 `ledger_name` 透传） |
| VII. Approval-Based Join | ✅ | **本次核心**：移除绕过审批的死入口（FR-037），"成为成员"唯一通道 = 口令申请 → owner 审批 |
| **VIII. Determinate Reference & Stable Anchors（不猜·不空）** | ✅ | **本次核心**：①多候选必问（FR-019）②指向永不悬空（FR-012~014/FR-033~036）③引用键唯一（FR-006/FR-009） |
| 2.1 口令规则 | ✅ | 口令继续用于申请；重置作废旧申请（沿用 002） |
| 2.2 成员变更 | ✅ | 移除/退出保留账目；owner 不可被移除/退出（沿用 002） |
| 2.3 默认账本与多账本 | ✅ | **本次收紧**：默认账本 = 指向关系；改名不影响；删除自动重建空账本；`current_ledger_id` 永不为空；回落顺序固定；显式选择不被兜底改写 |
| 2.4 管理目标（避免歧义） | ✅ | **本次收紧**：删除「或先确认当前账本」漏洞；多账本未指定必须列候选；同名列候选禁止静默取一 |
| 2.5 待审批可得性 & 通知不丢失 | ✅ | **本次扩展**：申请后尽力通知 owner；失败/异常入**持久化**待补发；重启后仍在；下次对话补发 |
| Section 3 Nickname 约定 | ✅ | **本次收紧**：唯一性范围「账本内唯一」→「**全局唯一**」；默认昵称防碰撞范围随之全局化 |
| 4.1 Soft Delete | ✅ | **本次扩展**：已删 = 只读，六个写操作全部拒绝；重复删除不覆盖原删除时间 |
| 4.2 Idempotency | ✅ | subscribe 幂等建用户/建默认账本（UNIQUE openid 兜底）；重复申请沿用 002 |
| 4.3 YAGNI | ✅ | 编号复用内部 id（不引入编号状态）；不新增实体（`UndeliveredNotice` 用表承载，无 ORM 模型） |
| 5.1 Tests Must Not Regress | ✅ | 原 58 用例保持绿；新增 31 → 89 全绿 |
| 5.2 Model Default Is Centralized | ✅ | 不涉及模型变更 |
| 5.3 Run With Clean PYTHONPATH | ✅ | 全部命令带 `PYTHONPATH= ` |

**GATE 通过**（无违规，Complexity Tracking 留空）。

## Project Structure

### Documentation (this feature)

```text
specs/003-identity-and-selection/
├── spec.md            # (done — b168428/b617737/03ff32a)
├── plan.md            # (this — 回填)
├── research.md        # Phase 0（回填）
├── data-model.md      # Phase 1（回填）
├── quickstart.md      # Phase 1（回填）
├── checklists/
│   └── requirements.md  # (done — 本次刷新末项与 Notes)
└── tasks.md           # Phase 2（回填）
```

### Source Code (real project layout)

```text
AI-SmartLedger/
├── db.py        # ← 主改：default_ledger_id 锚点 / 回落链 / 选择器解析 / 已删守卫 /
│                #   昵称全局唯一+存量清理 / undelivered_notices 持久化 / 成员名单预览
├── agent.py     # ← 改：prompt 大改（编号、重名不猜、管理指定账本、删默认账本确认）+
│                #   ledger_name 透传 + _pending_joins_hint 兜底提示
├── main.py      # ← 改：subscribe 初始化 + 五要素/差异化欢迎语；unsubscribe 显式 no-op
├── wechat.py    # ← 改：access_token 过期判断改 time.monotonic（范围外小改）
├── llm.py       # ← 改：降级路径补记账人昵称（FR-032）
├── graph.py     # 不动
├── tools.py     # ← 改：移除免账本查询入口（FR-037 死代码清理，保留 QueryParams/TOOL_SCHEMA）
└── tests/       # ← 新增 test_identity_selection.py（31）；回归 test_wechat_mock.py / test_shared_ledger.py
```

**Structure Decision**: 沿用扁平结构。改动集中在 **db.py（数据层与规则收敛）** + **agent.py（工具透传与 prompt 行为约束）**，辅以 main.py（生命周期入口）、llm.py（降级展示）、wechat.py（时钟小改）。

## Phase 0: Research (关键决策)

见 `research.md`。核心决策：

- **D1**：默认账本 = **指针** `users.default_ledger_id`（替代按名字识别），带存量回填
- **D2**：账本编号 = **内部 id 的展示形式** `#N`（零迁移、永久稳定）；选择器统一走 `resolve_ledger_selector`
- **D3**：昵称全局唯一 = **UNIQUE 索引**（并发兜底）+ `validate_nickname` 前置校验 + 存量检测/清理
- **D4**：回落 = 事件内 `_settle_current_ledger`（确定性写入）+ 读时 `_fallback_ledger_id`（默认 → 最近加入 → 报错）
- **D5**：已删账本 = 只读，`is_ledger_deleted` 守卫嵌入全部 6 个写操作 + 记账双重防御
- **D6**：通知不丢 = **新增 `undelivered_notices` 表**（enqueue / drain），替代进程内存队列
- **D7**：生命周期 = subscribe 初始化 + 差异化欢迎语；unsubscribe **显式 no-op**（不删任何数据）
- **D8**：记账人 = 展示时动态关联**当前昵称**（否决快照）
- **D9**：移除"凭口令直接加入"死入口，`join_ledger` 只产生 pending 申请
- **D10**：令牌过期判断改单调时钟（范围外小改，一并落地）

## Phase 1: Design

见 `data-model.md`（新表/新列/新索引 + 关键规则 + 状态转移）与 `quickstart.md`（验证场景 V1~V16 + Definition of Done）。

## Complexity Tracking

无需填——Constitution Check 无违规（GATE 通过）。
