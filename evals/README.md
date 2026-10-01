# evals — promptfoo 回复评测

用 [promptfoo](https://promptfoo.dev) 对机器人做端到端回归评测：每条用例把一句话发给
项目真实入口 `graph.process_message()`，对最终微信回复做断言（字符串规则 + LLM 评分）。

被测 agent 与 LLM 评分走**同一套配置**（来自 `.env`）：网关 `https://ccc.hwprize.com/v1`、
模型 `deepseek-flash`（hw 网关，同时支持 OpenAI 与 Anthropic 两种协议）。

## 文件

| 文件 | 作用 |
| --- | --- |
| `provider.py` | promptfoo 自定义 Python Provider：转发用例给 `graph.process_message()` |
| `promptfooconfig.yaml` | 评测配置：8 个典型用例（记账/反问/查账/建账本/闲聊）+ 断言 |

隔离机制：运行期把 `db.DB_PATH` 重定向到 `evals/.eval_ledger.db`（自动建库），生产
`ledger.db` 不受影响；每条用例用独立 openid（`vars.openid`）拿到独立默认账本，用例之间
互不污染。`eval-reader` 这个 openid 首次使用时自动播种三条流水（午餐 30 / 电影 50 /
工资 5000），供查询类断言比对。

## 运行

前置：Node ≥ 18（`npx promptfoo`）；LLM 调用（被测 agent + 评分）走 `.env` 里的
hw 网关，需先把 key 导出给 promptfoo 的评分模型。

Git Bash：

```bash
# 1. 把 .env 里的 hw 网关 key 导出给 promptfoo 评分模型用（不打印到屏幕）
export LLM_API_KEY=$(grep '^LLM_API_KEY=' .env | cut -d= -f2-)

# 2. 用项目 venv 的 Python 跑评测（依赖 openai/langgraph 装在 venv 里）。
#    网关/模型全部来自 .env（hw 网关 / deepseek-flash），无需额外覆盖。
#    --max-concurrency 1：顶层 concurrency 键无效，必须用 CLI 参数串行执行。
cd evals
PATH="../.venv/Scripts:$PATH" \
  npx -y promptfoo@latest eval -c promptfooconfig.yaml --max-concurrency 1 --no-cache
```

结果直接打印在终端；想看网页版明细：

```bash
npx -y promptfoo@latest view
```

## 断言写法速查

- 确定性断言（快、免费）：`contains` / `icontains` / `regex` / `not-icontains` /
  `not-regex` / `equals` / `is-json` / `javascript`
- 语义断言：`llm-rubric`——用一句中文描述"回复应该怎样"，由 yaml 里 `defaultTest`
  指定的打分模型判定。改动 prompt 或模型后跑一遍，回复跑偏即失败。

新增用例：往 `promptfooconfig.yaml` 的 `tests:` 里加一项，给一个全新的 openid
（想带历史数据就复用 `eval-reader`，或照抄 `_seed_reader` 播种）。

## CI 提示

评测会真实调用 hw 网关 LLM（每条用例若干次），并写入 `evals/.eval_ledger.db`。
两者都已进 `.gitignore`；在 CI 里跑需要把 `LLM_API_KEY` 放进仓库密钥并缓存 npx。
