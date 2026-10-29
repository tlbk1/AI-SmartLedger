"""core/llm.py — 网关配置唯一来源（spec 006）。

主链（langchain ChatOpenAI）与兜底链（openai SDK）连同一网关；
base_url / api_key 的解析只写这一处，模型名统一取 core/config.DEFAULT_MODEL
（其内部已含 LLM_MODEL 优先逻辑），调用方不再各自重复默认值。
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayConfig:
    """一次网关调用的连接参数。"""

    base_url: str
    api_key: str
    model: str


def gateway() -> GatewayConfig:
    """解析网关配置。缺省值与拆分前两处重复解析逐字一致（旧默认值修正为独立变更）。"""
    from .config import DEFAULT_MODEL

    return GatewayConfig(
        base_url=os.environ.get("LLM_BASE_URL", "https://ccc.szprize.cn/v1"),
        api_key=os.environ["LLM_API_KEY"],
        model=DEFAULT_MODEL,
    )
