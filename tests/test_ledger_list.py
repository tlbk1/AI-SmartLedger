"""账本列表展示格式：带人数、默认不显示口令、固定建账本提示。"""
import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import ai_smartledger.db as db
from ai_smartledger.agents import fast_paths
from ai_smartledger.agents import react_agent as agent
from ai_smartledger.agents.react_agent import _extract_reply
from ai_smartledger.tools.builtin.ledgers import format_ledger_list


@pytest.fixture
def iso(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(db, "DB_PATH", pathlib.Path(tmp) / "t.db")
    db.init()
    yield
    shutil.rmtree(tmp, ignore_errors=True)


def _ledger(**kw):
    base = {
        "id": 1,
        "name": "我的账本",
        "is_current": True,
        "is_deleted": False,
        "role": "owner",
        "invite_code": "abcd",
        "member_count": 1,
    }
    base.update(kw)
    return base


def test_single_ledger_exact():
    text = format_ledger_list([_ledger()])
    assert text == (
        "你的账本：\n"
        "- #1 我的账本（当前 · 1 人）\n"
        "要再建一个，说「建个账本 XXX」就行。"
    )
    assert "口令" not in text
    assert "默认" not in text
    assert "成员：" not in text


def test_multi_and_non_current():
    text = format_ledger_list(
        [_ledger(), _ledger(id=2, name="家用", is_current=False, member_count=3)]
    )
    assert "- #1 我的账本（当前 · 1 人）" in text
    assert "- #2 家用（3 人）" in text


def test_deleted_marker():
    text = format_ledger_list([_ledger(is_deleted=True, member_count=2)])
    assert "- #1 我的账本（当前 · 2 人 · 已删除·只读）" in text


def test_invite_hidden_by_default_shown_on_request():
    ledgers = [_ledger()]
    assert "口令" not in format_ledger_list(ledgers)
    assert "口令 abcd" in format_ledger_list(ledgers, show_invite=True)
    # 非 owner 即使 show_invite 也不显示口令
    assert "口令" not in format_ledger_list([_ledger(role="member")], show_invite=True)


def test_db_member_count(iso):
    db.get_or_create_user("o_owner")
    ok, code = db.create_ledger("o_owner", "家用")
    assert ok, code
    lid = db.get_user_ledger_id("o_owner")

    db.apply_join("o_friend", code)
    pending = db.list_pending_joins("o_owner", lid)
    assert pending
    ok, msg = db.approve_join("o_owner", pending[0]["nickname"], lid)
    assert ok, msg

    counts = {l["id"]: l["member_count"] for l in db.get_my_ledgers("o_owner")}
    assert counts[lid] == 2


def test_tool_output_defaults(iso):
    db.get_or_create_user("o_tool")
    tool = {t.name: t for t in agent.make_tools("o_tool")}["get_my_ledgers"]
    assert tool.return_direct is False

    out = tool.invoke({})  # show 有默认值
    assert "你的账本：" in out
    assert "（当前 · 1 人）" in out
    assert "口令" not in out
    assert "建个账本" in out

    assert "口令" in tool.invoke({"show_invite": True})
    assert "口令" not in tool.invoke({"show": "y", "show_invite": None})


def test_tool_no_ledger_message_is_user_facing(iso, monkeypatch):
    db.get_or_create_user("o_empty")
    monkeypatch.setattr(db, "get_my_ledgers", lambda openid: [])
    tool = {t.name: t for t in agent.make_tools("o_empty")}["get_my_ledgers"]
    out = tool.invoke({})
    assert "create_ledger" not in out
    assert "join_ledger" not in out
    assert "建个账本" in out


def test_run_agent_ledger_list_fast_path(iso):
    db.get_or_create_user("o_fast")
    assert agent.run_agent("o_fast", "我的账本") == (
        "你的账本：\n"
        "- #1 我的账本（当前 · 1 人）\n"
        "要再建一个，说「建个账本 XXX」就行。"
    )


def test_run_agent_invite_fast_path(iso):
    db.get_or_create_user("o_fast2")
    [ledger] = db.get_my_ledgers("o_fast2")
    out = agent.run_agent("o_fast2", "口令")
    assert f"口令 {ledger['invite_code']}" in out
    assert "建个账本" in out


def test_fast_path_pending_hint(iso):
    db.get_or_create_user("o_owner")
    ok, code = db.create_ledger("o_owner", "家用")
    assert ok, code
    db.apply_join("o_friend", code)
    out = agent.run_agent("o_owner", "我的账本")
    assert "加入申请" in out


def test_compound_message_not_fast_path():
    assert fast_paths.ledger_fast_reply("o_x", "我的账本和成员") is None


def test_extract_reply_skips_tool_messages():
    messages = [
        HumanMessage("我的账本"),
        AIMessage("", tool_calls=[{"name": "get_my_ledgers", "args": {}, "id": "1"}]),
        ToolMessage("内部列表", tool_call_id="1"),
        AIMessage("你的账本：..."),
    ]
    assert _extract_reply(messages) == "你的账本：..."


def test_extract_reply_ignores_tool_only():
    assert _extract_reply([ToolMessage("Error invoking tool", tool_call_id="1")]) == ""


def test_extract_reply_uses_last_ai_message():
    messages = [HumanMessage("你好"), AIMessage("你好呀")]
    assert _extract_reply(messages) == "你好呀"
