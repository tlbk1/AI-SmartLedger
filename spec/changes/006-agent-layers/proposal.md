# 变更提案：006-agent-layers

## 问题与背景

根目录平铺 7 个模块无包边界；`agent.py`（495 行）职责混装——ReAct 编排、17 个工具闭包（约 330 行）、模型构造挤在一个文件；`llm.py`（461 行）兜底链同时混装数据类、TOOL_SCHEMA 与提示词；`graph.py` 已无 StateGraph，名不副实；存在全库唯一循环依赖边 agent⤳main（agent.py:189/302 两处延迟导入 `from main import _do_push_customer`）。维护者希望参考 hello-agents 的分层树（core/agents/tools）获得一眼可读的结构。

基线证据见 `spec/baseline/*`（117 tests passed in 12.12s，2026-10-25）。方向性决定（包一层 ai_smartledger、解循环依赖）已经维护者在计划评审中拍板；网关默认值修正已按「不把行为修正顺手修进重构」拆为独立变更（见 `spec/baseline/debt.md` 候选①）。

## 目标

1. 建立 `ai_smartledger/` 包，按 core（框架层）/ agents（实现层）/ tools（工具层）分层，`db/` 整体原样迁入。
2. 解除 agent⤳main 循环依赖：`main._do_push_customer` 迁至 `notify.py`，工具闭包改为顶层正向导入。
3. 网关配置唯一来源 `core/llm.py` 的 `gateway()`，消除两处重复的 base_url/model 解析；`_get_model`/`_get_client` 留守原模块（monkeypatch 契约）。
4. 行为零变化：117 用例断言零改动（仅 import 行调整），e2e 16/16、evals 8/8 保持。

## 非目标

- 不引入参考图中的框架机器：Agent 基类、tools/base、registry、chain、async_executor、exceptions 体系。
- 不重组测试文件（保持平铺与功能命名）；不新建 conftest.py（另列债务候选③）。
- 不集中 `WECHAT_*` 环境读取；不动 `db/` 内部实现。
- 网关默认值 szprize→hw 修正是行为变化，不在本变更内（独立变更，RED-GREEN）。
- 不重写 docs/ 三份旧文档（代码讲解重写另列候选④；新结构的权威记录在本 spec/ 内）。

## 影响范围

- 全部源码模块位移（git mv 保历史）：main/graph/agent/llm/prompts/config/wechat + db/。
- import 行变更：tests×4、evals/provider.py、scripts/e2e_shared_ledger.py、start_dev.bat、main.py 的 uvicorn 字符串。
- docs/viz/gen_data.py 的模块清单可能漂移（生成物，非权威，低风险）。
- 不涉及数据库结构、不涉及依赖变更、不涉及对外协议。

## 风险

1. monkeypatch 目标搬家 → 静默假绿（005 教训）。对策：patch 目标逐条留守或 re-export（见 design 契约清单），收尾逐条核验。
2. 17 工具闭包拆分错域/漏迁。对策：逐闭包阅读归组，拆分前后工具名集合必须一致。
3. `from main import` 残留或新循环边。对策：grep 门禁（包内禁 `from main import` / `import graph`）。
4. pyright 未安装（wu5 必需门禁）——需维护者批准依赖接入或明确豁免（见待决问题）。

## 验收标准

1. `.venv` + PYTHONPATH 清空：`pytest -q` 117 passed（测试文件 diff 仅 import 行，断言零改动）。
2. `python scripts/e2e_shared_ledger.py` 16/16。
3. promptfoo evals 8/8。
4. grep：`ai_smartledger/` 内无 `from main import`、无 `import graph`。
5. `uvicorn ai_smartledger.main:app` 起服务，GET /health 返回 200。
6. 拆分前后工具名集合一致（17 个）。

## 待决问题

- pyright：接入（需「批准依赖」，安装到 `.venv`）或豁免——影响 verify 门禁能否通过。倾向接入（一次性配置，长期受益），由维护者定。
