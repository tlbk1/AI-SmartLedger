# 验证记录：006-agent-layers

## TDD 证据

<!-- 记录 RED 原因、GREEN 结果和重构后验证。 -->

## 自动验证

<!-- `verify` 命令在此追加摘要。 -->

## 审查

- [ ] 规格符合性审查
- [ ] 代码质量审查
- [ ] 未引入不当 hardcode
- [ ] 独立复审（不适用时写明原因）

## 偏差、风险与遗留债务

### 自动验证 2026-10-25T01:46:35+08:00

- 范围：`full`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过

### 自动验证 2026-10-25T01:53:50+08:00

- 范围：`full`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过

### 自动验证 2026-10-25T01:57:44+08:00

- 范围：`full`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过

### 自动验证 2026-10-25T01:58:52+08:00

- 范围：`full`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过

### 自动验证 2026-10-25T02:09:04+08:00

- 范围：`full`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过

### 自动验证 2026-10-25T02:11:35+08:00

- 范围：`full`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过

## 代码精修记录（T5，2026-10-25）

- 机械层：ruff/refurb/vulture 未安装（依赖批准仅含 pyright）→ 降级人工等效扫描：死代码 grep 核查 5 处（fallback_agent 的 time/datetime/SHANGHAI/ZoneInfo、react_agent._model_name），全部为基线遗留死代码，应用删除；ruff/vulture 接入列入待人工（需「批准依赖」）。
- 语义层：ponytail-review 降级人工 1 轮（diff 主体为逐字搬运，新代码面约 90 行）：yagni 0 条应用（GatewayConfig/resolve_admin_ledger 均多调用方；core.llm 的 config 懒加载为 dotenv 时序约束，不得上提）；delete 5 条应用；shrink 0 条。
- 注释：删 1 条（main.py 迁移指引注释，属叙事）；改 3 处过时引用（router 文档串/注释中 agent.py → react_agent；fallback_agent 头文档「四个函数」→「五个」、中转站域名 → 指向 core/llm.py）。工具闭包内局部 import（json/threading）按原样保留（逐字搬运纪律）。
- 净变化：react_agent.py 495→135 行；新增 tools 层 7 文件；其余为位移。
- 精修后回归：全量 117 passed（无撤销）。
- 待人工：ruff/vulture 接入（需依赖批准）；evals 8 场景本会话 shell 无法执行（python 出站被环境阻断 WinError 10013，curl 可达网关证明网络与配置正常），需维护者在本机终端按 evals/README 执行确认 8/8。
- 已回滚：无。转正式审查：无。

### 自动验证 2026-10-25T02:11:56+08:00

- 范围：`full`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过

## 审查记录（T6，2026-10-25）

### 规格符合性审查（第一轮）

- [x] REQ-001 包结构：ls 实证 ai_smartledger/{core,agents,tools/builtin,db} + main/notify/router/wechat；根目录平铺模块全部退役（git mv）。
- [x] REQ-002 core 层：grep 实证 LLM_BASE_URL/LLM_API_KEY 解析仅存 core/llm.py（另一命中为注释）；prompts.py 承载两个常量。
- [x] REQ-003 agents 层：run_agent/_pending_joins_hint/AGENT_FAILURE/_get_model 留守 react_agent；AGENT_SYSTEM_PROMPT/make_tools 顶层 re-export（117 例中 patch 目标全部生效即证）；_get_client 留守 fallback_agent。
- [x] REQ-004 工具集合守恒：拆分前后名称集合比对脚本通过，17 个（归组与计划一致：2+3+7+4+ask_clarify）。
- [x] REQ-005 db 原样：git diff --find-renames --stat 显示 7 文件 0 增删（纯 rename）；iso fixture 契约经 117 例实证。
- [x] REQ-006 router：process_message/_dispatch_fallback/_classify_and_route 逐字保持（独立复审 AST 对比确认）；GraphState 删除（无引用）。
- [x] REQ-007 notify 解环：grep「from main import|import graph」包内零命中；两处闭包改 notify.push_customer 顶层正向依赖；main 超时回调改调 notify。
- [x] REQ-008 入口：start_dev.bat 与 main.py uvicorn 串更新；uvicorn ai_smartledger.main:app 冒烟 /health → {"status":"ok"}。
- [x] REQ-009 契约总则：git diff a6e91f2..HEAD -- tests/ 的非 import 行为零命中（断言零改动机械实证）；WECHAT_* 读取未集中（wechat.py 未动）；无新增 hardcode（见下）。
- [x] REQ-010 旧符号退役：根目录 import agent/import graph 实证 ImportError；e2e 16/16。
- [x] 非目标核查：未引入基类/注册表/异步执行器/exceptions 体系；测试未重组；网关默认值未改（仍 szprize，修正归独立变更）；db 内部零改动。
- [x] test-plan 映射：REQ-001~010 均有对应测试与验收结果。

### 代码质量审查（第二轮）

