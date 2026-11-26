"""单次结构化调度 + 精确命令快路径。"""
import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import langgraph.prebuilt as lp
from langchain_core.messages import AIMessage

from ai_smartledger import db
from ai_smartledger.agents import fallback_agent, fast_paths
from ai_smartledger.agents import react_agent as agent
from ai_smartledger.core.prompts import GREETING_TEXT, HELP_TEXT

STATUS = (
    "<agent_status>当前时间 2026-11-26T12:00:00+08:00；"
    "当前账本 「我的账本」（#1）；待审批 0 条</agent_status>"
)


@pytest.fixture
def iso(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(db, "DB_PATH", pathlib.Path(tmp) / "t.db")
    db.init()
    yield
    shutil.rmtree(tmp, ignore_errors=True)


def _seed(openid="o_q"):
    uid = db.get_or_create_user(openid)
    lid = db.get_user_ledger_id(openid)
    db.insert_many_for_ledger(
        lid,
        uid,
        [
            db.Transaction("expense", 30, "餐饮", "2026-11-10T12:00:00+08:00", "午餐"),
            db.Transaction("expense", 50, "娱乐", "2026-11-20T19:00:00+08:00", "电影"),
            db.Transaction("income", 5000, "工资", "2026-11-01T09:00:00+08:00", "工资"),
        ],
    )
    return lid


def _count(openid):
    lid = db.get_user_ledger_id(openid)
    return len(db.query_by_ledger(lid, "1970-01-01", "2999-12-31", limit=1000))


def _stub(monkeypatch, data):
    monkeypatch.setattr(
        fallback_agent, "dispatch_message", lambda content, status: data
    )


def _query(**kw):
    base = {
        "date_from": "2026-11-01",
        "date_to": "2026-11-26",
        "category": None,
        "type": None,
        "detail": False,
        "limit": 5,
        "order_by": "top_amount",
        "label": "本月",
    }
    base.update(kw)
    return {"intent": "query", "query": base}


def test_static_commands_are_exact_and_llm_free(iso, monkeypatch):
    monkeypatch.setattr(
        fallback_agent,
        "dispatch_message",
        lambda *a: (_ for _ in ()).throw(AssertionError("dispatcher called")),
    )
    assert fast_paths.fast_reply("o_s", "你好", STATUS) == GREETING_TEXT
    assert fast_paths.fast_reply("o_s", "帮助", STATUS) == HELP_TEXT


def test_ledger_command_exact_and_llm_free(iso, monkeypatch):
    db.get_or_create_user("o_l")
    monkeypatch.setattr(
        fallback_agent,
        "dispatch_message",
        lambda *a: (_ for _ in ()).throw(AssertionError("dispatcher called")),
    )
    reply = fast_paths.fast_reply("o_l", "我的账本", STATUS)
    assert "你的账本：" in reply
    assert fast_paths.ledger_fast_reply("o_l", "我的账本这个月多少钱") is None


def test_ledger_like_query_goes_to_dispatcher(iso, monkeypatch):
    _seed("o_lq")
    _stub(monkeypatch, _query(type="expense"))
    reply = fast_paths.fast_reply("o_lq", "我的账本这个月多少钱", STATUS)
    assert "80" in reply and "本月" in reply
    assert "你的账本" not in reply


def test_record_dispatch_writes(iso, monkeypatch):
    db.get_or_create_user("o_rec")
    _stub(
        monkeypatch,
        {
            "intent": "record",
            "transactions": [
                {
                    "type": "expense",
                    "amount": 25,
                    "category": "餐饮",
                    "note": "午饭",
                    "happened_at": "2026-11-26T12:00:00+08:00",
                }
            ],
        },
    )
    reply = fast_paths.fast_reply("o_rec", "午饭 25", STATUS)
    assert "记好" in reply and "25" in reply
    assert _count("o_rec") == 1


def test_record_dispatch_multi(iso, monkeypatch):
    db.get_or_create_user("o_rec2")
    _stub(
        monkeypatch,
        {
            "intent": "record",
            "transactions": [
                {"type": "expense", "amount": 15, "category": "交通", "note": "打车"},
                {"type": "expense", "amount": 22, "category": "餐饮", "note": "咖啡"},
            ],
        },
    )
    reply = fast_paths.fast_reply("o_rec2", "打车15，咖啡22", STATUS)
    assert "记好" in reply and "15" in reply and "22" in reply
    assert _count("o_rec2") == 2


def test_record_dispatch_bad_amount_uses_tool_error(iso, monkeypatch):
    db.get_or_create_user("o_rec3")
    _stub(
        monkeypatch,
        {
            "intent": "record",
            "transactions": [{"type": "expense", "amount": "bad", "category": "餐饮"}],
        },
    )
    reply = fast_paths.fast_reply("o_rec3", "午饭 bad", STATUS)
    assert "金额无效" in reply
    assert _count("o_rec3") == 0


def test_record_dispatch_without_transactions_falls_back(iso, monkeypatch):
    db.get_or_create_user("o_rec4")
    _stub(monkeypatch, {"intent": "record", "transactions": []})
    assert fast_paths.fast_reply("o_rec4", "午饭 25", STATUS) is None
    assert _count("o_rec4") == 0


def test_query_totals(iso, monkeypatch):
    _seed("o_q1")
    _stub(monkeypatch, _query(type="expense"))
    reply = fast_paths.fast_reply("o_q1", "这个月我花了多少钱", STATUS)
    assert "80" in reply and "支出" in reply
    assert "5000" not in reply


def test_query_category(iso, monkeypatch):
    _seed("o_q2")
    _stub(monkeypatch, _query(category="餐饮", type="expense"))
    reply = fast_paths.fast_reply("o_q2", "查一下我的餐饮消费", STATUS)
    assert "30" in reply and "餐饮" in reply
    assert "50" not in reply


def test_query_last_month_empty(iso, monkeypatch):
    _seed("o_q3")
    _stub(
        monkeypatch,
        _query(date_from="2026-10-01", date_to="2026-10-31", type="expense", label="上个月"),
    )
    reply = fast_paths.fast_reply("o_q3", "上个月我花了多少钱", STATUS)
    assert "没有" in reply


def test_query_income(iso, monkeypatch):
    _seed("o_q4")
    _stub(monkeypatch, _query(type="income"))
    reply = fast_paths.fast_reply("o_q4", "这个月收入多少", STATUS)
    assert "5000" in reply and "收入" in reply


def test_query_both_types(iso, monkeypatch):
    _seed("o_q5")
    _stub(monkeypatch, _query())
    reply = fast_paths.fast_reply("o_q5", "这个月账单", STATUS)
    assert "80" in reply and "5000" in reply


def test_query_recent_detail(iso, monkeypatch):
    _seed("o_q6")
    _stub(
        monkeypatch,
        _query(
            date_from="1970-01-01",
            date_to="2026-11-26",
            detail=True,
            order_by="recent",
            limit=2,
            label="最近",
        ),
    )
    reply = fast_paths.fast_reply("o_q6", "最近2笔", STATUS)
    assert "最近 2 笔" in reply
    assert "电影" in reply and "午餐" in reply


def test_query_invalid_dates_fall_back(iso, monkeypatch):
    _seed("o_q7")
    _stub(monkeypatch, _query(date_from="bad", date_to="2026-11-26"))
    assert fast_paths.fast_reply("o_q7", "这个月花了多少", STATUS) is None


def test_chat_and_clarify(iso, monkeypatch):
    db.get_or_create_user("o_c")
    _stub(monkeypatch, {"intent": "chat", "reply": "你好呀"})
    assert fast_paths.fast_reply("o_c", "你好呀", STATUS) == "你好呀"
    _stub(monkeypatch, {"intent": "clarify", "reply": "你是想记账还是查账？"})
    assert fast_paths.fast_reply("o_c", "查一下昨天咖啡22", STATUS) == "你是想记账还是查账？"


def test_agent_intent_falls_back(iso, monkeypatch):
    db.get_or_create_user("o_a")
    _stub(monkeypatch, {"intent": "agent"})
    assert fast_paths.fast_reply("o_a", "帮我建个账本", STATUS) is None


def test_dispatch_failure_falls_back(iso, monkeypatch):
    db.get_or_create_user("o_f")
    _stub(monkeypatch, {})
    assert fast_paths.fast_reply("o_f", "随便说一句", STATUS) is None


class _FakeAgent:
    def __init__(self):
        self.messages = None

    def invoke(self, payload, config=None):
        self.messages = payload["messages"]
        return {"messages": [*payload["messages"], AIMessage("ok")]}


def test_run_agent_static_fast_path_without_model(iso, monkeypatch):
    monkeypatch.setattr(
        agent,
        "_get_model",
        lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )
    monkeypatch.setattr(
        fallback_agent,
        "dispatch_message",
        lambda *a: (_ for _ in ()).throw(AssertionError("dispatcher called")),
    )
    assert agent.run_agent("o_static", "你好") == GREETING_TEXT


def test_run_agent_query_fast_path_without_model(iso, monkeypatch):
    _seed("o_qrun")
    _stub(monkeypatch, _query(type="expense"))
    monkeypatch.setattr(
        agent,
        "_get_model",
        lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )
    reply = agent.run_agent("o_qrun", "这个月我花了多少钱")
    assert "80" in reply


def test_run_agent_agent_intent_uses_agent(iso, monkeypatch):
    db.get_or_create_user("o_arun")
    _stub(monkeypatch, {"intent": "agent"})
    fake = _FakeAgent()
    monkeypatch.setattr(agent, "_get_model", lambda: object())
    monkeypatch.setattr(lp, "create_react_agent", lambda **kwargs: fake)
    assert agent.run_agent("o_arun", "帮我建个账本") == "ok"
    assert fake.messages[-1]["content"] == "帮我建个账本"
    assert fake.messages[-2]["content"].startswith("<agent_status>")
