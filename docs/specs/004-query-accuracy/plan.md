# Implementation Plan: query-accuracy

**Branch**: `feat/002-shared-ledger`（004 在该分支上推进，随 PR #1 一起提交） | **Date**: 2026-10-01 | **Spec**: [spec.md](./spec.md)

> 说明：spec 定稿后直接实现，本 plan 与 `tasks.md` 随实现同批提交（research / data-model / quickstart 从简——本特性无新增实体、关键取舍已收敛在 spec 的 Assumptions 与本 plan 的 Decisions）。

## Summary

把「这个月花了多少」从"行内心算 + 受 limit 截断"改为**SQL 聚合**：新增 `sum_by_ledger`（无 LIMIT、按类型分组），明细降级为"金额最大的 ≤5 笔"（`order_by` 双模式），工具返回统一信封 `{records, totals, total_count, order_by}`；主路径（提示词）与兜底路径（`graph.py`）同口径；降级文案同步。**两条路径都不再允许从明细心算合计。**

## Technical Context

**Language/Version**: Python 3.11（项目 venv） | **Storage**: SQLite（无 schema 变更）

**改动文件**：
| 文件 | 改动 |
|---|---|
| `db.py` | 新增 `_date_bounds`（窗口归一，明细/聚合共用）、`sum_by_ledger`（`GROUP BY type`，无 LIMIT）；`query_by_ledger` 加 `order_by`（默认 `recent`，旧调用方零改动） |
| `agent.py` | `query_transactions` 工具：默认 `limit=5` + 钳制 `[1,50]`、`order_by` 默认 `top_amount`、统一信封（含已删账本分支）、docstring 重写；system prompt 查账条目同步 |
| `llm.py` | `summarize_query_result`：`totals` **必填**；正常 prompt 与降级文案都改用 totals（收支分开、删除 `sum()` 心算） |
| `graph.py` | 兜底路径先取聚合、再连同 rows 传入总结（与主路径同口径） |
| `tests/test_query_accuracy.py` | **新增 11 用例**（聚合正确性 / 收支分列 / 分类过滤 / top_amount 与 recent 排序 / 信封与钳制 / 已删账本同形状 / 兜底传 totals / totals 必填） |
| `tests/test_identity_selection.py`、`tests/test_shared_ledger.py` | 既有调用方补 `totals`（签名变更波及 2 处） |
| `evals/provider.py` | 种子"电影票"日期跨月修复（见下） |

**Testing**: pytest **114 passed**（103 → +11）；e2e **16/16**；evals（promptfoo，真实 LLM）**8/8**。

## Constitution Check

| 原则 | 符合性 | 说明 |
|---|---|---|
| I. In-Ledger Isolation | ✅ | 聚合与明细同样带 `ledger_id`；无跨账本读取 |
| II. Identity Injection Safety | ✅ | 信封只含 `created_by_nickname`；内部 id/openid 已 pop |
| III. Record Integrity | ✅ | 不涉及写入路径 |
| IV. LLM Extraction Fallback | ✅ | 兜底路径改为**聚合**（更不会写/算错）；降级文案确定性输出 |
| V. Agent Behavior | ✅ | 提示词明确"金额用 totals、禁止心算"；明细职责变化有文案说明 |
| VI/VII. 权限与审批 | ✅ | 不涉及 |
| VIII. Determinate Reference & Stable Anchors | ✅ | "不猜"：合计不再由模型猜算；明细与合计的关系明确（不暗示完整） |
| 5.1 Tests Must Not Regress | ✅ | 103 条旧用例全绿（仅 2 处签名调用方同步更新） |

## Decisions

- **D1 信封统一（含已删账本分支）**：正常与已删情形键结构一致（核心 4 键 + 已删附加 3 键），LLM 只面对一种 JSON 形状。
- **D2 `order_by` 两值，两层默认不同**：`db.query_by_ledger` 默认 `recent`（旧调用方零改动、不回归）；工具层默认 `top_amount`（明细职责 = 佐证合计，大头对得上才有说服力）。"最近买了啥"由 LLM 显式传 `recent`。
- **D3 `totals` 必填**：可选参数会让人漏传而悄悄退回心算——必填让遗漏立刻 TypeError（有测试锁定）。
- **D4 窗口逻辑共用**：抽 `_date_bounds`，明细与聚合共用一处；这是本仓库反复出现"同口径写两遍然后漂移"后的硬性收敛。
- **D5 limit 钳制 `[1, 50]`**：上限防上下文/消息长度失控（保险丝），下限防 `0`/负数穿透（不报错）。
- **D6 降级文案不引入千分位**：与全库既有金额格式一致（spec 对话样例为示意，非规定文案）。

## 实现中发现的补充项（已修）

**evals 夹具的第二处"月界"缺陷**：`_seed_reader` 把"电影票"播在"昨天"——每月 1 号落入上个月，导致「本月支出合计 ≈ 80」的 rubric 在 1 号必然失败（本月只剩午餐 30）。**2026-10-01 当天实测抓到**（evals 7/8，失败项即此；bot 答 30 对该月而言正确，是 rubric 错）。修复：跨月时把电影票改播在本月 1 号 19:00（与先前修的"种子守卫跨月"同属一类，已在注释中互相引用）。修复后 evals 8/8。

## 验证记录

| 验证 | 结果 |
|---|---|
| `pytest tests/ -q` | **114 passed** |
| `scripts/e2e_shared_ledger.py` | **16/16** |
| evals（真实 LLM，含 1 号月界场景） | **8/8** |
| 复现场景归零（25 笔/¥1250） | 聚合报 **1250**；对照 limit=20 的明细之和仍为 800（解耦验证） |

## 范围外

分页/导出/按成员聚合/趋势图；`tools.py::QueryParams.limit`（兜底路径的明细条数）保持 20——兜底是"降级浏览"路径，其合计已由 totals 保证正确，条数不影响正确性。
