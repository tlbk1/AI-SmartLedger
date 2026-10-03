"""成员与权限：审批制加入 / 成员变更 / 两级权限判定。"""
from typing import Optional
from datetime import datetime
from . import SHANGHAI, _connect
from .users import _create_default_ledger, _ensure_nickname_by_id, _get_user_id_by_openid, get_or_create_user
from .ledgers import _gen_invite_code, _get_default_ledger_id, _get_ledger_id_by_invite, _rebuild_default_anchor, _settle_current_ledger, get_user_ledger_id, is_ledger_deleted


def is_ledger_admin(openid: str, ledger_id: Optional[int] = None) -> bool:
    """判断 openid 是否是指定账本的 owner（管理员）。

    T003/spec-002 D2：显式传 ledger_id 时按【目标账本】判定（修复"当前账本误判"缺陷）；
    不传时回退到当前账本（向后兼容旧调用）。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False
    with _connect() as conn:
        row = conn.execute("""
            SELECT lm.role
            FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE u.openid = ? AND lm.ledger_id = ?
        """, (openid, ledger_id)).fetchone()
        return bool(row and row["role"] == "owner")


def is_ledger_member(openid: str, ledger_id: int) -> bool:
    """T004：判断 openid 是否为指定账本的成员（owner 或 member 均可）。"""
    with _connect() as conn:
        row = conn.execute("""
            SELECT 1 FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE u.openid = ? AND lm.ledger_id = ?
        """, (openid, ledger_id)).fetchone()
        return row is not None


def apply_join(openid: str, invite_code: str) -> tuple[bool, str, Optional[int]]:
    """US1/T010：凭口令提交加入申请 → 进入待审批(pending)。

    - 口令无效 → 失败
    - 已是成员 → 提示已是成员
    - 已有 pending → 幂等，不重复
    返回 (成功?, 消息, 申请的目标账本 id)。

    第三个返回值是调用方定位「本次申请的是哪本账」的唯一依据——通知 owner 必须
    用它，不能查「该用户最新一条 pending」（用户先后申请多本账时会送错人）。
    失败时账本 id 为 None。
    """
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        found = _get_ledger_id_by_invite(conn, invite_code)
        if found is None:
            return False, "口令不存在，请核对", None
        ledger_id, name = found
        # 已是成员？
        if conn.execute(
            "SELECT 1 FROM ledger_members WHERE ledger_id=? AND user_id=?", (ledger_id, user_id)
        ).fetchone():
            return False, f"你已经是「{name}」的成员了", None
        # 已有申请？
        row = conn.execute(
            "SELECT status FROM join_requests WHERE ledger_id=? AND user_id=?", (ledger_id, user_id)
        ).fetchone()
        if row:
            if row["status"] == "pending":
                return True, f"你已经申请加入「{name}」，等管理员同意即可", ledger_id
            if row["status"] == "approved":
                # 仍是成员 → 真的已是成员；若已被移除（ledger_members 无此人）→ 允许重新申请
                still_member = conn.execute(
                    "SELECT 1 FROM ledger_members WHERE ledger_id=? AND user_id=?",
                    (ledger_id, user_id),
                ).fetchone()
                if still_member:
                    return False, f"你已经是「{name}」的成员了", None
                conn.execute(
                    "UPDATE join_requests SET status='pending', created_at=? WHERE ledger_id=? AND user_id=?",
                    (datetime.now(SHANGHAI).isoformat(), ledger_id, user_id),
                )
                conn.commit()
                return True, f"已重新提交加入「{name}」的申请，等管理员同意", ledger_id
            # expired/rejected 之外的旧记录 → 重新申请（覆盖为 pending）
            conn.execute(
                "UPDATE join_requests SET status='pending', created_at=? WHERE ledger_id=? AND user_id=?",
                (datetime.now(SHANGHAI).isoformat(), ledger_id, user_id),
            )
            conn.commit()
            return True, f"已重新提交加入「{name}」的申请，等管理员同意", ledger_id
        # 新建申请
        conn.execute(
            "INSERT INTO join_requests (ledger_id, user_id, status, created_at) VALUES (?, ?, 'pending', ?)",
            (ledger_id, user_id, datetime.now(SHANGHAI).isoformat()),
        )
        conn.commit()
        return True, f"已提交加入「{name}」的申请，等管理员同意即可", ledger_id


def get_my_join_status(openid: str, ledger_id: int) -> Optional[str]:
    """US1/T011（FR-015）：查自己对某账本的申请状态；无申请返回 None。"""
    with _connect() as conn:
        uid = _get_user_id_by_openid(conn, openid)
        if uid is None:
            return None
        row = conn.execute(
            "SELECT status FROM join_requests WHERE ledger_id=? AND user_id=?", (ledger_id, uid)
        ).fetchone()
        return row["status"] if row else None


def _get_openid_by_nickname_in_ledger(ledger_id: int, nickname: str) -> Optional[str]:
    """T018：按昵称查某账本内成员的 openid（仅用于审批后推送通知，绝不进 LLM）。"""
    with _connect() as conn:
        row = conn.execute("""
            SELECT u.openid FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE lm.ledger_id = ? AND u.nickname = ?
            LIMIT 1
        """, (ledger_id, nickname)).fetchone()
        return row["openid"] if row else None


def get_my_join_status_text(openid: str) -> str:
    """US1：把用户所有申请状态整理成文本（供 agent 工具展示）。"""
    with _connect() as conn:
        uid = _get_user_id_by_openid(conn, openid)
        if uid is None:
            return "你还没有任何加入申请。"
        rows = conn.execute("""
            SELECT l.name, jr.status
            FROM join_requests jr
            JOIN ledgers l ON l.id = jr.ledger_id
            WHERE jr.user_id = ?
            ORDER BY jr.id DESC
        """, (uid,)).fetchall()
    if not rows:
        return "你还没有任何加入申请。"
    label = {"pending": "待审批", "approved": "已通过", "expired": "已作废（口令已重置）", "rejected": "已拒绝"}
    lines = [f"- 「{r['name']}」：{label.get(r['status'], r['status'])}" for r in rows]
    return "你的加入申请：\n" + "\n".join(lines)


def list_pending_joins(openid: str, ledger_id: Optional[int] = None) -> list[dict]:
    """US5/FR-026：owner 视角列出【指定账本】（缺省当前账本）的全部待审批申请
    （含申请人 nickname，不含 openid）。非该账本 owner → []。"""
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None or not is_ledger_admin(openid, ledger_id):
        return []
    with _connect() as conn:
        rows = conn.execute("""
            SELECT u.nickname, jr.created_at
            FROM join_requests jr
            JOIN users u ON u.id = jr.user_id
            WHERE jr.ledger_id = ? AND jr.status = 'pending'
            ORDER BY jr.id
        """, (ledger_id,)).fetchall()
        return [dict(r) for r in rows]


def approve_join(openid: str, applicant_nickname: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US5/FR-023/FR-026：owner 同意【指定账本】（缺省当前账本）的某申请人。

    按申请人昵称在该账本的 pending 申请中定位；pending → approved，并插入 member。
    返回 (成功?, 消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能同意加入申请"
    if is_ledger_deleted(ledger_id):
        return False, "该账本已被删除（只读），无法批准加入申请"
    with _connect() as conn:
        # 找 pending 申请中昵称匹配的申请人
        row = conn.execute("""
            SELECT jr.id AS req_id, u.id AS uid, u.nickname
            FROM join_requests jr
            JOIN users u ON u.id = jr.user_id
            WHERE jr.ledger_id = ? AND jr.status = 'pending' AND u.nickname = ?
            LIMIT 1
        """, (ledger_id, applicant_nickname)).fetchone()
        if row is None:
            return False, f"没有叫「{applicant_nickname}」的待审批申请"
        now = datetime.now(SHANGHAI).isoformat()
        conn.execute("UPDATE join_requests SET status='approved' WHERE id=?", (row["req_id"],))
        conn.execute(
            "INSERT OR IGNORE INTO ledger_members (ledger_id, user_id, role, joined_at) "
            "VALUES (?, ?, 'member', ?)",
            (ledger_id, row["uid"], now),
        )
        conn.commit()
        return True, f"已同意「{applicant_nickname}」加入"


def admin_remove_member(openid: str, target_nickname: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US3/T021 + US4/T024：owner 移除账本成员（按昵称在账本内查人）。

    - 权限按【目标账本】判定（显式传 ledger_id，默认当前账本）
    - **owner 不可被移除**（D7）
    - 移除只删成员关系，**不动历史账目**
    返回 (成功?, 消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能移除成员"
    if is_ledger_deleted(ledger_id):
        return False, "该账本已被删除（只读），无法移除成员"
    with _connect() as conn:
        # 在账本内按昵称找目标用户（注意：不在账本里的同名用户不误伤）
        row = conn.execute("""
            SELECT u.id, u.nickname, lm.role
            FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE lm.ledger_id = ? AND u.nickname = ?
            ORDER BY lm.id DESC LIMIT 1
        """, (ledger_id, target_nickname)).fetchone()
        if row is None:
            return False, f"账本里没有叫「{target_nickname}」的成员"
        # owner 不可被移除（D7）
        if row["role"] == "owner":
            return False, "不能移除管理员（owner）"
        target_uid = row["id"]
        # FR-012：被移除者若以本账本为默认锚点，先重建锚点（必须在删除成员关系之前判断）
        _rebuild_default_anchor(conn, target_uid, ledger_id, datetime.now(SHANGHAI).isoformat())
        cur = conn.execute(
            "DELETE FROM ledger_members WHERE ledger_id=? AND user_id=?",
            (ledger_id, target_uid),
        )
        conn.commit()
        if cur.rowcount == 0:
            return False, "该用户不在这个账本里"
        # FR-032/033/034：被移除者的当前账本确定回落（默认账本优先，永不静默落他人账本）
        _settle_current_ledger(conn, target_uid, ledger_id)
        conn.commit()
        return True, f"已移除成员 {target_nickname}"


def list_ledger_members(openid: str) -> list[dict]:
    """T004/T009: 列出当前账本的成员，只含代表身份的信息，绝不返回 openid。

    - SELECT 不再取 openid（从源头杜绝泄露，constitution 原则 I）
    - 返回前对每个成员调用 _ensure_nickname，保证 nickname 非空（默认或自设）
    """
    ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return []
    with _connect() as conn:
        rows = conn.execute("""
            SELECT u.id, u.nickname, lm.role
            FROM ledger_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE lm.ledger_id = ?
            ORDER BY (lm.role='owner') DESC, lm.id
        """, (ledger_id,)).fetchall()
        members = []
        for r in rows:
            nick = r["nickname"]
            if not nick or not nick.strip():
                # 空昵称 → 生成默认昵称并落库（_ensure_nickname 负责）
                # 这里在函数外单独落库，避免在 with conn 里再开连接
                nick = None
            members.append({"user_id": r["id"], "nickname": nick, "role": r["role"]})
        conn.commit()
    # 对空昵称成员在外面落默认昵称，并回填
    for m in members:
        if not m["nickname"] or not m["nickname"].strip():
            m["nickname"] = _ensure_nickname_by_id(m["user_id"])
    return members


def admin_rename_ledger(openid: str, new_name: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US3/T021：owner 修改账本名（权限按目标账本判定）。返回 (成功?, 消息)。
    评审：新名不能为空/纯空白（与 create_ledger 同规）。"""
    new_name = (new_name or "").strip()
    if not new_name:
        return False, "账本名不能为空，请给账本起个名字"
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能修改账本名"
    if is_ledger_deleted(ledger_id):
        return False, "该账本已被删除（只读），无法改名"
    with _connect() as conn:
        conn.execute("UPDATE ledgers SET name=? WHERE id=?", (new_name, ledger_id))
        conn.commit()
        return True, f"账本已改名为「{new_name}」"


