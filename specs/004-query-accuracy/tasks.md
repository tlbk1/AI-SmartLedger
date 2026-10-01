# Tasks: query-accuracy

**Branch**: `feat/002-shared-ledger` | **Input**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

> 与 plan.md 同批（2026-10-01）：实现先行落地，本清单按实际产物回写并全部勾选。

## Phase 1: 数据层（合计与明细解耦）

- [x] T001 `db._date_bounds(date_from, date_to)`：窗口归一抽公共 helper（半开区间，明细/聚合共用）per FR-002
- [x] T002 `db.query_by_ledger` 加 `order_by`（`recent` 默认 / `top_amount` 金额降序+时间并列裁决），内部改用 `_date_bounds` per FR-005
- [x] T003 `db.sum_by_ledger(ledger_id, date_from, date_to, category, type_filter)`：`GROUP BY type` 的 COUNT/SUM，**无 LIMIT**，空账本返回 0 且不缺键，金额 `round(2)` 消浮点尾差 per FR-001/FR-003/FR-004

## Phase 2: 工具层与展示层

- [x] T004 `agent.query_transactions`：默认 `limit=5` + 钳制 `[1,50]`、`order_by` 默认 `top_amount`（非法值回落）、docstring 重写（totals 完整/必用、records 仅供浏览、禁止自行求和）per FR-006/FR-009
- [x] T005 工具返回统一信封 `{records, totals, total_count, order_by}`；**已删账本分支同一信封** + `ledger_deleted`/`ledger_name`/`notice` per FR-008
- [x] T006 `llm.summarize_query_result`：`totals` **必填**；正常 prompt 改用 totals（收支分开、明确"禁止根据明细求和"）；空结果判定改 `total_count==0` per FR-010
- [x] T007 降级文案改用 totals（`支出 ¥x（n 笔）、收入 ¥y（m 笔）`），明细逐条保留记账人昵称 per FR-013
- [x] T008 `graph.py` 兜底路径：先 `sum_by_ledger` 再连同 rows 传入总结（与主路径同口径，废除"取 20 条心算"）per FR-012
- [x] T009 system prompt 查账条目（工具行 + 工作方式第 2 条）同步"金额用 totals、禁止心算" per FR-011

## Phase 3: 测试与验证

- [x] T010 新增 `tests/test_query_accuracy.py`（11 用例）：复现归零（25 笔/1250，对照 limit=20 只剩 800）、空账本零值、收支分列、分类过滤、窗口共用、top_amount/recent 排序、信封与钳制（999→50、0→1、recent 透传）、已删账本同形状、兜底传 totals（spy）、totals 必填（TypeError）per FR-001~FR-013
- [x] T011 更新签名变更的既有调用方：`tests/test_shared_ledger.py`（空结果确定性文案）、`tests/test_identity_selection.py`（降级文案断言 + 收支分开新断言）per FR-010
- [x] T012 全量验证：pytest **114 passed** + e2e **16/16** + evals（真实 LLM）**8/8**

## Phase 4: 实现中发现的补充项（已完成）

- [x] T013 **evals 夹具月界修复**：`_seed_reader` 的"电影票"在每月 1 号落入上个月 → 「本月支出合计 ≈ 80」rubric 在 1 号必然失败（2026-10-01 实测抓到，bot 答 30 正确、rubric 错）；修复为跨月时播在本月 1 号 19:00 per SC-007

## Notes

- `tools.py::QueryParams.limit` 保持 20：兜底路径是降级浏览路径，合计已由 totals 保证正确（见 plan「范围外」）。
- 金额格式不含千分位：与全库既有风格一致（plan D6）。
- 复现数据（修复前）：25 笔/¥1250 → 按 limit=20 的明细之和 ¥800 → 用户被告知 ¥800.00。
