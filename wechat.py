"""
wechat.py — 微信公众号协议适配层

职责边界：签名校验、XML 解析、被动回复组装、客服消息推送、access_token 管理。
其他模块不碰 XML / 签名 / token 这些协议细节。

设计依据：
- 需求文档 14.5: 签名校验算法 + 明文模式 + XML 字段
- 需求文档 14.2: 优先被动回复，超时走客服消息
- 需求文档 14.8: access_token 内存缓存 + 懒刷新 + 40001 兜底
"""

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from defusedxml import ElementTree as ET  # 防 XML 炸弹

logger = logging.getLogger(__name__)

# ──────────────────────────── 配置 ────────────────────────────

# 必须在读取环境变量前加载 .env——
# 否则当别的模块先 import 本文件时（main.py 的 load_dotenv 还没执行），
# os.environ 里没有 WECHAT_TOKEN，会落到默认值 beecount2026，
# 导致签名永远对不上（用户已踩坑：Token 改为 wwwledger 后仍 403）。
from dotenv import load_dotenv

load_dotenv()

WECHAT_TOKEN = os.environ.get("WECHAT_TOKEN", "beecount2026")
WECHAT_APP_ID = os.environ.get("WECHAT_APP_ID", "")
WECHAT_APP_SECRET = os.environ.get("WECHAT_APP_SECRET", "")


# ──────────────────────────── 签名校验 ────────────────────────────

def verify_signature(signature: str, timestamp: str, nonce: str) -> bool:
    """
    微信签名校验算法（需求 14.5）：
    1. 将 token / timestamp / nonce 字典序排序
    2. 拼接成一个字符串
    3. SHA1
    4. 与 signature 比对
    """
    parts = sorted([WECHAT_TOKEN, timestamp, nonce])
    raw = "".join(parts)
    our_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return our_hash == signature


# ──────────────────────────── XML 解析 ────────────────────────────

@dataclass
class WeChatMessage:
    """解析后的微信消息。"""
    msg_type: str         # text / event / image ...
    from_user: str        # 用户 openid
    to_user: str          # 我们的公众号 gh_xxx
    content: str          # 文本内容（event 消息为空）
    msg_id: str           # 消息ID（event 消息没有，用空串占位）
    event: str            # 事件类型（subscribe / SCAN / ...；非事件为空）
    create_time: int      # 微信的 CreateTime


def parse_message(xml_bytes: bytes) -> Optional[WeChatMessage]:
    """
    解析微信 POST 的 XML 消息体。
    使用 defusedxml 防 XML 炸弹攻击。
    返回 None 表示解析失败。
    """
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        logger.warning("XML 解析失败: %s", e)
        return None

    def _text(tag: str) -> str:
        el = root.find(tag)
        return el.text if el is not None and el.text else ""

    return WeChatMessage(
        msg_type=_text("MsgType"),
        from_user=_text("FromUserName"),
        to_user=_text("ToUserName"),
        content=_text("Content"),
        msg_id=_text("MsgId"),
        event=_text("Event"),
        create_time=int(_text("CreateTime") or "0"),
    )


# ──────────────────────────── 被动回复 ────────────────────────────

def build_text_reply(to_user_openid: str, from_user_account: str, text: str) -> str:
    """
    组装微信被动回复 XML。
    注意方向：发给用户，所以 To=用户openid，From=公众号gh_xxx。
    """
    ts = int(time.time())
    # 用 CDATA 包裹防特殊字符问题
    return (
        f"<xml>"
        f"<ToUserName><![CDATA[{to_user_openid}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user_account}]]></FromUserName>"
        f"<CreateTime>{ts}</CreateTime>"
        f"<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{text}]]></Content>"
        f"</xml>"
    )


# ──────────────────────────── access_token 管理（需求 14.8） ────────────────────────────

_token_cache: dict = {"token": "", "expires_at": 0.0}  # 内存缓存


def get_access_token() -> str:
    """
    获取 access_token，过期前 5 分钟懒刷新。
    有效期 7200 秒，每日获取上限约 2000 次，不能每次请求都去取。
    """
    # 大多数情况直接命中缓存
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 300:
        return _token_cache["token"]

    # 缓存过期或即将过期，重新获取
    url = "https://api.weixin.qq.com/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": WECHAT_APP_ID,
        "secret": WECHAT_APP_SECRET,
    }
    try:
        resp = httpx.get(url, params=params, timeout=10)
        data = resp.json()
    except Exception as e:
        logger.error("获取 access_token 失败: %s", e)
        return _token_cache["token"]  # 返回旧的（可能有）

    if "access_token" in data:
        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = time.time() + data.get("expires_in", 7200)
        logger.info("access_token 已刷新，有效期 %ss", data.get("expires_in", 7200))
        return data["access_token"]
    else:
        logger.error("获取 access_token 返回异常: %s", data)
        return _token_cache["token"]  # 返回旧的


def _force_refresh_token():
    """强制刷新 token（客服消息 40001/42001 时调用）。"""
    _token_cache["expires_at"] = 0.0
    get_access_token()


# ──────────────────────────── 客服消息推送 ────────────────────────────

def send_customer_message(openid: str, text: str) -> bool:
    """
    通过客服消息接口推送文本消息（需求 14.2 超时降级路径）。
    失败时返回 False，调用方应入 undelivered 队列。

    限制：只能发给 48 小时内互动过的用户。
    token 失效（40001/42001）时强制刷新并重试 1 次。
    """
    token = get_access_token()
    if not token:
        logger.error("无可用 access_token，无法推送客服消息")
        return False

    url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token}"
    payload = {
        "touser": openid,
        "msgtype": "text",
        "text": {"content": text},
    }

    def _do_post() -> dict:
        resp = httpx.post(url, json=payload, timeout=10)
        return resp.json()

    try:
        result = _do_post()
        errcode = result.get("errcode", 0)

        # 40001: token 无效；42001: token 过期 → 强制刷新重试
        if errcode in (40001, 42001):
            logger.warning("客服消息返回 %s，强制刷新 token 重试", errcode)
            _force_refresh_token()
            token_new = get_access_token()
            url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token_new}"
            result = _do_post()
            errcode = result.get("errcode", 0)

        if errcode == 0:
            logger.info("客服消息推送成功: openid=%s", openid)
            return True
        else:
            logger.warning("客服消息推送失败: %s", result)
            return False

    except Exception as e:
        logger.error("客服消息推送异常: %s", e)
        return False
