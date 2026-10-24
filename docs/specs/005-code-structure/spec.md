# Feature Specification: code-structure

**Branch**: `feat/002-shared-ledger` | **Created**: 2026-10-01 | **Status**: Draft

**一句话**：把 1430 行的 `db.py` 按业务域拆成 `db/` 包，prompt 搬出独立文件，删掉遗留的 tools.py——**数据库、页面、回复内容一个字都不变**。

**为什么**：三个月三个 spec 快速叠加，`db.py` 混了 6 个不相干的域（建表/用户/账本/审批/账目/通知）。三轮评审 15 条问题里 11 条的现场都在这个文件——不是代码写得差，是所有东西都挤在一间屋里，改哪都容易碰坏别的。

**验收唯一硬标准**：117 个测试、e2e 16/16、evals 8/8 全绿，测试断言零修改。全绿 = 行为没变 = 重构成功。

## 做什么（3 件事）

### 1. db.py 拆包

```
db.py（1430 行，6 个域挤在一起）
  ↓ 拆成
db/
  __init__.py        # 只留 DB_PATH、_connect()、re-export（原因见下）
  schema.py          # 建表/补列/索引/回填（全部 DDL 只住这里）
  users.py           # 用户、昵称（生成/校验/唯一性）
  ledgers.py         # 账本创建/切换/selector 解析/删除
  members.py         # 申请/审批/移除/退出/权限
  transactions.py    # 记账/明细查询/聚合
  notices.py         # 待补发通知 + 缓存/对话状态/幂等去重
```

两条铁律：

- **调用方零改动**：`db/__init__.py` re-export 全部公开名字，`import db; db.xxx` 照常工作（agent/graph/main/测试/e2e 一行不改）。
- **DB_PATH 和 `_connect()` 必须留在 `__init__.py`**：测试用 `monkeypatch.setattr(db, "DB_PATH", ...)` 换库，`_connect()` 读的是自己模块里的变量——拆散了测试就悄悄失效（假绿）。

不做的事：不上 ORM、不拆服务、不设"每文件 ≤N 行"之类的数字指标。

### 2. schema 归位（搬家时顺手，不单独立项）

建表代码从 `init()` 搬进 `schema.py`，**内容原样**：仍是"最终形态 + 幂等"（`CREATE TABLE IF NOT EXISTS` + 补列），**不引入版本号/迁移框架**（曾讨论过，砍掉——库里那两段一次性回填本来就幂等，加一行注释说明即可）。

### 3. prompt 独立 + tools.py 退役

- 57 行 `AGENT_SYSTEM_PROMPT` 从 agent.py 搬到 `prompts.py`（占位符 `{now}` 接口不变）——它是被评审改得最频繁的文件，值得单独住。
- `tools.py` 删除：只剩 `QueryParams`/`TOOL_SCHEMA` 两个名字被 llm.py 用，并进去即可。全仓 grep 无残留。

## 不做什么

测试重组（纯搬文件零收益，下次顺手）、ORM、服务拆分、目录大迁移。

## 验收

| 验证 | 标准 |
|---|---|
| `pytest tests/ -q` | 117 用例全绿，断言零修改 |
| `scripts/e2e_shared_ledger.py` | 16/16 |
| evals（真实 LLM） | 8/8 |
| `grep "CREATE TABLE\|ALTER TABLE\|ADD COLUMN"` | 只在 schema.py 命中 |
| `grep "from tools\|import tools"` | 零命中 |
| 连跑两次 `init()` | 第二次无副作用（幂等） |
