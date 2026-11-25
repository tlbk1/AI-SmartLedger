import pytest

import llm
from prompts import HELP_TEXT


@pytest.mark.parametrize("text", ["帮助", "怎么用", "有什么功能"])
def test_chat_reply_returns_help_without_llm(monkeypatch, text):
    def boom():
        raise AssertionError("LLM 不应被调用")

    monkeypatch.setattr(llm, "_get_client", boom)
    assert llm.chat_reply(text, "2026-11-25T12:00:00+08:00") == HELP_TEXT


def test_chat_reply_non_help_still_calls_llm(monkeypatch):
    """普通聊天里出现「怎么用」不能误判成帮助命令。"""
    def boom():
        raise AssertionError("LLM 应被调用")

    monkeypatch.setattr(llm, "_get_client", boom)
    with pytest.raises(AssertionError, match="LLM 应被调用"):
        llm.chat_reply("这个月怎么用了这么多", "2026-11-25T12:00:00+08:00")
