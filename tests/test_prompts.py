from prompts import GREETING_TEXT, HELP_TEXT, build_agent_prompt


def test_agent_prompt_contains_greeting_and_help():
    prompt = build_agent_prompt("2026-11-25T12:00:00+08:00")

    assert GREETING_TEXT in prompt
    assert HELP_TEXT in prompt
    assert "2026-11-25T12:00:00+08:00" in prompt
    assert "{greeting}" not in prompt
    assert "{help_text}" not in prompt
