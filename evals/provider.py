"""
evals/provider.py — promptfoo 自定义 Provider（Python）

promptfoo 每条用例都会调用本文件的 call_api(prompt, options, context)：
prompt 即用例里的 message，我们把它交给项目真实入口 graph.process_message()，
拿到机器人最终回复后返回给 promptfoo 做断言——因此验证的是
「agent 循环 + 工具 + DB」的全链路行为，而不是裸 LLM。

隔离设计：
- 独立数据库 evals/.eval_ledger.db（运行期改写 db.DB_PATH），不碰生产 ledger.db
- 每条用例用各自的 openid（vars.openid）→ 各自的默认账本，用例之间互不污染
- openid 为 eval-reader 的用例首次运行时播种三条固定流水（幂等），供查询类断言使用

运行前提：promptfoo 要能找到带项目依赖的 Python（openai/langgraph 等），见 README.md。
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # .env 与项目内相对路径统一以项目根为基准

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import db  # noqa: E402

# 重定向到评测专用库。db._connect() 每次都读模块属性，运行期改写即可生效。
db.DB_PATH = Path(__file__).resolve().parent / ".eval_ledger.db"

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _ensure_user(openid: str) -> None:
    """建表 + 幂等建用户（spec-003 FR-002：用户出现即初始化默认账本）。"""
    db.init()
    db.get_or_create_user(openid)


def _seed_reader(openid: str) -> None:
    """给查询类用例播种固定流水（只播一次）。

    播种内容（查询断言的依据）：
    - 今天 12:00 午餐 餐饮 30 元（支出）
    - 昨天 19:00 电影 娱乐 50 元（支出）
    - 本月 1 号 09:00 发工资 收入 5000 元
    """
    ledger_id = db.get_user_ledger_id(openid)
    if ledger_id is None:
        return
    now = datetime.now(SHANGHAI)
    rows = db.query_by_ledger(
        ledger_id,
        now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(),
        (now + timedelta(days=1)).isoformat(),
        limit=100,
    )
    if len(rows) >= 3:
        return
    today = now.strftime("%Y-%m-%dT12:00:00+08:00")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%dT19:00:00+08:00")
    month_start = now.replace(day=1).strftime("%Y-%m-%dT09:00:00+08:00")
    db.insert_many_for_ledger(
        ledger_id,
        db.get_or_create_user(openid),
        [
            db.Transaction(type="expense", amount=30.0, category="餐饮", note="午餐", happened_at=today),
            db.Transaction(type="expense", amount=50.0, category="娱乐", note="电影", happened_at=yesterday),
            db.Transaction(type="income", amount=5000.0, category="工资", note="发工资", happened_at=month_start),
        ],
    )


def call_api(prompt: str, options, context) -> dict:
    """promptfoo Python Provider 约定接口。"""
    try:
        vars_ = (context or {}).get("vars", {}) or {}
        openid = vars_.get("openid") or "eval-default"
        _ensure_user(openid)
        if openid == "eval-reader":
            _seed_reader(openid)

        import graph  # 延迟导入：确保上面的 DB 重定向先生效

        reply = graph.process_message(openid, prompt)
        return {"output": reply}
    except Exception as exc:  # 让 promptfoo 把失败显示成用例错误，而不是整个 eval 崩掉
        return {"error": f"{type(exc).__name__}: {exc}"}