def reset_invite_code(openid: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US9/T040（FR-013）：owner 重置账本口令。

    - 生成新口令，旧口令失效
    - 该账本所有 pending 申请置 expired（D5）
    返回 (成功?, 新口令 或 错误消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能重置口令"
    if is_ledger_deleted(ledger_id):
        return False, "该账本已被删除（只读），无法重置口令"
    with _connect() as conn:
        new_code = _gen_invite_code(conn)
        conn.execute("UPDATE ledgers SET invite_code=? WHERE id=?", (new_code, ledger_id))
        # 作废旧申请（D5）
        conn.execute(
            "UPDATE join_requests SET status='expired' WHERE ledger_id=? AND status='pending'",
            (ledger_id,),
        )
        conn.commit()
        return True, new_code


def leave_ledger(openid: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US8/T037（FR-012）：member 主动退出账本。

    - member 可退出（删除自己的成员关系，**不动历史账目**）
    - **owner 不能退出**（单 owner 模型，只能删账本，D7）
    返回 (成功?, 消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if is_ledger_deleted(ledger_id):
        return False, "该账本已被删除（只读），无法退出"
    with _connect() as conn:
        uid = _get_user_id_by_openid(conn, openid)
        if uid is None:
            return False, "用户不存在"
        row = conn.execute(
            "SELECT role FROM ledger_members WHERE ledger_id=? AND user_id=?", (ledger_id, uid)
        ).fetchone()
        if row is None:
            return False, "你不在这个账本里"
        if row["role"] == "owner":
            return False, "管理员不能退出账本（可删除账本）"
        # FR-012：退出者若以本账本为默认锚点，先重建锚点（必须在删除成员关系之前判断）
        _rebuild_default_anchor(conn, uid, ledger_id, datetime.now(SHANGHAI).isoformat())
        conn.execute("DELETE FROM ledger_members WHERE ledger_id=? AND user_id=?", (ledger_id, uid))
        # FR-032/033/034：退出后当前账本确定回落（默认账本优先）
        _settle_current_ledger(conn, uid, ledger_id)
        conn.commit()
        return True, "已退出账本（你的历史账目仍保留在这个账本里）"


def admin_delete_ledger(openid: str, ledger_id: Optional[int] = None) -> tuple[bool, str]:
    """US6/FR-028~FR-029 + US3 FR-013：owner 软删除账本。

    - 已删除的账本不能重复删（FR-029：明确提示，**不覆盖原删除时间**）
    - 成员（含 owner 本人）的当前账本**立即回落**到各自默认账本（FR-033）——
      系统默认不把任何人留在已删除账本；查看历史须显式切入（只读）
    - 默认账本即被删账本的成员 → 自动重建新的空「我的账本」并更新指向
      （FR-013 方案b：锚点永不落空；调用方须先经 FR-014 确认）
    返回 (成功?, 消息)。
    """
    if ledger_id is None:
        ledger_id = get_user_ledger_id(openid)
    if ledger_id is None:
        return False, "你没有加入任何账本"
    if not is_ledger_admin(openid, ledger_id):
        return False, "只有管理员才能删除账本"
    with _connect() as conn:
        row = conn.execute("SELECT deleted_at FROM ledgers WHERE id=?", (ledger_id,)).fetchone()
        if row is None:
            return False, "账本不存在"
        if row["deleted_at"]:
            return False, "该账本已被删除（只读），无需重复删除"
        members = conn.execute(
            "SELECT user_id FROM ledger_members WHERE ledger_id=?", (ledger_id,)
        ).fetchall()
        # 必须在打删除标记**之前**判断谁以此为默认账本——否则 _get_default_ledger_id
        # 会因账本已删而返回 None，重建逻辑（FR-013）永远不触发
        default_holders = [
            m["user_id"]
            for m in members
            if _get_default_ledger_id(conn, m["user_id"]) == ledger_id
        ]
        now = datetime.now(SHANGHAI).isoformat()
        conn.execute("UPDATE ledgers SET deleted_at=? WHERE id=?", (now, ledger_id))
        caller_uid = _get_user_id_by_openid(conn, openid)
        rebuilt_for_caller = False
        rebuilt_any = False
        for m in members:
            uid = m["user_id"]
            if uid in default_holders:
                # FR-013：默认账本就是刚删的这个 → 重建空默认账本并改指向
                new_lid = _create_default_ledger(conn, uid, now)
                conn.execute("UPDATE users SET default_ledger_id=? WHERE id=?", (new_lid, uid))
                rebuilt_any = True
                if uid == caller_uid:
                    rebuilt_for_caller = True
            _settle_current_ledger(conn, uid, ledger_id)
        conn.commit()
        if rebuilt_for_caller:
            cur = conn.execute(
                "SELECT current_ledger_id FROM users WHERE id=?", (caller_uid,)
            ).fetchone()
            extra = ""
            if cur and cur["current_ledger_id"]:
                r = conn.execute(
                    "SELECT name FROM ledgers WHERE id=?", (cur["current_ledger_id"],)
                ).fetchone()
                if r:
                    extra = f"，当前账本已切到「{r['name']}」"
            return True, f"账本已删除（软删除，账目保留可追溯）。已自动重建新的空默认账本「我的账本」{extra}"
        if rebuilt_any:
            return True, "账本已删除（软删除，账目保留可追溯）。已为默认账本被删的成员自动重建「我的账本」"
        return True, "账本已删除（软删除，账目保留可追溯；成员当前账本已回落到各自默认账本）"
