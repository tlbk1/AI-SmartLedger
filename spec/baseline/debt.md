# 基线债务

## 已有 pytest/pyright 失败

- pytest：基线实测 117 passed in 12.12s（2026-10-25，`.venv`，PYTHONPATH 清空），无已知 flaky。
- pyright：未安装（全局与 `.venv` 均无）。wu5 完成标准要求通过——需在首个正式变更中接入，或经用户明确豁免。
- 环境问题：全局 Python311 缺 pygments，`python -m pytest` 无法从全局解释器启动；事实运行环境是 `.venv`（3.11.3）。

## 结构与文档债务

- 结构：根目录平铺 7 模块无包边界；`agent.py`/`llm.py` 职责混装；`graph.py` 名不副实（已无 StateGraph）；agent⤳main 循环依赖（延迟导入 ×2）。
- 工程：无 conftest.py（`iso` fixture 复制 4 份）；无 pyproject/pytest.ini；无 CI；requirements.txt 不含 langchain 系直接依赖。
- 文档：`docs/代码讲解.md` 过时（仍描述 db.py 单文件与旧 classify→record/query 路径）；`docs/viz` 依赖图随重构漂移；无 README。

## 待另建变更的候选（不在重构中顺手修）

1. 网关默认值 szprize→hw 修正（行为变化，单独走 RED-GREEN 变更）
2. requirements.txt 补齐 langchain-openai/langchain-core
3. conftest.py 收拢 `iso` fixture（4 份重复）
4. docs/代码讲解.md 重写（006 后模块路径全变）
5. pyright 接入与豁免清单
