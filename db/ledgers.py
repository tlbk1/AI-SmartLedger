"""账本与指针：创建 / selector 解析 / 切换 / 默认锚点 / 软删除。"""
from typing import Optional
from datetime import datetime
from . import SHANGHAI, _connect
from .users import _create_default_ledger, _ensure_nickname_by_id, get_or_create_user


_INVITE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def _gen_invite_code(conn) -> str:
    """生成唯一邀请口令（6位，去易混淆字符）。"""
    import secrets
    for _ in range(50):  # 防碰撞，最多试50次
        code = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(6))
        if conn.execute("SELECT 1 FROM ledgers WHERE invite_code=?", (code,)).fetchone() is None:
            return code
    raise RuntimeError("无法生成唯一邀请口令")


def _fallback_ledger_id(conn, user_id: int, exclude: Optional[int] = None) -> Optional[int]:
    """FR-035 兜底链：用户自己的默认账本 → 最近加入的未删除账本 → None。

    评审问题2修复：旧逻辑只按「最近加入」回退，会把用户静默落进别人的共享账本。
    exclude 用于回落场景排除刚失效的那个账本。

    默认锚点必须**同时**是未删除且用户仍在成员表里：被移除/退出后锚点可能仍
    指向那本账（`admin_remove_member`/`leave_ledger` 负责重建，此处是读时止损），
    只判 deleted_at 会把已移除的人再送回那本账——账本隔离失效。
    """
    default_lid = _get_default_ledger_id(conn, user_id)
    if (
        default_lid is not None
        and default_lid != exclude
        and _is_member_by_uid(conn, user_id, default_lid)
    ):
        return default_lid
    row = conn.execute("""
        SELECT lm.ledger_id
        FROM ledger_members lm
        JOIN ledgers l ON l.id = lm.ledger_id
        WHERE lm.user_id = ? AND l.deleted_at IS NULL AND lm.ledger_id != ?
        ORDER BY lm.id DESC
        LIMIT 1
    """, (user_id, exclude if exclude is not None else -1)).fetchone()
    return row["ledger_id"] if row else None


def _is_member_by_uid(conn, user_id: int, ledger_id: int) -> bool:
    """复用已打开的连接判断成员关系（`is_ledger_member` 按 openid 查且另开连接）。"""
    return conn.execute(
        "SELECT 1 FROM ledger_members WHERE ledger_id=? AND user_id=?",
        (ledger_id, user_id),
    ).fetchone() is not None


def _rebuild_default_anchor(conn, uid: int, dead_ledger_id: int, now: str) -> bool:
    """FR-012：默认锚点指向的账本失效（被移除/退出）时重建锚点，锚点永不落空。

    与 `admin_delete_ledger` 的同名处理同源：先建一本新的「我的账本」占位，
    再让兜底链在该账本被删时把锚点改指到其它未删除的共同账本。
    **必须在删除成员关系之前调用**——否则 `_get_default_ledger_id` 的判断条件
    （用户是否以该账本为默认）已不成立。
    返回是否重建。
    """
    if _get_default_ledger_id(conn, uid) != dead_ledger_id:
        return False
    new_lid = _create_default_ledger(conn, uid, now)
    conn.execute("UPDATE users SET default_ledger_id=? WHERE id=?", (new_lid, uid))
    return True


def _settle_current_ledger(conn, uid: int, dead_ledger_id: int):
    """FR-032/033/034：uid 的当前账本失效（被移除/退出/被删）后确定回落。

    默认账本优先，取不到退最近加入（FR-035）；仅当用户已无任何账本
    （仅存量脏数据可达）才写空——由读时兜底链终结为「明确报错」。
    """
    cur = conn.execute("SELECT current_ledger_id FROM users WHERE id=?", (uid,)).fetchone()
    if not (cur and cur["current_ledger_id"] == dead_ledger_id):
        return
    target = _fallback_ledger_id(conn, uid, exclude=dead_ledger_id)
    conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (target, uid))


def get_user_ledger_id(openid: str) -> Optional[int]:
    """根据 openid 找用户「当前账本」id（任务3：读 users.current_ledger_id）。

    FR-036：**尊重用户的显式选择**——即使用户当前账本已被软删除也照原样返回
    （让他能查看历史账目，只读；写入在 insert/工具层拒绝）。
    FR-035：仅当指针为空或悬空时才兜底——默认账本 → 最近加入的未删除账本 →
    None（上层明确报错，绝不静默落进他人账本）。
    """
    with _connect() as conn:
        # 一次取全（id + 当前指针）：热路径不再把同一行 users 查三遍
        row = conn.execute(
            "SELECT id, current_ledger_id FROM users WHERE openid=?", (openid,)
        ).fetchone()
        if row is None:
            return None
        if row["current_ledger_id"] is not None:
            # 账本存在即返回（含已软删除——用户显式切入是为了看历史，只读）。
            # 仍要求成员关系成立：被移除/退出后失效的指针不得再读到该账本（账本隔离）。
            l = conn.execute("SELECT 1 FROM ledgers WHERE id=?", (row["current_ledger_id"],)).fetchone()
            if l and _is_member_by_uid(conn, row["id"], row["current_ledger_id"]):
                return row["current_ledger_id"]
        # FR-035 兜底链：默认账本 → 最近加入的未删除账本 → None
        return _fallback_ledger_id(conn, row["id"])


