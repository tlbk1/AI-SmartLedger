# Quickstart Validation: default-nickname

## Prerequisites

- 项目 venv：`.venv/Scripts/python.exe`
- 运行测试前**清空 PYTHONPATH**（`PYTHONPATH= ...`，否则误用外部包）

## Setup

```bash
cd E:\360MoveData\Users\www\Desktop\AI-SmartLedger
PYTHONPATH= .venv/Scripts/python.exe tests/test_wechat_mock.py   # 原有 18 用例
```

## Validation Scenarios (from spec.md acceptance)

### V1: 默认昵称生成（User Story 1 / FR-001）
```bash
PYTHONPATH= .venv/Scripts/python.exe -c "
import db
db.init()
# 新建未设昵称用户
uid = db.get_or_create_user('o_new_user')
db.list_ledger_members('o_new_user')  # → 应返回 [{nickname: '账本成员 xxxx', ...}]
# 断言: nickname 非空, 不以 openid 开头, 以'账本成员 '开头
"
```
**预期**：成员 `nickname` = "账本成员 <4位hex>"，非空，非 openid 前缀。

### V2: 同一用户各账本同一默认昵称（FR-004）
```bash
# 同一 openid 在2个账本 → nickname 相同
```

### V3: 自设替换默认（FR-005）
```bash
db.set_nickname('o_new_user', '小王')
db.list_ledger_members('o_new_user')  # → nickname = '小王'
```

### V4: 不显示 openid（FR-002 / User Story 3）
```bash
# list_ledger_members 返回的 dict 中, 不含 'openid' 或 'openid' 前缀
```
**预期**：成员 dict 无 `openid` 字段；展示名只用 nickname。

### V5: 空昵称不生效（FR-006）
```bash
db.set_nickname('o_new_user', '   ')  # 空白昵称
# → 不生效, 保留默认昵称
```

### V6: 账本内唯一（FR-003）
```bash
# 生成默认昵称时, 若与账本内现有重名, 应重生成
```

## Acceptance via pytest

新增测试用例命（`tests/test_wechat_mock.py` 或单独文件）：
- `test_default_nickname_generated`
- `test_same_user_same_default_nickname_across_ledgers`
- `test_set_nickname_replaces_default`
- `test_no_openid_in_member_list`
- `test_blank_nickname_ignored`

运行：`PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q`，须全绿且原有 18 用例不回归。

## Definition of Done

- [ ] 所有 V1-V6 验证通过
- [ ] 原有 18 测试不回归
- [ ] `list_ledger_members` 不返回 openid
- [ ] 默认昵称落库、用户级一个
