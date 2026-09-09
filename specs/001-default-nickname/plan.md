# Implementation Plan: default-nickname

**Branch**: `001-default-nickname` | **Date**: 2026-09-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-default-nickname/spec.md`

> Note: Filled in by `/speckit-plan` per spec.md + constitution + project reality.

## Summary

为未设置昵称的用户自动生成默认昵称（「账本成员」+ 4 位随机 hex），存入 `users.nickname`；移除 `list_ledger_members`/`list_members` 里 fallback 显示 openid 的兜底（`m["openid"][:8]`），任何展示成员处只用 nickname（默认或自设），永不显示 openid。默认昵称用户级一个、账本内唯一、自设后立即替换、空昵称不生效。

## Technical Context

**Language/Version**: Python 3.11（项目 venv，`.venv/Scripts/python.exe`）

**Primary Dependencies**: FastAPI（main.py）、langgraph（agent.py）、openai（llm.py）、sqlite3（db.py，原生，无 ORM）

**Storage**: SQLite（`ledger.db`），`users.nickname` 字段已存在（TEXT，可为空）

**Testing**: pytest（`tests/test_wechat_mock.py`，当前 18 用例）

**Target Platform**: Windows 本地 + 微信测试号 + 共账 agent

**Project Type**: web-service（FastAPI）+ agent 循环（langgraph）+ CLI 兜底

**Performance Goals**: 默认昵称生成即时（毫秒级），无额外网络调用

**Constraints**: 遵循 constitution——不显示 openid、账本内唯一、用户级一个。改动不破坏现有 18 测试。

**Scale/Scope**: 单用户自昵称 + 默认生成；涉及 db.py（数据层）、agent.py（工具/prompt）、可能补测试。

## Constitution Check

*GATE: MUST pass. Re-check after Phase 1.*

| 原则 | 符合性 | 说明 |
|---|---|---|
| I. 账本内身份标识，禁止显示 openid | ✅ | 移除 `m["openid"][:8]` 兜底，一律用 nickname |
| II. 账本内唯一 | ✅ | 生成默认昵称时校验账本内冲突，冲突重生成 |
| III. 默认昵称（账本成员+后缀，用户级一个，永不为空） | ✅ | 生成逻辑 + 空昵称不生效 |
| IV. 可改不追溯 | ✅ | `set_nickname` 直接覆盖，不追踪历史 |
| V. 跨账本可重名 | ✅ | 唯一性只在账本内校验 |
| 2.2 身份注入安全 | ✅ | 不改 openid 闭包注入机制 |
| 2.4 软删除 | ✅ | 不动 |
| 3.1 测试不回归 | ✅ | 补测试，18 用例不破坏 |

## Project Structure

### Documentation (this feature)

```text
specs/001-default-nickname/
├── spec.md            # (done) 规格
├── plan.md            # (this) 技术方案
├── research.md        # Phase 0
├── data-model.md      # Phase 1
├── quickstart.md      # Phase 1
└── tasks.md           # Phase 2 (/speckit-tasks)
```

### Source Code (real project layout)

```text
AI-SmartLedger/
├── db.py            # ← 改：生成默认昵称/唯一校验；list_ledger_members 不返 openid 兜底
├── agent.py         # ← 改：list_members 工具去掉 m["openid"][:8]；set_nickname 校验空/唯一
├── llm.py           # 不变
├── graph.py         # 不变
├── main.py          # 不变
├── config.py        # 不变
├── tests/
│   └── test_wechat_mock.py   # ← 补：默认昵称/唯一/不显示openid 测试
└── .venv/            # 项目 venv（不改）
```

**Structure Decision**: 单文件数据层（db.py）+ 单文件 agent（agent.py），沿用现有扁平结构，不新增目录。改动集中在 db.py（数据层）与 agent.py（工具/prompt），加测试。

## Complexity Tracking

无需填——Constitution Check 无违规（GATE 通过）。
