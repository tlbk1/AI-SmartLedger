# 接管计划

1. 审计系统结构、行为、测试与风险基线。
2. 用户明确说“批准基线”。
3. 以后重构具体模块时，先建立该模块稳定边界的特征测试。
4. 技术债与 Bug 分别建立正式变更，不在接管阶段顺手修改。

## 项目清单

- `requirements.txt`

## 本项目特化

- 稳定边界：`db/` 域公开函数、`graph.process_message`、`main` 的 `/wechat`、`wechat` 适配器函数——已有 117 个功能级用例 + e2e 16 项 + evals 8 场景覆盖，直接作为特征测试使用。
- 首个变更：006 结构重构（根目录平铺 → `ai_smartledger/{core,agents,tools,db}` 包分层，解 agent⤳main 循环依赖）。方向已在 ZCode 计划模式获批（包一层 ai_smartledger）；原「顺手修两项」中的网关默认值修正按本流程第 4 条改为独立变更（见 debt 候选①），notify.py 提取属分层本体、保留在 006 内。
- 环境约定：`.venv/Scripts/python.exe -m pytest`（PYTHONPATH 清空）；改动前备份 `db/ledger.db`；提交署名 www + noreply 邮箱；push/PR 走 api.github.com 且必须单独授权。
- 分支策略：基线批准后从 `feat/005-code-structure` 切 `feat/006-agent-layers`。
- Spec Kit 退役（已拍板 2026-10-25）：`specs/001~005` git mv 至 `docs/specs/` 留档；`.specify/` 脚手架 git rm 删除；执行时同步更新 system-map 清单。此归置与 wu5 初始化、基线文档合并为接管首个提交（005 合并前旧 PR 描述里的 specs/ 链接会随迁失效，属预期）。
