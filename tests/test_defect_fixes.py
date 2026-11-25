"""007-defect-fixes：记录工具入参校验 + 回复只取最后一条 AI 消息。"""
import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import langgraph.prebuilt as lp
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ai_smartledger import db
from ai_smartledger.agents import react_agent as agent


@pytest.fixture
def iso(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(db, "DB_PATH", pathlib.Path(tmp) / "t.db")
    db.init()
    yield
    shutil.rmtree(tmp, ignore_errors=True)


def _tool(openid: str):
    db.get_or_create_user(openid)
    return {t.name: t for t in agent.make_tools(openid)}["record_transactions"]


def _count(openid: str) -> int:
    lid = db.get_user_ledger_id(openid)
    return len(db.query_by_ledger(lid, "1970-01-01", "2999-12-31", limit=1000))


def test_record_rejects_empty_batch(iso):
    tool = _tool("o_empty")
    out = tool.invoke({"transactions": []})
    assert "没有要记" in out
    assert _count("o_empty") == 0


def test_record_rejects_non_dict_item(iso):
    tool = _tool("o_nondict")
    out = tool.invoke({"transactions": ["x"]})
    assert "不是有效账目" in out
    assert _count("o_nondict") == 0


def test_record_rejects_missing_amount(iso):
    tool = _tool("o_missing")
    out = tool.invoke({"transactions": [{"type": "expense", "category": "餐饮"}]})
    assert "金额无效" in out
    assert _count("o_missing") == 0


@pytest.mark.parametrize(
    "amount", ["12元", None, "", pytest.param(10**400, id="overflow")]
)
def test_record_rejects_bad_amount(iso, amount):
    tool = _tool("o_bad")
    out = tool.invoke({"transactions": [{"type": "expense", "amount": amount}]})
    assert "金额无效" in out
    assert _count("o_bad") == 0


def test_record_rejects_bad_second_item_without_partial_write(iso):
    tool = _tool("o_partial")
    out = tool.invoke(
        {"transactions": [{"amount": 10}, {"amount": "不是数字"}]}
    )
    assert "金额无效" in out
    assert _count("o_partial") == 0


def test_record_accepts_legal_batch(iso):
    tool = _tool("o_ok")
    out = tool.invoke(
        {"transactions": [{"amount": 25, "category": "餐饮", "note": "午饭"}]}
    )
    assert "已记 1 笔" in out
    lid = db.get_user_ledger_id("o_ok")
    rows = db.query_by_ledger(lid, "1970-01-01", "2999-12-31", limit=1000)
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "expense"
    assert row["amount"] == 25
    assert row["category"] == "餐饮"
    assert row["note"] == "午饭"
    assert row["happened_at"]


def test_extract_reply_only_accepts_last_ai_message():
    assert agent._extract_reply([HumanMessage("x"), AIMessage("ok")]) == "ok"
    assert agent._extract_reply([HumanMessage("x"), AIMessage("")]) == ""
    assert (
        agent._extract_reply(
            [
                HumanMessage("x"),
                AIMessage("old"),
                ToolMessage("tool", tool_call_id="1"),
            ]
        )
        == ""
    )
    assert agent._extract_reply([AIMessage("old"), HumanMessage("用户原话")]) == ""


class _FakeAgent:
    def __init__(self, messages):
        self.messages = messages

    def invoke(self, payload, config=None):
        return {"messages": self.messages}


def _patch_agent(monkeypatch, messages):
    monkeypatch.setattr(agent, "_get_model", lambda: object())
    monkeypatch.setattr(lp, "create_react_agent", lambda **kwargs: _FakeAgent(messages))


def test_run_agent_returns_failure_when_last_is_tool(iso, monkeypatch):
    messages = [
        HumanMessage("测试"),
        AIMessage("", tool_calls=[{"name": "x", "args": {}, "id": "1"}]),
        ToolMessage("tool error", tool_call_id="1"),
    ]
    _patch_agent(monkeypatch, messages)
    assert agent.run_agent("o_run_fail", "测试") == agent.AGENT_FAILURE
    assert db.get_chat_history("o_run_fail") == []


def test_run_agent_accepts_last_ai_message(iso, monkeypatch):
    _patch_agent(monkeypatch, [HumanMessage("测试"), AIMessage("ok")])
    assert agent.run_agent("o_run_ok", "测试") == "ok"
    history = db.get_chat_history("o_run_ok")
    assert history[-1]["content"] == "ok"


def test_run_agent_warns_when_record_succeeded_but_no_final_ai(iso, monkeypatch):
    messages = [
        HumanMessage("测试"),
        AIMessage(
            "",
            tool_calls=[
                {"name": "record_transactions", "args": {}, "id": "1"}
            ],
        ),
        ToolMessage(
            "✓ 已记 1 笔", tool_call_id="1", name="record_transactions"
        ),
        AIMessage(""),
    ]
    _patch_agent(monkeypatch, messages)
    out = agent.run_agent("o_run_warn", "测试")
    assert out != agent.AGENT_FAILURE
    assert "已经记上" in out
    assert db.get_chat_history("o_run_warn")[-1]["content"] == out
