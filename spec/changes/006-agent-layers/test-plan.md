# 测试计划：006-agent-layers

## 需求—测试映射

> 行为保持重构：「预期 RED」不适用——特征测试（现有 117 例）在搬运前已全绿，搬运后必须保持全绿；无新业务实现，故无 RED-GREEN 循环，仅 GREEN 守卫。

| 需求 ID | 测试文件/用例 | 层级 | 预期 RED | 验收结果 |
|---|---|---|---|---|
| REQ-001 包结构 | 全套 117 例（import 即验证）+ 目标树核对 | structure | 不适用 | pytest 117 passed；树形与 REQ-001 一致 |
| REQ-002 core 层 | test_query_accuracy（兜底链提取/摘要走 gateway）；test_identity_selection | unit | 不适用 | 117 passed |
| REQ-003 agents 层 | test_identity_selection（make_tools/_pending_joins_hint/prompt 文案断言）、test_shared_ledger | unit | 不适用 | 117 passed（patch 目标全部生效） |
| REQ-004 工具集合守恒 | 拆分前后 make_tools 工具名集合比对 + 17 工具功能用例（分散于四个测试文件） | contract | 不适用 | 集合相等，个数 17 |
| REQ-005 db 原样迁入 | iso fixture 全套（DB_PATH monkeypatch 生效性即隐式回归） | integration | 不适用 | 117 passed，无静默假绿（备份库比对） |
| REQ-006 router | test_wechat_mock（编排/幂等/欢迎语）、test_query_accuracy（哨兵→兜底查询口径） | integration | 不适用 | 117 passed |
| REQ-007 notify 解环 | test_identity_selection（send_customer_message 替身、enqueue_undelivered/drain 用例）+ grep 门禁 | integration/static | 不适用 | 117 passed + grep 零命中 |
| REQ-008 入口与脚本 | uvicorn ai_smartledger.main:app 起服务 GET /health | smoke | 不适用 | HTTP 200 |
| REQ-009 契约总则 | git diff 审查 tests/ 四文件 | static review | 不适用 | diff 仅 import 行，断言零改动 |
| REQ-010 旧符号退役 | scripts/e2e_shared_ledger.py 16 项（用户旅程全链路） | e2e | 不适用 | 16/16 |

## 验证命令

- `.venv/Scripts/python.exe -m pytest -q`（PYTHONPATH 清空）——必需，每任务转绿条件
- `python scripts/e2e_shared_ledger.py`——必需，T4/T6
- promptfoo evals（evals/，8 场景，独立评测库）——必需，T4/T6
- `pyright`——**待决**：未安装。维护者「批准依赖」→ 装入 `.venv` 并配最小配置后作为必需门禁；维护者「豁免」→ 在 verification.md 记录豁免结论并在 workflow.json 停用该项
- ruff：项目未配置，不启用
- 改动前备份 `db/ledger.db`（约定，T1 开始前执行）
