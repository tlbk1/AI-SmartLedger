# Feature Specification: shared-ledger

**Feature Branch**: `feat/002-shared-ledger`

**Created**: 2026-09-04

**Status**: Draft

**Input**: User description: "不同成员共用账本：两级权限（owner/member）、审批制加入（口令申请→owner同意）、账目共享可见（显示谁记的）、昵称展示、默认账本+多账本、成员变更（移除/退出保留历史账目）、口令重置、删除账本后成员处理、管理操作明确指定账本"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 申请加入账本（审批制） (Priority: P1)

一个用户拿到邀请口令，发起加入申请。系统**不立即**让他加入，而是记录为待审批（pending），并告知申请人"已提交，等待管理员同意"。

**Why this priority**: 审批制是共账的准入口（constitution VII）——没有它，任何人都能凭口令进入账本，权限控制失效。

**Independent Test**: 用户 A 发口令申请 → 查其状态为"待审批"（不是 member），且查不到账本任何数据。

**Acceptance Scenarios**:

1. **Given** 用户 A 不是账本成员，**When** A 提交有效口令申请加入，**Then** A 进入"待审批"（pending）状态，**且 A 看不到该账本任何账目**
2. **Given** A 提交无效口令，**When** 系统处理，**Then** 提示口令不存在，不产生申请
3. **Given** A 已在待审批中，**When** A 重复提交同一账本申请，**Then** 不产生重复申请（幂等）

---

### User Story 2 - owner 同意加入申请 (Priority: P1)

账本 owner 能查看到待审批申请并**同意**。同意后申请人正式成为 member（可访问账本）。

**Why this priority**: 与 US1 配对——只有 owner 能决定谁加入（constitution VI/VII）。

**Independent Test**: owner 同意申请 → 申请人成为 member 并能记账。

**审批流程（明确）**:
1. 申请人提交申请 → 存为待审批（pending）
2. 系统**尽力**推送通知 owner（48h 内可推则推，推不了则静默跳过）
3. owner 任何一次对话 → bot 检查待审批 → 顺带提示"有 N 条待审批"（**兜底保底**）
4. owner 说"同意 xxx" → 完成，申请人成为 member

**Acceptance Scenarios**:

1. **Given** owner 有待审批申请，**When** owner 同意，**Then** 申请人成为该账本 member，可记账/查账
2. **Given** 非 owner（member）尝试审批，**When** 其调用审批操作，**Then** 被拒绝（只有 owner 能审批）
3. **Given** owner 有多个待审批，**When** owner 查询待审批列表，**Then** 看到全部待审批申请（含申请人昵称）
4. **Given** owner 超过 48h 未互动导致推送失败，**When** owner 下次发消息，**Then** bot 提示待审批数量，审批不卡死（兜底生效）

> **范围说明（YAGNI）**：本次**只做"同意"**，"拒绝申请"暂不实现（后续按需增强；`rejected` 为状态预留值）。

---

### User Story 3 - 两级权限边界 (Priority: P1)

owner 与 member 权限分明：owner 可管理（审批/移除/改名/删账本/重置口令），member 只能记账、查账、查看成员列表。

**Why this priority**: 分权是共账的安全核心（constitution VI）。权限判定必须基于**目标账本内该成员的角色**。

**Independent Test**: member 调用任意管理操作 → 全部被拒；owner 调用 → 成功。

**Acceptance Scenarios**:

1. **Given** 用户是某账本 member，**When** 调用移除成员/改名/删账本/审批/重置口令，**Then** 全部被拒绝
2. **Given** 用户是某账本 owner，**When** 调用上述管理操作，**Then** 成功
3. **Given** 用户 X 在账本 L1 是 owner、在 L2 是 member，**When** X 对 L2 调用管理操作，**Then** 被拒绝（权限按目标账本判定，不因 X 是别的账本 owner 而放行）

---

### User Story 4 - 成员移除（保留历史账目） (Priority: P1)

owner 移除成员后，该成员立即无法访问账本，但**其历史账目保留在账本内**，不被删除、归属不变。

**Why this priority**: constitution 2.2——移除≠删数据，保护账本数据完整性。

**Independent Test**: 成员记账 → 被移除 → 账目仍在账本、可被 owner 查到。

**Acceptance Scenarios**:

1. **Given** member 在账本记过账，**When** owner 移除该 member，**Then** 该 member 不再能访问账本，但**其账目仍在账本内可见**
2. **Given** member 被移除后，**When** 该 member 尝试记账/查账该账本，**Then** 被拒绝/不可见
3. **Given** owner 移除成员，**When** 按昵称指定目标，**Then** 准确移除该成员（不误伤账本外同名用户）

---

### User Story 5 - 账目共享可见 + 显示记账人 (Priority: P1)

账本内**所有成员都能看到该账本的全部账目**（含其他成员记的），且每笔账能显示**是谁记的**（记账人昵称）。查账可看本月/本年等汇总。