- [x] 正确性：独立复审对 17 工具、run_agent、兜底五函数、main/graph 做 AST 级新旧对比——LLM 可见 docstring、回复文案、钳制逻辑、错误消息逐字节一致；仅三处已文档化例外（resolve_admin_ledger 签名显式化、notify 正向调用、工具列表按域排序）。
- [x] 并发：notify 守护线程块逐字迁移，target=None 守卫、异常入队路径不变；notify.push_customer 为 main._do_push_customer 的逐字上提（catch-all 在内层，外层兜底覆盖不变）。
- [x] 导入契约：无环；懒导入保持（langchain_openai/langgraph.prebuilt/config-in-function/router 兜底导入）。
- [x] 安全：无密钥/敏感值入日志或仓库；.env 未触碰；*.db 不入库。
- [x] hardcode 核查：新增字面量仅 gateway() 的旧网关默认值（两处重复合一处，属既有债务搬运，修正归独立变更）；无新增魔法数字/路径/URL。
- [x] 回归风险：全量 117 passed；pyright（off 模式）0 错误。

### 独立复审（第三镜头，独立子代理，未泄漏先前结论）

- 结论：critical 0 / high 1 / low 2。对逐字搬运保真度、notify 线程块、导入图、工具组装、main 语义、gateway 等价性、密钥/路径做系统核查。
- [high→遗留债务 D1] 活库路径随包迁移：DB_PATH=Path(__file__).parent/"ledger.db" 使活库从 <root>/db/ledger.db 变为 <root>/ai_smartledger/db/ledger.db，变更未编码数据迁移；本机为单机部署，活库已随包迁移到位（git mv 携带未跟踪 .db），风险限于「存在旧路径活库的其他部署」——当前不存在。按 wu5 约束（数据迁移不在 skill 范围）不在本变更加自动迁移；design.md「无数据迁移」表述以本条为准修正（design.md 已批准哈希锁定，不改原文）。遗留处置：若将来多机部署，另建变更做 DB_PATH 环境变量覆盖 + 迁移脚本。
- [low F2，待维护者定夺] router._classify_and_route 内 `import ai_smartledger.agents.fallback_agent as llm` 是包内唯一绝对自导入，建议改 `from .agents import fallback_agent as llm`（等价、保持懒加载）。按评审授权规则未自动修改。
- [low F3，已处置] evals/_t4_out.json（本会话验证的临时产物）被 T4 的 git add -A 误带入库——已 git rm --cached + gitignore 补 evals/_*_out.json。
- [ ] 缺陷评审（open-code-review）：本会话 python 出站网络被阻断（WinError 10013），ocr 无法在本会话执行——待维护者在本机终端运行后补记录。

### 待维护者执行（收尾前置）

1. 本机终端跑 evals：cd evals 后按 README 导出 key 并 `npx -y promptfoo@latest eval -c promptfooconfig.yaml --max-concurrency 1 --no-cache`，确认 8/8（本会话已排除代码侧回归：117 + e2e 16/16 + 冒烟 + 独立复审全绿）。
2. 本机终端跑 ocr 缺陷评审（full 模式必跑项），结果回填本文件。
3. 对 F2 的取舍（一行修改或忽略）。
4. 归档与 push 需明确授权（push 走 api.github.com）。

### 自动验证 2026-10-25T02:23:35+08:00

- 范围：`affected`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过

## evals 与 ocr 评审记录（T6 补全，2026-10-25）

### evals（promptfoo，8 场景，hw 网关真实 LLM）

- [x] 三轮实测：7/8 → **8/8** → **8/8**（--no-cache）。首轮单例失败复跑即过，定性为评分模型侧波动（deepseek-flash 输出非确定性），非代码回归；按 flaky 纪律三轮取二全绿记录。
- 环境备注：本会话 python 出站一度被 WinError 10013 阻断，维护者解除后本会话直接完成，无需再人工执行。
- [x] evals 8/8 达成。

### 缺陷评审（open-code-review v1.13.3，外部模型 deepseek-flash，范围 a6e91f2..HEAD 代码文件）

