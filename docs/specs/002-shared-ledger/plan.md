# Implementation Plan: shared-ledger

**Branch**: `002-shared-ledger` | **Date**: 2026-09-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-shared-ledger/spec.md`

## Summary

把现有"共账骨架"补成完整的共账功能：**审批制加入**（口令申请 → pending → owner 同意 → 成为 member，加入/同意均尽力推送+兜底提示）、**权限严格按目标账本角色判定**（修"当前账本误判"缺陷）、**账目共享可见并显示记账人昵称**、**成员移除/退出**（历史账目保留）、**口令重置**（作废旧申请）、**删账本后成员回落默认账本**（历史可查+提示已删除）。

## Technical Context

**Language/Version**: Python 3.11（项目 venv）

**Primary Dependencies**: FastAPI（main.py）、langgraph（agent.py）、openai（llm.py）、sqlite3（db.py，原生无 ORM）

**Storage**: SQLite（`ledger.db`）。现有表：`users`/`ledgers`/`ledger_members`/`transactions`。**新增 `join_requests` 表**。

**Testing**: pytest（`tests/test_wechat_mock.py`，当前 23 用例）；新增用例用 `isolated_db` fixture 隔离

**Target Platform**: Windows 本地 + 微信公众号（被动响应 + 客服消息推送）

**Project Type**: web-service + agent 循环

**Performance Goals**: 审批/权限校验为本地 SQLite 查询，毫秒级

**Constraints**: 
- 客服消息**只能发给 48h 内互动过的用户**（推送尽力而为，兜底靠 owner 主动查）
- 不允许显示 openid；不允许写 ledger_id=NULL 的账目
- 不破坏现有 23 测试

**Scale/Scope**: 单机 SQLite；预计新增 1 表、~10 个 db 函数、~8 个 agent 工具、~15 个测试

## Constitution Check

*GATE: MUST pass. Re-check after Phase 1.*

| 原则 | 符合性 | 说明 |
|---|---|---|
| I. 账本隔离 | ✅ | 所有查询继续带 ledger_id；审批前申请人看不到数据 |
| II. 身份注入安全 | ✅ | 继续闭包注入 openid；申请/审批按 nickname 展示，不暴露 openid |
| III. 记账完整性 | ✅ | 不改写入路径（仍 insert_many_for_ledger） |
| IV. LLM 抽取兜底 | ✅ | 不涉及 |
| V. Agent 行为 | ✅ | 新增工具在 prompt 里说明用途；管理操作须确认账本 |
| VI. 所有权 & 两级权限 | ✅ | **本次核心**：权限判定改为按目标账本内角色 |
| VII. 审批制 | ✅ | **本次核心**：join_requests + pending → owner 同意 |
| 2.1 口令规则 | ✅ | 口令用于申请；新增重置（旧口令失效+作废旧申请） |
| 2.2 成员变更 | ✅ | 移除/退出保留历史账目；owner 不可被移除/退出 |
| 2.3 默认账本+多账本 | ✅ | 已实现（沿用） |
| 2.4 管理目标 | ✅ | 管理操作须明确账本（单账本免确认） |
| 2.5 待审批可得性 | ✅ | owner 任何对话时提示待审批 |
| 4.1 软删除 | ✅ | 删账本=软删除；成员回落默认账本 |
| 4.2 幂等 | ✅ | 重复申请幂等 |
| 5.1 测试不回归 | ✅ | 补测试，23 用例保持绿 |

**GATE 通过**（无违规，Complexity Tracking 留空）。

## Project Structure

### Documentation (this feature)

```text
specs/002-shared-ledger/
├── spec.md            # (done)
├── plan.md            # (this)
├── research.md        # Phase 0
├── data-model.md      # Phase 1
├── quickstart.md      # Phase 1
└── tasks.md           # Phase 2 (/speckit-tasks)
```

### Source Code (real project layout)

```text
AI-SmartLedger/
├── db.py        # ← 主改：join_requests 表 + 申请/审批/权限/退出/重置/回落 函数
├── agent.py     # ← 改：新增工具(申请加入/待审批/同意/退出/重置口令) + prompt
├── graph.py     # ← 改：兜底路径的记账人展示（如涉及）
├── llm.py       # ← 改：查账结果摘要需含记账人（summarize_query_result）
├── main.py      # ← 可能改：待审批提示接入
├── tools.py     # 不动（本次不退役，由 T000 边界决定）
└── tests/test_wechat_mock.py  # ← 补测试
```

**Structure Decision**: 沿用扁平结构，改动集中在 db.py（数据层）+ agent.py（工具/prompt），辅助改 llm.py（摘要含记账人）。

## Phase 0: Research (关键决策)

见 `research.md`。核心决策：
- **D1**：新增 `join_requests` 表（不改现有表语义）
- **D2**：权限判定函数改为 `is_ledger_admin(openid, ledger_id)`（显式传账本，不再依赖 current_ledger）
- **D3**：账目共享靠"查询不按 created_by 过滤"（现状已是），加 nickname 关联展示
- **D4**：审批推送"尽力而为"，失败入 undelivered + owner 主动查兜底
- **D5**：口令重置时把该账本 pending 申请置 `expired`
- **D6**：删账本后成员 current_ledger_id 回落默认账本

## Phase 1: Design

见 `data-model.md`（新表 + 关键规则）与 `quickstart.md`（验证场景）。

## Complexity Tracking

无需填——Constitution Check 无违规（GATE 通过）。
