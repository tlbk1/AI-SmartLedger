# Feature Specification: default-nickname

**Feature Branch**: `feat/001-default-nickname`

**Created**: 2026-09-04

**Status**: Draft

**Input**: User description: "给未设置昵称的用户自动生成默认昵称（账本成员+随机后缀），并去掉成员列表里 fallback 显示 openid 的兜底"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 默认昵称自动生成 (Priority: P1)

用户加入账本/首次交互，但从未主动设置昵称。系统应自动为其生成一个默认昵称，让成员列表/按昵称定位时有一个稳定的展示名。

**Why this priority**: 这是功能核心——没有默认昵称，成员在账本里就无法被稳定识别，按昵称移除/定位会失败。

**Independent Test**: 新建一个无昵称用户，成员列表显示它时，应出现"账本成员+随机后缀"形式的昵称，而不是 openid 或空。

**Acceptance Scenarios**:

1. **Given** 一个从未设置昵称的用户加入账本，**When** 管理员调用成员列表，**Then** 该成员显示为「账本成员 <随机后缀>」（如"账本成员 a3f9"），而非空或 openid 前缀
2. **Given** 同一用户，**When** 在多个账本里查看，**Then** 显示的是**同一个**默认昵称（用户级一个，非每账本各一个）

---

### User Story 2 - 主动设置后替换默认昵称 (Priority: P2)

用户设置了自己的昵称后，默认昵称被替换，列表里显示用户设置的昵称。

**Why this priority**: 默认昵称是兜底，用户主动设置才是理想态。用户设置后应立即用新昵称。

**Independent Test**: 一个有默认昵称的用户调用设置昵称，成员列表应显示新昵称。

**Acceptance Scenarios**:

1. **Given** 用户当前是默认昵称"账本成员 a3f9"，**When** 用户调用 `set_nickname("小王")`，**Then** 成员列表显示"小王"，不再显示默认昵称

---

### User Story 3 - 不再显示 openid (Priority: P1)

任何展示成员的地方，绝不允许 fallback 显示 openid（含前缀/截断）。这是安全纪律（constitution 原则 I）。

**Why this priority**: openid 是敏感身份，暴露会泄露用户身份。这是合规底线。

**Independent Test**: 写一个成员列表，确认输出里不含任何 openid 片段。

**Acceptance Scenarios**:

1. **Given** 任何成员（哪怕从未设置昵称），**When** 调用成员列表，**Then** 输出中不含 openid 或 openid 前缀（如 `m["openid"][:8]`）
2. **Given** 一个从未设置昵称的用户，**When** 显示在成员列表，**Then** 显示默认昵称而非 openid 前缀

---

### Edge Cases

- 默认昵称随机后缀撞概率极低，但若与账本内现有昵称重名（原则 II 唯一性），应拒绝该默认昵称并重生成。
- 用户设置了空的昵称（如空字符串）——应视为未设置，回退到默认昵称（或拒绝空昵称）。
- 默认昵称在改名后不追溯——只影响当前展示。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 为从未设置昵称的用户生成一个默认昵称（格式「账本成员」+ 随机后缀），并**存入** users.nickname（而非仅展示时临时拼）。
- **FR-002**: 系统 MUST 在展示任何成员时使用 nickname（默认或自设），**绝不 fallback 显示 openid**（含截断前缀）。
- **FR-003**: 系统 MUST 保证默认昵称在**同一账本内唯一**（原则 II）；若冲突则重生成。
- **FR-004**: 默认昵称 MUST 是**用户级**的——同一用户在各账本显示同一默认昵称。
- **FR-005**: 用户主动 `set_nickname` 后，MUST 用新昵称替换默认昵称，立即生效。
- **FR-006**: 空昵称或纯空白昵称 MUST 不生效（视为未设置，保留默认昵称）。

### Key Entities

- **User**: 有 openid（内部身份，永不展示）和 nickname（对外展示名，含默认或自设）。
- **Ledger**: 账本内成员识别用 nickname；唯一性约束在账本内生效。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 从未设置昵称的用户，在成员列表里 100% 显示「账本成员+后缀」形式，永不显示 openid。
- **SC-002**: 任一用户的成员列表输出中，openid 及 openid 前缀出现次数为 0。
- **SC-003**: 用户设置昵称后，成员列表立即切换为自设昵称（切换延迟 < 1 次交互）。
- **SC-004**: 同一用户在不同账本显示同一个默认昵称。

## Assumptions

- 项目沿用现有 `users.nickname` 字段（不新增表），默认昵称落库到该字段。
- 默认昵称的随机后缀用足够随机的字符（如 4 位 hex），降低撞名概率。
- 旧数据（已有用户但 nickname 为空）：视为"从未设置"，第一次展示时按需生成并落库。
- 不重构历史账目归属；默认昵称只影响当前展示与识别。