**Why this priority**: 这是"共账"的字面核心——共享账目。没有它，账本只是"多人各记各的"，不算共账。

**Independent Test**: A、B 同账本，A 记一笔、B 记一笔 → A 查账能看到两笔，且能看出哪笔是 B 记的。

**Acceptance Scenarios**:

1. **Given** A、B 是同一账本成员，**When** A 记一笔账、B 记一笔账，**Then** A 查账时**能看到两笔**（含 B 记的）
2. **Given** 账本内有多笔账，**When** 任意成员查账，**Then** 每笔账可显示**记账人昵称**（谁记的）
3. **Given** 成员查账，**When** 查看本月/本年，**Then** 能看到该时间范围内账本的**全部**账目汇总（不限自己）

---

### User Story 6 - 默认账本与多账本 (Priority: P2)

每个用户自动拥有一个默认账本（创建用户时生成，该用户为 owner）；用户可创建多个账本，也可加入多个账本。

**Why this priority**: constitution 2.3——保证任何用户至少有账本可用（同默认昵称机制）。

**Independent Test**: 新用户 → 自动有默认账本且为 owner；可再创建/加入多个。

**Acceptance Scenarios**:

1. **Given** 新用户首次出现，**When** 系统初始化，**Then** 自动创建默认账本，该用户为 owner
2. **Given** 用户已有默认账本，**When** 再创建账本，**Then** 可拥有多个账本
3. **Given** 用户加入他人账本后，**When** 查看当前账本，**Then** 当前账本**未自动切换**到新加入的账本

---

### User Story 7 - 管理操作明确指定账本 (Priority: P2)

当用户有多个账本时，涉及账本的管理操作（移除/改名/删账本/审批/重置口令）必须明确作用于哪个账本，由用户指定，不得静默依赖"最近账本"。

**Why this priority**: constitution 2.4——避免在错误账本上执行危险操作。

**Independent Test**: 用户有 A、B 两账本，明确要求对 B 改名 → 只有 B 改，A 不变。

**Acceptance Scenarios**:

1. **Given** 用户有多个账本，**When** 发起管理操作但未指明账本，**Then** 系统请其确认要对哪个账本操作
2. **Given** 用户指明对账本 B 操作，**When** 执行，**Then** 仅影响 B，其他账本不受影响

---

### User Story 8 - member 主动退出账本 (Priority: P2)

member 可主动退出自己加入的账本。退出后不再能访问，但**其历史账目保留**在账本内。

**Why this priority**: 与"移除"对称——成员有权自己离开（constitution 2.2）。

**Independent Test**: member 退出 → 不再是成员、无法访问，但账目仍在账本。

**Acceptance Scenarios**:

1. **Given** member 已加入账本并记过账，**When** 该 member 主动退出，**Then** 其不再能访问该账本，但**其历史账目仍保留**在账本内
2. **Given** owner 想退出自己的账本，**When** owner 发起退出，**Then** 被拒绝（owner 只能删账本，不能退出——单 owner 模型）

---

### User Story 9 - 口令重置 (Priority: P2)

owner 可为账本**重新生成口令**（旧口令立即失效），用于口令泄露或需要控制加入时。

**Why this priority**: 口令是加入的唯一凭证，泄露后需要能重置（constitution 2.1）。

**Independent Test**: owner 重置口令 → 旧口令失效、新口令可申请加入。

**Acceptance Scenarios**:

1. **Given** 账本有旧口令，**When** owner 重置口令，**Then** 旧口令失效（用旧口令申请失败），返回新口令
2. **Given** member 尝试重置口令，**When** 其调用，**Then** 被拒绝（只有 owner 能重置）

---

### User Story 10 - 删除账本后成员的处理 (Priority: P2)

owner 删除（软删除）账本后，原成员**当前账本回落到默认账本**；成员**仍能查看该账本的历史账目**，但系统提示"该账本已被删除"。

**Why this priority**: constitution 4.1（软删除）+ 2.2——数据可追溯，但不能让用户以为账本还在用。

**Independent Test**: 删除账本 → 成员当前账本变为默认账本；查该账本历史可见但带"已删除"提示。

**Acceptance Scenarios**:

1. **Given** 账本被 owner 删除，**When** 原成员查看当前账本，**Then** 其当前账本**回落到默认账本**（不再指向已删账本）
2. **Given** 账本已删除，**When** 原成员查看该账本历史，**Then** 能看到历史账目，但系统**明确提示"该账本已被删除"**
3. **Given** 账本已删除，**When** 有人用其口令申请加入，**Then** 提示口令不存在（口令失效）

---

### Edge Cases

