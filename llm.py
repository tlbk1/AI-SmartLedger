"""
llm.py — LLM 调用层

封装所有跟公司中转站（ccc.szprize.cn/v1）的交互。
给上层提供四个语义化函数。

设计依据：
- 需求文档 11.2: OpenAI SDK + base_url 指向中转站
- 需求文档 11.4: 分类清单 + 关键词写进 prompt
- 需求文档 14.6: LLM 格式错误重试1次
- 需求文档 14.6: 分类不确定归"其他"
- 技术方案 3.4: 用 tool calling 强制结构化输出
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from openai import OpenAI

from tools import TOOL_SCHEMA, QueryParams

# 与 wechat.py 同理：模块级就要读到 .env 里的 LLM_API_KEY / LLM_MODEL，
# 否则被其他模块先 import 时 os.environ 为空，直接 KeyError。
load_dotenv()

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")

# ──────────────────────────── LLM 客户端 ────────────────────────────

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """懒初始化 OpenAI 客户端（指向公司中转站）。"""
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.environ.get("LLM_BASE_URL", "https://ccc.szprize.cn/v1"),
            api_key=os.environ["LLM_API_KEY"],
        )
    return _client


def _model() -> str:
    return os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")


# ──────────────────────────── Prompt 常量 ────────────────────────────

SYSTEM_PROMPT = """\
你是一个微信记账机器人的核心 AI。你的工作是把用户的自然语言消息处理成结构化结果。

当前时间（Asia/Shanghai）：{now}

预设分类清单（只能用这些，拿不准归"其他"）：
- 支出（7类）：餐饮、交通、购物、居住、娱乐、医疗、其他
  关键词参考：餐饮(吃饭/外卖/零食/奶茶) 交通(打车/地铁/公交/加油) \
购物(日用/衣服/数码) 居住(房租/水电/物业) 娱乐(电影/游戏/聚餐) \
医疗(看病/买药) 其他(人情/教育/话费/礼物)
- 收入（3类）：工资、外快、其他
  关键词参考：工资(月薪/发工资) 外快(兼职/报销/投资收益/红包)

