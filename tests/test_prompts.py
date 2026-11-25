from ai_smartledger.core.prompts import GREETING_TEXT, HELP_TEXT, build_agent_prompt


def test_agent_prompt_contains_greeting_and_help():
    prompt = build_agent_prompt("2026-11-25T12:00:00+08:00")

    assert GREETING_TEXT in prompt
    assert HELP_TEXT in prompt
    assert "2026-11-25T12:00:00+08:00" in prompt
    assert "{greeting}" not in prompt
    assert "{help_text}" not in prompt


def test_agent_prompt_includes_copy_rules():
    prompt = build_agent_prompt("2026-11-25T12:00:00+08:00")

    assert "结尾的建账本提示必须保留" in prompt
    assert "show_invite=True" in prompt
    assert "微信不渲染 Markdown" in prompt