- 申请加入一个**已被软删除**的账本 → 口令失效，提示不存在。
- owner 不能"退出"自己的账本（单 owner 模型，只能删账本）。
- 同一用户对同一账本重复申请 → 幂等，不产生多条待审批。
- 移除/退出时昵称不存在 → 明确提示"账本里没有叫 X 的成员"。
- 被移除/退出/账本被删后，该用户当前账本若指向该账本 → **回落到默认账本**。
- 一个用户既是被删账本的成员、又是其他账本成员 → 仅回落"当前账本"，其他账本不受影响。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 支持**口令申请加入**：凭有效口令提交申请，进入**待审批**（pending）状态；审批前申请人**不得见到账本任何数据**。
- **FR-002**: 系统 MUST 让 **owner 同意**加入申请；同意后申请人才成为 member。（**拒绝**本次不实现；`rejected` 为状态预留值。）
- **FR-003**: 系统 MUST 保证**只有 owner** 可审批/移除成员/改名/删账本/重置口令；member 调用一律拒绝。
- **FR-004**: 权限判定 MUST 基于**目标账本内该用户的角色**，不得因该用户在别的账本是 owner 而放行。
- **FR-005**: 移除成员后，该成员 MUST 立即失去访问，但其**历史账目 MUST 保留**在账本内。
- **FR-006**: 每个用户 MUST 自动拥有默认账本（该用户为 owner），并可创建/加入多个账本。
- **FR-007**: 加入新账本后 MUST **不自动切换**当前账本。
- **FR-008**: 管理操作 MUST 明确指定目标账本（多账本时须确认），不得静默依赖"最近账本"。
- **FR-009**: 系统 MUST 让 owner 能查询**全部待审批申请**（含申请人昵称），保证待审批可得。
- **FR-010**: 重复申请同一账本 MUST 幂等（不产生重复待审批）。
- **FR-011**: 账本内**任一成员 MUST 能查看该账本全部账目**（含其他成员记的），且**每笔账 MUST 可显示记账人昵称**。
- **FR-012**: member MUST 可**主动退出**账本；退出后 MUST 失去访问，但其**历史账目 MUST 保留**。owner MUST 不能退出（只能删账本）。
- **FR-013**: owner MUST 可为账本**重置口令**（旧口令失效、返回新口令）；member 不得重置。
- **FR-014**: 账本被删除后，原成员 MUST 回落到默认账本作为当前账本；MUST 仍能查看该账本历史账目，且系统 MUST 明确提示"该账本已被删除"。

### Key Entities

- **User**：用户（openid 内部身份、nickname 展示名、current_ledger_id 当前账本）。
- **Ledger**：账本（name、owner_user_id、invite_code、created_at、deleted_at）。
- **LedgerMember**：成员关系（ledger_id、user_id、role=owner/member、joined_at）。
- **JoinRequest**（新增）：加入申请（ledger_id、user_id、status、created_at）。status 取值：`pending`（待审批）/ `approved`（已同意）；`rejected`（已拒绝）为**预留值**，本次不实现。
- **Transaction**：账目（已有 created_by_user_id 记录记账人；共账展示时关联其昵称）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 未获 owner 同意的申请人，访问账本数据的成功率 = 0%（看不到任何账目）。
- **SC-002**: member 调用管理操作被拒率 = 100%。
- **SC-003**: 成员被移除/退出后，其历史账目在账本内保留率 = 100%（一条不丢）。
- **SC-004**: owner 查询待审批时，能看到 100% 的待审批申请。
- **SC-005**: 新用户在无任何操作的情况下，默认账本存在率 = 100%。
- **SC-006**: 账本内任一成员查账时，能查到该账本 100% 的账目（含其他成员记的）。
- **SC-007**: 查账结果中，可显示记账人昵称的账目比例 = 100%。
- **SC-008**: 账本被删除后，原成员当前账本回落默认账本的比例 = 100%，且仍可查看其历史账目。

## Assumptions

- 沿用现有 `users`/`ledgers`/`ledger_members`/`transactions` 表；**新增 `join_requests` 表**承载待审批状态（不改现有表语义）。
- 角色仍为**两级**（owner/member），不引入更多角色。
- **单 owner 模型**：本次不做转让/多 owner（owner 像群主，只能删账本、不能退出）。转让为后续增强。
- **微信客服消息推送规则**：`message/custom/send` **只能发给 48 小时内与公众号互动过的用户**；窗口内无条数限制。因此审批的"通知 owner"**只做尽力而为**（48h 内可推则推，失败静默跳过、不阻塞），**保底靠 owner 主动查询**。
- 现有 `send_customer_message` + `undelivered` 队列可复用（推送失败入队，用户下次互动补发）。
- **本次范围只做"同意"**，"拒绝申请"不实现（YAGNI）。
- 口令通过微信等渠道自行转发即可（因有 owner 审批机制兜底），系统不限制分享方式。
- 已删除账本的历史账目**只读可查**（带"已删除"提示），不再可记账。
- 不重构历史账目；移除/退出不改变账目归属。