重要规则：
1. 金额默认单位是"元"
2. 时间默认"现在"，支持"昨天""上周五"等相对时间
3. 高频分类单独列，低频归"其他"，宁可贵在"其他"也不要错分
4. 输出必须是合法 JSON，不要多余文本
"""


# ──────────────────────────── 数据结构 ────────────────────────────

@dataclass
class ExtractedTxn:
    """单笔抽取结果。"""
    type: str               # expense / income
    amount: float
    category: str
    note: str
    happened_at: str        # ISO 字符串


@dataclass
class ExtractionResult:
    """抽取整体结果。三种情况。"""
    ok: bool = False
    txns: list[ExtractedTxn] = None
    need_amount: bool = False       # 缺金额，需要反问
    question: str = ""              # 反问的问题
    draft: dict = None              # 半成品（配合反问）
    error: str = ""


# ──────────────────────────── ① 意图分类 ────────────────────────────

def classify_intent(content: str, now_str: str) -> Literal["record", "query", "chat"]:
    """
    让 LLM 判断用户消息属于哪种意图。
    用 tool calling 强制三选一，比纯文本输出稳。
    """
    client = _get_client()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "set_intent",
                "description": "设置当前用户消息的意图分类",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": ["record", "query", "chat"],
                            "description": "record=记一笔账, query=查账, chat=闲聊/其他",
                        }
                    },
                    "required": ["intent"],
                },
            },
        }
    ]

    few_shot = (
        "示例判断：\n"
        '  "午饭35" → record\n'
        '  "打车12，奶茶15" → record\n'
        '  "这个月餐饮花了多少" → query\n'
        '  "最近5笔" → query\n'
        '  "你好" → chat\n'
        '  "今天花了好多" → record（无具体金额，会触发反问）\n'
    )

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(now=now_str)},
                {"role": "system", "content": few_shot},
                {"role": "user", "content": content},
            ],
            tools=tools,
            # 注意：deepseek-v4-flash/pro 是 thinking mode，
            # 不支持强制 tool_choice，只能给 tools 数组让模型自己决定。
            temperature=0,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            args = json.loads(msg.tool_calls[0].function.arguments)
            return args.get("intent", "chat")
        # 模型没走 tool calling，尝试从文本解析 JSON
        text = msg.content or ""
        if text.strip():
            try:
                # 提取第一个 JSON 对象
                start, end = text.find("{"), text.rfind("}")
                if start >= 0 and end > start:
                    args = json.loads(text[start:end + 1])
                    return args.get("intent", "chat")
            except json.JSONDecodeError:
                pass
        return "chat"
    except Exception as e:
        logger.warning("意图分类失败，降级为 chat: %s", e)
        return "chat"


# ──────────────────────────── ② 结构化抽取 ────────────────────────────

def extract_transactions(content: str, now_str: str) -> ExtractionResult:
    """
    从用户消息抽取账单。
    返回三种情况：成功 / 缺金额需反问 / 错误。
    失败时重试 1 次（需求 14.6）。
    """
    for attempt in range(2):  # 最多 2 次（初始 + 1 次重试）
        result = _extract_once(content, now_str)
        if result.ok or result.need_amount:
            return result
        if attempt == 0:
            logger.warning("抽取失败，重试1次: %s", result.error)

    return result  # 两次都失败


def _extract_once(content: str, now_str: str) -> ExtractionResult:
    """单次抽取。"""
    client = _get_client()

    extract_prompt = (
        "请解析用户消息，提取所有账单记录。\n"
        "规则：\n"
        "1. 如果消息包含明确的金额，抽取为账单（一笔或多笔）\n"
        "2. 如果看起来要记账但没有具体金额，返回 need_amount=true\n"
        "3. 金额默认单位元\n"
        "4. happened_at 用 ISO 格式（如 2026-08-14T12:30:00+08:00）\n"
        "5. 分类只能用预设清单里的\n\n"
        "返回 JSON 格式：\n"
        '{"ok": true, "txns": [{"type":"expense","amount":35.0,"category":"餐饮",'
        '"note":"午饭","happened_at":"2026-08-14T12:30:00+08:00"}]}\n'
        "或：\n"
        '{"ok": false, "need_amount": true, "question": "金额是多少？",'
        ' "draft": {"type":"expense","category":"交通","note":"打车回家"}}\n'
    )

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(now=now_str)},
                {"role": "system", "content": extract_prompt},
                {"role": "user", "content": content},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        data = json.loads(raw)

        if data.get("ok"):
            txns = [
                ExtractedTxn(
                    type=t["type"],
                    amount=float(t["amount"]),
                    category=t["category"],
                    note=t.get("note", ""),
                    happened_at=t["happened_at"],
                )
                for t in data["txns"]
            ]
            return ExtractionResult(ok=True, txns=txns)

        elif data.get("need_amount"):
            return ExtractionResult(
                ok=False,
                need_amount=True,
                question=data.get("question", "金额是多少？"),
                draft=data.get("draft", {}),
            )

        else:
            return ExtractionResult(ok=False, error=data.get("error", "未知格式"))

    except (json.JSONDecodeError, KeyError) as e:
        return ExtractionResult(ok=False, error=f"格式错误: {e}")
    except Exception as e:
        return ExtractionResult(ok=False, error=f"LLM 调用失败: {e}")


# ──────────────────────────── ③ 查询参数提取 ────────────────────────────

def extract_query_params(content: str, now_str: str) -> Optional[QueryParams]:
    """
    用 tool calling 让 LLM 产出结构化查询参数（安全核心）。
    LLM 永远不写 SQL，只产出 date_from/date_to/category 等参数。
    """
    client = _get_client()

    guidance = (
        "请根据用户的查询消息，调用 query_transactions 工具。\n"
        "时间理解示例：\n"
        '  "这个月" → date_from=当月1日, date_to=当月最后一天\n'
        '  "上周" → 上周一到上周日\n'
        '  "最近5笔" → date_from=30天前, date_to=今天, limit=5\n'
        '  "本周花了多少" → 本周一到今天\n'
    )

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(now=now_str)},
                {"role": "system", "content": guidance},
                {"role": "user", "content": content},
            ],
            tools=[TOOL_SCHEMA],
            # 同 classify：thinking mode 不支持 tool_choice 强制
            temperature=0,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            args = json.loads(msg.tool_calls[0].function.arguments)
        else:
            # 兜底：从文本解析 JSON
            text = msg.content or ""
            start, end = text.find("{"), text.rfind("}")
            args = json.loads(text[start:end + 1]) if start >= 0 and end > start else {}
        return QueryParams(
            date_from=args["date_from"],
            date_to=args["date_to"],
            category=args.get("category"),
            type_filter=args.get("type_filter"),
            limit=args.get("limit", 20),
        )
    except Exception as e:
        logger.warning("查询参数提取失败: %s", e)
        return None


# ──────────────────────────── ④ 查询结果总结 ────────────────────────────

def summarize_query_result(rows: list[dict], original_question: str, now_str: str) -> str:
    """
    把 SQL 查询结果 + 用户原问题喂给 LLM，总结成自然语言。
    """
    if not rows:
        return "没有查到相关记录。"

    client = _get_client()

    # 计算汇总
    total = sum(r["amount"] for r in rows)
    summary_prompt = (
        f"用户问了：「{original_question}」\n"
        f"查询到 {len(rows)} 条记录，总金额 ¥{total:.2f}。\n"
        f"明细：\n{json.dumps(rows, ensure_ascii=False, indent=2)}\n\n"
        "请用简洁的中文总结这些数据，给用户一个易读的回复。"
        "格式参考：「本月餐饮支出 ¥820，共 12 笔」\n"
        "如果记录较多，列前几条明细 + 汇总。"
    )

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(now=now_str)},
                {"role": "user", "content": summary_prompt},
            ],
            temperature=0.3,  # 总结允许略微灵活
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("查询结果总结失败: %s", e)
        # 降级：返回原始数据
        return f"查到 {len(rows)} 条记录，总金额 ¥{total:.2f}。（AI 总结暂时不可用）"


# ──────────────────────────── ⑤ 兜底闲聊 ────────────────────────────

def chat_reply(content: str, now_str: str) -> str:
    """兜底闲聊回复。"""
    client = _get_client()
    chat_prompt = (
        "你是一个友好的微信记账助手。用户没有说要记账也没有问账时，简短友好地回复。"
        "可以提醒用户怎么使用：「直接发消息就能记账，比如『午饭35』」。"
    )

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(now=now_str)},
                {"role": "system", "content": chat_prompt},
                {"role": "user", "content": content},
            ],
            temperature=0.5,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("闲聊回复失败: %s", e)
        return "你好！直接发消息就能记账，比如「午饭35」。"