- 会话：65a63a0b-9258-4352-aa1f-9f0034979e94（3 跑：首发 27→续跑 27→补跑同 27/30 条目；tools/__init__ 组过滤请求网关 400 三次，该组由独立子代理复审覆盖——17/17 组装、ask_clarify、无名称冲突、admin/ledgers 搬运 AST 级保真已核）。用量约 1.3M tokens（hw 网关）。
- 分级：critical 0 / high 2 / medium 2 / low 5。
- 采纳：无（未获「评审并修复」授权，且 high/medium 均为基线既有行为）。
- 驳回/豁免（引规格或测试）：
  - [high] transactions 工具对 LLM 畸形入参不校验（非 dict/缺 amount/空列表「已记 0 笔」）——**书面豁免**：基线逐字搬运（git show a6e91f2:agent.py 同码），修复属行为变化，超出本变更「行为零变化」规格；失败路径由哨兵→兜底链兜住（router._dispatch_fallback），不崩溃不写脏账（003 方案B 已收敛写路径）。→ 转 fix 变更候选 F-a（ocr 已给建议代码）。
  - [high] fallback_agent 删除死代码疑留引用——**驳回（误报）**：删除前 grep 核查 SHANGHAI/time/datetime/ZoneInfo 零使用，删除后 117 + e2e 16/16 + evals 8/8 + import 冒烟全绿，引用不存在。
  - [medium] common.resolve_admin_ledger 对无账本用户返回 (None, None) 透传 db 层——**书面豁免**：基线 _resolve_admin_ledger 同码（agent.py:250-256）；同上转 fix 候选 F-b。
  - [medium] react_agent 回复提取仅滤 tool 消息，可能回退取到历史消息——**书面豁免**：基线同码（agent.py:480-485），兜底语义未变；转 fix 候选 F-c。
  - [low] router 绝对自导入——与独立复审 F2 收敛（两个独立镜头同报），**待维护者定夺**（一行等价修改，需授权）。
  - [low] core/llm gateway 与 config 对 LLM_MODEL 双重解析——记录：与基线表达式逐字等价，可随 F2 一批收敛（model=DEFAULT_MODEL）。
  - [low] 闭包内局部 import threading/json、__import__("datetime") 内联——**驳回**：基线同构，逐字搬运纪律优先。
  - [low] ledgers show 参数必填（模型漏传则工具失败）与 name.strip() 文案——**驳回**：基线设计（AGENT_SYSTEM_PROMPT 明确指导「show 填 'y'」；文案行为基线一致）。
- [x] 缺陷评审（open-code-review，critical/high 已处置：0 critical；2 high 豁免或驳回，理由如上）
- 转正式审查/另建变更：F-a（入参校验）、F-b（admin lid=None 判空）、F-c（回复提取边界）+ 既有债务候选①（网关默认值）。

### 收尾清单更新

- [x] evals 8/8（本会话完成）
- [x] ocr 缺陷评审（本会话完成，记录如上）
- [ ] F2（router 相对导入）及 fix 候选批次取舍——待维护者
- [ ] 归档 + push/PR——待维护者明确授权（push 走 api.github.com）

### 自动验证 2026-10-25T03:09:55+08:00

- 范围：`affected`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过

## low 级收敛修复（2026-10-25，维护者授权「修」）

- 范围：仅两处等价修改（ocr low + 独立复审 F2 双镜头收敛项）；F-a/F-b/F-c 行为类候选仍留独立 fix 变更。
- [F2] ai_smartledger/router.py `_classify_and_route`：`import ai_smartledger.agents.fallback_agent as llm` → `from .agents import fallback_agent as llm`（仍为函数内懒加载，包内导入风格统一，消除包名硬编码）。
- [gateway 双重解析] ai_smartledger/core/llm.py：`model=os.environ.get("LLM_MODEL", DEFAULT_MODEL)` → `model=DEFAULT_MODEL`；模块文档串同步修正（「唯一来源」范围写明 base_url/api_key，模型名统一取 config.DEFAULT_MODEL）。等价性前提已核验：tests/evals/scripts 全库无 LLM_MODEL 运行期改写（grep 实证），config 仅在 gateway() 内懒导入，DEFAULT_MODEL 自身即 `os.environ.get("LLM_MODEL", "deepseek-v4-flash")`。
- 前置核验：grep `LLM_MODEL|setenv|monkeypatch.setattr(os` 于 tests/evals/scripts/ai_smartledger → 仅 config.py 定义处与注释命中，无测试改写。
- 回归：全量 117 passed；e2e 16/16；gateway() 冒烟输出 base_url=ccc.hwprize.com/v1、model=deepseek-flash（.env 生效，证明 DEFAULT_MODEL 的 LLM_MODEL 优先链未变）；`import ai_smartledger.main` 冒烟通过。
- 无新增 literals；无行为变化（等价替换）。

### 自动验证 2026-10-29T21:34:52+08:00

- 范围：`affected`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过

## 审查结论汇总（review-policy 规定格式）

- [x] 规格符合性审查（详见上文「规格符合性审查（第一轮）」，REQ-001~010 逐条对照，非目标与范围零扩大）
- [x] 代码质量审查（详见上文「代码质量审查（第二轮）」，正确性/并发/导入/安全/hardcode 逐项）
- [x] 未引入不当 hardcode（新增字面量仅基线旧网关默认值搬运，已记录为既有债务；修正归独立变更）
- [x] 独立复审（独立子代理，AST 级新旧对比；critical 0 / high 1=活库路径已记债务 D1 / low 2，其中 F2 已修于 6a2a19c）

### 自动验证 2026-10-29T21:35:52+08:00

- 范围：`affected`
- `pytest`：exit `0`，通过
- `pyright`：exit `0`，通过
