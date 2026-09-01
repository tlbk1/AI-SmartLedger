"""一次性迁移脚本：把存量 ledger_id 为 NULL 的旧记录归入「历史账本」。
用法（一次性）：python scripts/migrate_old_ledger.py
安全：只把 ledger_id IS NULL 的记录归到一个新建的「历史账本」，不碰其他数据。
之后可删除本脚本或保留作记录。
"""
import os, sys, sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import secrets

# 允许从项目根目录运行
sys.path.insert(0, str(Path(__file__).parent.parent))
import db

SHANGHAI = ZoneInfo("Asia/Shanghai")


def main():
    with db._connect() as conn:
        null_rows = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE ledger_id IS NULL"
        ).fetchone()[0]
        if null_rows == 0:
            print("没有 NULL 记录，无需迁移。")
            return

        now = datetime.now(SHANGHAI).isoformat()
        # 建一个「历史账本」，owner 为 NULL(系统所有)
        inv = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(6))
        cur = conn.execute(
            "INSERT INTO ledgers (name, owner_user_id, invite_code, created_at) VALUES (?, NULL, ?, ?)",
            ("历史账本", inv, now),
        )
        ledger_id = cur.lastrowid
        # 把 NULL 记录归入历史账本
        conn.execute(
            "UPDATE transactions SET ledger_id=?, created_by_user_id=NULL WHERE ledger_id IS NULL",
            (ledger_id,),
        )
        conn.commit()
        print(f"✅ 已迁移 {null_rows} 条 NULL 记录到「历史账本」(id={ledger_id})")


if __name__ == "__main__":
    main()
