"""
main.py — FastAPI 入口 + /wechat 路由

设计依据：
- 需求文档 14.5: GET 签名校验返回 echostr；POST 签名校验后处理消息
- 需求文档 14.1: 幂等去重
- 需求文档 14.2: 优先被动回复（5s 内），超时走客服消息
- 审查修正 1: shield + done_callback 保证超时后后台结果仍推送
- 审查遗漏 1: 事件消息（subscribe）无 MsgId/Content，需单独处理
- 需求文档 14.7: 路由层 async，处理逻辑 asyncio.to_thread 包进线程池
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

import db
import graph
import wechat

# ──────────────────────────── 日志配置 ────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ──────────────────────────── 环境变量 ────────────────────────────

load_dotenv()  # 读取 .env

WECHAT_TIMEOUT = 4.5  # 秒；给微信 5 秒约束留 0.5 秒余量


# ──────────────────────────── FastAPI lifespan ────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化。"""
    db.init()
    logger.info("数据库已初始化")
    yield
    logger.info("服务关闭")


app = FastAPI(lifespan=lifespan)


# ──────────────────────────── GET /wechat（一次性握手） ────────────────────────────

@app.get("/wechat")
async def wechat_verify(request: Request):
    """
    微信配置时的 URL 验证（需求 14.5）。
    微信发 GET 带 signature/timestamp/nonce/echostr，
    校验通过后原样返回 echostr。
    """
    params = request.query_params
    signature = params.get("signature", "")
    timestamp = params.get("timestamp", "")
    nonce = params.get("nonce", "")
    echostr = params.get("echostr", "")

    if wechat.verify_signature(signature, timestamp, nonce):
        logger.info("GET 签名校验通过")
        return PlainTextResponse(echostr)
    else:
        logger.warning("GET 签名校验失败")
        return PlainTextResponse("Forbidden", status_code=403)


# ──────────────────────────── POST /wechat（消息处理） ────────────────────────────

@app.post("/wechat")
async def wechat_message(request: Request):
    """
    用户消息处理核心路径。
    审查修正 1：shield + done_callback 保证超时后后台结果仍能推送。
    """
    body = await request.body()
    params = request.query_params
    signature = params.get("signature", "")
    timestamp = params.get("timestamp", "")
    nonce = params.get("nonce", "")

    # ── [A] 签名校验 ──
    if not wechat.verify_signature(signature, timestamp, nonce):
        logger.warning("POST 签名校验失败")
        return PlainTextResponse("Forbidden", status_code=403)

    # ── [B] XML 解析 ──
    msg = wechat.parse_message(body)
    if msg is None:
        logger.warning("XML 解析失败")
        return PlainTextResponse("")

    # ── 事件消息闸门（审查遗漏 1）──
    if msg.msg_type != "text":
        if msg.msg_type == "event" and msg.event == "subscribe":
            logger.info("用户关注: %s", msg.from_user)
            reply = "👋 欢迎关注！直接发消息就能记账，比如「午饭35」"
            return Response(
                content=wechat.build_text_reply(msg.from_user, msg.to_user, reply),
                media_type="application/xml",
            )
        # 其他事件类型 / 图片语音等 → 回空串
        return PlainTextResponse("")

    # ── [C] 幂等去重 ──
    msg_id = msg.msg_id or ""  # event 无 MsgId 但前面已挡住
    if not db.mark_seen(msg_id):
        logger.info("重复消息已丢弃: msgid=%s", msg_id)
        return PlainTextResponse("")  # 重复消息回空串

    openid = msg.from_user
    our_account = msg.to_user
    content = msg.content

    logger.info("处理消息: openid=%s content=%s msgid=%s", openid, content[:30], msg_id)

    # ── [E] 处理 + 超时降级（审查修正 1）──
    # 正确顺序：先 create_task(to_thread(...))，等待时再 wait_for(shield(task))
    # 注意：不能 create_task(shield(...))——shield 返回 Future 不是协程
    # shield 让 wait_for 超时后放弃等待但不取消 task，线程跑完触发 done_callback
    task = asyncio.create_task(
        asyncio.to_thread(graph.process_message, openid, content)
    )

    try:
        # shield(task)：超时后 wait_for 取消的是 shield 包装层，task 本体继续跑
        reply_text = await asyncio.wait_for(asyncio.shield(task), timeout=WECHAT_TIMEOUT)

        # 快路径成功：塞回 HTTP 响应
        logger.info("快路径回复: %s", reply_text[:30])
        xml_reply = wechat.build_text_reply(openid, our_account, reply_text)
        return Response(content=xml_reply, media_type="application/xml")

    except asyncio.TimeoutError:
        # 超时：返回空串占位，后台线程完成后走客服消息
        logger.warning("处理超时 %ss，走慢路径: msgid=%s", WECHAT_TIMEOUT, msg_id)

        def _on_done(t: asyncio.Task):
            """后台线程完成时触发：推送客服消息。"""
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error("后台任务异常: %s", exc, exc_info=exc)
                return
            reply_text = t.result()
            if reply_text:
                ok = wechat.send_customer_message(openid, reply_text)
                if not ok:
                    # 推送失败（超48h等）→ 入队下次补发
                    db.enqueue_undelivered(openid, reply_text)
                    logger.info("客服消息未送达，已入队: openid=%s", openid)

        task.add_done_callback(_on_done)
        return PlainTextResponse("")

    except Exception as e:
        # 未捕获异常兜底
        logger.error("未捕获异常: %s", e, exc_info=True)
        reply = "出了点问题，请重试。"
        xml_reply = wechat.build_text_reply(openid, our_account, reply)
        return Response(content=xml_reply, media_type="application/xml")


# ──────────────────────────── 健康检查 ────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ──────────────────────────── 入口 ────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
