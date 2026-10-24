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
