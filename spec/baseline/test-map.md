# 测试基线

## 现有测试

- `tests/test_identity_selection.py`（42 例）：身份/选账本/权限等。
- `tests/test_shared_ledger.py`（38 例）：共账本邀请、审批、成员管理。
- `tests/test_wechat_mock.py`（23 例）：微信协议（验签/XML/回复构造/未送达补投）。
- `tests/test_query_accuracy.py`（14 例）：查询口径（SQL 聚合、收支分列、top-5）。
- 合计 117 例。基线实测：**117 passed in 12.12s**（2026-10-25，`.venv`，PYTHONPATH 清空）。
- 组织方式：按功能而非模块平铺；无 conftest.py，`iso` 隔离库 fixture（tempdir + monkeypatch `db.DB_PATH` + `db.init()`）复制 4 份。
- 补充防线：`scripts/e2e_shared_ledger.py`（16 项用户旅程检查，临时库）；`evals/`（promptfoo 8 场景回复质量，独立评测库）。

## 测试缺口

按稳定边界观察（接管期不补测试；重构以现有 117 例为特征测试）：

- `db/` 各域函数无直接单测，仅经功能测试间接覆盖。
- `main.py` 的 4.5s 超时调度（`asyncio.wait_for` + done-callback）无直接断言；推送失败→`enqueue_undelivered` 入队侧有测试，超时触发侧没有。
- `AGENT_FAILURE` 哨兵路径（主链异常→兜底链）在 tests/ 中零出现，无直接用例。
- 断言多处锁内部符号与 prompt 文案（如 `agent.AGENT_SYSTEM_PROMPT` 内容断言），对重构敏感。
