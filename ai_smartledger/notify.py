"""notify — 客服消息推送慢路径（spec 006 自 main.py 迁入，解 agent→main 循环依赖）。

推送失败（超 48h 窗口等）→ 入未送达队列，用户下一条消息到达时补投。
在事件循环外线程执行（main 的 asyncio.to_thread / 工具闭包的守护线程）。
"""
import logging

from . import db, wechat

logger = logging.getLogger(__name__)


def push_customer(openid: str, text: str):
    """同步推送客服消息（慢路径兜底）。推送失败入队，下次补投。"""
    try:
        ok = wechat.send_customer_message(openid, text)
        if not ok:
            db.enqueue_undelivered(openid, text)
            logger.info("客服消息未送达，已入队: openid=%s", openid)
    except Exception:
        logger.exception("客服消息推送异常: openid=%s", openid)
