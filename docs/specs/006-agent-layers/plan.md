# 实施计划：006-agent-layers

> 本文件批准后保持不可变；任务进度写入 `docs/specs/wu5-retired/state.json`。

## 任务

> **TDD 政策**：本变更为行为保持重构，现有 117 用例即特征测试（python-tdd「老项目重构前先锁定稳定边界」）；无新业务逻辑，无 RED 阶段，全程以「117 保持全绿」为守卫。
> **每个任务的转绿定义**：`.venv/Scripts/python.exe -m pytest -q`（PYTHONPATH 清空）117 passed，方可创建该任务的原子提交；不提交已知红灯。
> **Hardcode 政策**：搬运不新增字面量；`gateway()` 将两处重复的网关解析合一（默认值逐字保持基线，修正归独立变更）。

1. **T1 骨架与平移**（REQ-001/002/005/008）：建 `ai_smartledger/` 包与子包 `__init__.py`；git mv——config.py→core/config.py、prompts.py→core/prompts.py（并入 llm.SYSTEM_PROMPT）、agent.py→agents/react_agent.py、llm.py→agents/fallback_agent.py、graph.py→router.py、wechat.py、main.py、db/→ai_smartledger/db/；新建 core/llm.py（`gateway()`，react_agent/fallback_agent 内部调用，`_get_model`/`_get_client` 留守）；包内导入修正；tests×4、evals/provider.py、scripts/e2e、start_dev.bat、main.py 的 uvicorn 串 import 行更新。
2. **T2 工具拆域**（REQ-003/004）：从 react_agent 抽出 17 个工具闭包 → tools/__init__.py（`make_tools` 组装 + ask_clarify）+ builtin/{transactions,ledgers,members,admin}.py；react_agent 顶层 re-export `make_tools`；拆分前后工具名集合比对（相等且个数为 17）。
3. **T3 notify 解环**（REQ-007）：main._do_push_customer → notify.py；工具闭包内两处延迟导入改为顶层正向导入；main 超时 done-callback 改调 notify。
4. **T4 死代码清理与门禁预检**（REQ-006/010）：删 dead `GraphState`（先确认无引用）；grep 门禁自检（包内 `from main import`/`import graph` 零命中）；全量验证预跑（pytest + e2e + evals + uvicorn 冒烟）。
5. **T5 代码精修**：code-refinement 技能——删多余代码与注释，不改行为，测试保持全绿，产出并入 verification.md。
6. **T6 审查与归档**：review-policy 双层审查（规格符合性 + 代码质量）+ open-code-review 缺陷评审；critical/high 处置后重跑受影响验证；更新 features/、归档、最终规格提交。

## 原子提交边界

| 任务 | 提交 |
|---|---|
| T1 | `refactor(006): 平移模块进 ai_smartledger 包（core/agents/db/router/wechat/main）+ 网关解析集中 gateway()` |
| T2 | `refactor(006): 17 工具闭包按域拆入 tools/builtin，make_tools 组装入口` |
| T3 | `refactor(006): notify.py 承接推送慢路径，解 agent→main 循环依赖` |
| T4 | `refactor(006): 清理 dead GraphState，包内反向依赖清零` |
| T5 | 精修有改动时 `refactor(006): 代码精修（删冗余注释与死代码，行为零变化）`，无改动则并入 T4 提交说明 |
| T6 | `docs(006): 双层审查与缺陷评审记录、features/ 更新与归档` |

里程碑提交（已走门禁）：`docs(spec): 批准 006-agent-layers 的规格与设计`（8b86ede）；本计划批准后创建 `docs(plan): 批准 006-agent-layers 的实施计划`。
