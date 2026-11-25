# 规格与流程文档

当前开发流程：**dev-flow**（设计/计划 + delivery-gate 审计）。wu5-dev-flow 已于 2026-11-25 退役。

## 目录

| 目录 | 内容 |
|---|---|
| `001~005-*` | 旧 Spec Kit 规格归档（已退役） |
| `006-agent-layers/` | 006 分层重构归档 |
| `007-defect-fixes/` | 007 缺陷修复规格 |
| `baseline/` | wu5 基线：系统图/行为图/债务/风险/测试图 |
| `features/` | 006 接管时的结构快照 |
| `testing/` | wu5 测试策略 |
| `wu5-retired/` | wu5 的 index/project/status/state/workflow 历史快照 |
| `../superpowers/` | 当前 dev-flow 的 design/plan |
| `../viz/` | 代码图/时序图 |
| `../../.delivery-gate/` | dev-flow 审计记录 |

## 旧路径映射

| 旧路径（wu5） | 新路径 |
|---|---|
| `spec/archive/2026-10-29-006-agent-layers/` | `docs/specs/006-agent-layers/` |
| `spec/changes/007-defect-fixes/` | `docs/specs/007-defect-fixes/` |
| `spec/baseline/` | `docs/specs/baseline/` |
| `spec/features/` | `docs/specs/features/` |
| `spec/testing/` | `docs/specs/testing/` |
| `spec/index.md`、`project.md`、`status.md`、`state.json`、`workflow.json` | `docs/specs/wu5-retired/` |

> `docs/superpowers/` 和 `.delivery-gate*/` 未搬迁。`docs/superpowers` 中已批准的 design/plan 保留了批准时的原文；搬迁只调整了 spec 路径引用，历史审计哈希是搬迁前快照。