def create_ledger(openid: str, name: str) -> tuple[bool, str]:
    """创建账本，创建者为 owner，并设为该用户的当前账本（任务3）。
    FR-008 同类校验（评审）：账本名不能为空/纯空白——否则列表里出现无名条目、
    且无法按名字选中（空 selector 会被解析器当成"未指定"）。
    返回 (成功?, 结果消息或口令)。"""
    name = (name or "").strip()
    if not name:
        return False, "账本名不能为空，请给账本起个名字（比如「我们家」「旅行账」）"
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        invite = _gen_invite_code(conn)
        now = datetime.now(SHANGHAI).isoformat()
        cur = conn.execute(
            "INSERT INTO ledgers (name, owner_user_id, invite_code, created_at) VALUES (?, ?, ?, ?)",
            (name, user_id, invite, now),
        )
        ledger_id = cur.lastrowid
        conn.execute(
            "INSERT INTO ledger_members (ledger_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
            (ledger_id, user_id, now),
        )
        # 任务3：新创建的账本设为当前账本
        conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (ledger_id, user_id))
        conn.commit()
        return True, invite


def get_ledger_owner_openid(ledger_id: int) -> Optional[str]:
    """FR-038：取账本 owner 的 openid（仅供推送通知使用，绝不进 LLM/展示层）。"""
    with _connect() as conn:
        row = conn.execute("""
            SELECT u.openid FROM ledgers l
            JOIN users u ON u.id = l.owner_user_id
            WHERE l.id = ?
        """, (ledger_id,)).fetchone()
        return row["openid"] if row else None


def _members_preview(conn, ledger_id: int, limit: int = 5) -> tuple[list[str], int]:
    """FR-016：成员昵称预览——owner 优先、其余按加入顺序（稳定），截断并返回总人数。
    只返回昵称，绝不返回 openid（constitution 原则 II）。"""
    rows = conn.execute("""
        SELECT u.id, u.nickname FROM ledger_members lm
        JOIN users u ON u.id = lm.user_id
        WHERE lm.ledger_id = ?
        ORDER BY CASE WHEN lm.role='owner' THEN 0 ELSE 1 END, lm.id ASC
    """, (ledger_id,)).fetchall()
    names = []
    for r in rows[:limit]:
        nick = (r["nickname"] or "").strip()
        if not nick:
            nick = _ensure_nickname_by_id(r["id"])
        names.append(nick)
    return names, len(rows)


def ledger_member_preview(ledger_id: int, limit: int = 5) -> dict:
    """FR-015/FR-016：对外取某账本成员名单预览（供账本列表展示）。"""
    with _connect() as conn:
        names, total = _members_preview(conn, ledger_id, limit)
        return {"names": names, "total": total}


