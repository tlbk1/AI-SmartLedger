"""config.py — 全局共享常量（避免各模块硬编码重复值）。"""
import os

# 默认 LLM 模型（若 .env 未设 LLM_MODEL 时使用）。
# 之前 agent.py 写 deepseek-v4-flash、llm.py 写 claude-haiku-4-5，不一致；统一到这里。
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