def get_my_ledgers(openid: str) -> list[dict]:
    """列出用户加入的所有账本（FR-015/FR-018 的数据源）。

    - **包含已软删除的账本**（带 `is_deleted` 标记）——原成员仍能查看其历史账目
    - 附 `is_current` / `is_default` 标记（FR-014：默认账本由指向关系确定）
    - 按加入顺序排序（lm.id ASC）：新加入的排末尾，展示顺序稳定
    """
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        u = conn.execute(
            "SELECT current_ledger_id, default_ledger_id FROM users WHERE id=?", (user_id,)
        ).fetchone()
        cur_id = u["current_ledger_id"] if u else None
        def_id = u["default_ledger_id"] if u else None
        rows = conn.execute("""
            SELECT l.id, l.name, l.invite_code, lm.role, lm.joined_at, l.deleted_at
            FROM ledger_members lm
            JOIN ledgers l ON l.id = lm.ledger_id
            WHERE lm.user_id = ?
            ORDER BY lm.id ASC
        """, (user_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["is_deleted"] = bool(d.get("deleted_at"))
            d["is_current"] = d["id"] == cur_id
            d["is_default"] = d["id"] == def_id
            out.append(d)
        return out


def get_ledger_info(ledger_id: int) -> Optional[dict]:
    """T050：取账本信息（含已软删除的）。返回 {id, name, is_deleted} 或 None。

    与 get_user_ledger_id 不同：**不过滤 deleted_at**，供"查已删账本历史"路径判断是否要附提示。
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, name, deleted_at FROM ledgers WHERE id=?", (ledger_id,)
        ).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "name": row["name"], "is_deleted": bool(row["deleted_at"])}


def is_ledger_deleted(ledger_id: Optional[int]) -> bool:
    """T050：账本是否已被软删除（不存在也算 True——不存在的账本不可用）。"""
    if ledger_id is None:
        return True
    info = get_ledger_info(ledger_id)
    return True if info is None else info["is_deleted"]


def resolve_ledger_selector(openid: str, selector: str) -> tuple[Optional[int], Optional[str]]:
    """FR-018/FR-023：在用户已加入的账本（含已删除）中解析 selector（`#N` 或名称）。

    返回 (ledger_id, None) 或 (None, 提示文本)。重名时提示文本为候选列表
    （编号+成员名单，由代码实时重算，FR-022），供调用方原样转述给用户。
    """
    user_id = get_or_create_user(openid)
    sel = (selector or "").strip()
    if not sel:
        return None, "请指定账本（编号或名称）"
    with _connect() as conn:
        raw = sel.lstrip("#").strip()
        if raw.isdigit():
            row = conn.execute("""
                SELECT l.id FROM ledger_members lm
                JOIN ledgers l ON l.id = lm.ledger_id
                WHERE lm.user_id = ? AND l.id = ?
            """, (user_id, int(raw))).fetchone()
            if row is None:
                return None, f"没有编号为 #{int(raw)} 的账本"
            return row["id"], None
        rows = conn.execute("""
            SELECT l.id, l.name FROM ledger_members lm
            JOIN ledgers l ON l.id = lm.ledger_id
            WHERE lm.user_id = ? AND l.name = ?
            ORDER BY lm.id ASC
        """, (user_id, sel)).fetchall()
        if not rows:
            return None, f"你还没有加入叫「{sel}」的账本"
        if len(rows) > 1:
            # FR-019：重名不猜——列候选请用户回复编号
            lines = []
            for r in rows:
                names, total = _members_preview(conn, r["id"])
                extra = f" 等 {total} 人" if total > len(names) else ""
                lines.append(f"· #{r['id']} {r['name']}（成员：{'、'.join(names) or '（无）'}{extra}）")
            return None, f"你有 {len(rows)} 个叫「{sel}」的账本，回复编号选择：\n" + "\n".join(lines)
        return rows[0]["id"], None


def switch_ledger(openid: str, selector: str) -> tuple[bool, str]:
    """FR-018~FR-022：用编号（`#N` / 裸数字）或名称切换当前账本。

    - `#N`/纯数字 → 按内部 id 在用户账本列表（含已删除）中**精确匹配**；
      未命中明确报错，MUST NOT 按位置/最近加入解释（FR-018/FR-021）
    - 名称唯一 → 直接切换（FR-020）
    - 名称重名 → **不切换**，返回全部同名候选请用户回复编号（FR-019/FR-022）
    - 允许**显式切入**已删除账本（合法只读，带提示）（FR-036）
    """
    user_id = get_or_create_user(openid)
    sel = (selector or "").strip()
    if not sel:
        return False, "请告诉我要切换到哪个账本（编号或名称），如「#4」或「我们家」"
    lid, err = resolve_ledger_selector(openid, sel)
    if err is not None:
        return False, err
    with _connect() as conn:
        target = conn.execute(
            "SELECT id, name, deleted_at FROM ledgers WHERE id=?", (lid,)
        ).fetchone()
        if target is None:
            return False, "该账本不存在"
        conn.execute("UPDATE users SET current_ledger_id=? WHERE id=?", (target["id"], user_id))
        conn.commit()
        if target["deleted_at"]:
            return True, (
                f"已切到「{target['name']}」（#{target['id']}）——该账本已被删除，"
                "只能查看历史账目（只读），不能再记账。"
            )
        return True, f"已切到「{target['name']}」（#{target['id']}），后续记账/查账都在这个账本"


def _get_ledger_id_by_invite(conn, invite_code: str) -> Optional[tuple[int, str]]:
    """T005：按口令查未删除账本，返回 (ledger_id, name)；找不到返回 None。"""
    row = conn.execute(
        "SELECT id, name FROM ledgers WHERE invite_code=? AND deleted_at IS NULL",
        (invite_code.strip().lower(),),
    ).fetchone()
    return (row["id"], row["name"]) if row else None


def _get_default_ledger_id(conn, user_id: int) -> Optional[int]:
    """FR-012：默认账本由指向关系确定（users.default_ledger_id），**不靠名字识别**。

    账本改名不影响返回值。指向已删除/不存在的账本视为失效（返回 None 走兜底）——
    正常数据下不会发生（删除默认账本会触发重建并更新指向，见 admin_delete_ledger）。
    """
    row = conn.execute(
        "SELECT default_ledger_id FROM users WHERE id=?", (user_id,)
    ).fetchone()
    lid = row["default_ledger_id"] if row else None
    if lid is None:
        return None
    alive = conn.execute(
        "SELECT 1 FROM ledgers WHERE id=? AND deleted_at IS NULL", (lid,)
    ).fetchone()
    return lid if alive else None


def is_default_ledger(openid: str, ledger_id: int) -> bool:
    """FR-014：判断账本是否为该用户的默认账本（供删除确认流程使用）。"""
    user_id = get_or_create_user(openid)
    with _connect() as conn:
        return _get_default_ledger_id(conn, user_id) == ledger_id
