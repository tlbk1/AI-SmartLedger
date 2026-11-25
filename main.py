"""main.py — FastAPI 入口 + /wechat 路由：微信协议接入与超时降级调度。"""

import asyncio
import logging
import logging.handlers
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

import db
import graph
import wechat
from prompts import WELCOME_BACK_TEXT

# ──────────────────────────── 日志配置 ────────────────────────────

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """日志含 openid/消息内容，每次打开（含轮转后）都收紧到 640。"""

    def _open(self):
        f = super()._open()
        try:
            os.chmod(self.baseFilename, 0o640)
        except OSError:
            pass
        return f


def _setup_logging():
    """同时输出到 stderr（journald）和 logs/app.log（无需 root 即可查看）。"""
    handlers = [logging.StreamHandler()]
    try:
        os.makedirs(LOG_DIR, mode=0o750, exist_ok=True)
        fh = _PrivateRotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handlers.append(fh)
    except OSError as e:
        logging.getLogger(__name__).warning("无法创建文件日志 %s: %s", LOG_FILE, e)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    # 第三方 HTTP 客户端默认在 INFO 打每条请求，会淹没业务日志
    for noisy in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger(__name__)

# ──────────────────────────── 环境变量 ────────────────────────────

load_dotenv()  # 读取 .env

WECHAT_TIMEOUT = 4.5  # 秒；给微信 5 秒约束留 0.5 秒余量


def _do_push_customer(openid: str, text: str):
    """同步推送客服消息（在事件循环外的线程执行）。推送失败则入队待补发。"""
    try:
        ok = wechat.send_customer_message(openid, text)
        if not ok:
            db.enqueue_undelivered(openid, text)
            logger.info("客服消息未送达，已入队: openid=%s", openid)
    except Exception:
        logger.exception("客服消息推送异常: openid=%s", openid)


def _build_welcome(openid: str) -> str:
    """关注/再关注的欢迎语。幂等初始化身份；首关给五要素引导，回来只问候一句。"""
    existed = db.user_exists(openid)
    db.get_or_create_user(openid)
    if existed:
        return WELCOME_BACK_TEXT

    lid = db.get_user_ledger_id(openid)
    info = db.get_ledger_info(lid) if lid else None
    cur = f"「{info['name']}」（#{info['id']}）" if info else "「我的账本」"
    return (
        "👋 欢迎使用记账助手！\n"
        f"· 你在默认账本{cur}，说「午饭 25」就能记一笔\n"
        "· 建账本：「建个账本 家用」，把口令发给家人朋友\n"
        "· 加账本：把别人的口令发给我，申请加入\n"
        "· 起称呼：「我叫小王」"
    )


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
    """微信配置 URL 时的一次性握手：校验签名通过则原样返回 echostr。"""
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
    """用户消息处理核心路径：验签 → 解析 → 事件分流 → 幂等 → 处理 + 超时降级。"""
    t0 = time.monotonic()  # 用于日志记录端到端耗时
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

    # ── 事件消息闸门 ──
    if msg.msg_type != "text":
        if msg.msg_type == "event" and msg.event == "subscribe":
            logger.info("用户关注: %s", msg.from_user)
            reply = _build_welcome(openid=msg.from_user)
            return Response(
                content=wechat.build_text_reply(msg.from_user, msg.to_user, reply),
                media_type="application/xml",
            )
        if msg.msg_type == "event" and msg.event == "unsubscribe":
            # 取关不做任何数据变更（数据与成员关系全保留）；平台限制下也无从推送
            logger.info("用户取关（数据保留）: %s", msg.from_user)
            return PlainTextResponse("")
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

    logger.info("处理消息: openid=%s content=%r msgid=%s", openid, content[:30], msg_id)

    # ── [E] 处理 + 超时降级 ──
    # 顺序关键：先 create_task(to_thread(...))，等待时再 wait_for(shield(task))。
    # shield 让 wait_for 超时后放弃等待但不取消 task，线程跑完触发 done_callback。
    # （不能写成 create_task(shield(...))——shield 返回 Future，不是协程）
    task = asyncio.create_task(
        asyncio.to_thread(graph.process_message, openid, content)
    )

    try:
        # shield(task)：超时后 wait_for 取消的是 shield 包装层，task 本体继续跑
        reply_text = await asyncio.wait_for(asyncio.shield(task), timeout=WECHAT_TIMEOUT)

        # 快路径成功：塞回 HTTP 响应
        elapsed = time.monotonic() - t0
        logger.info("PATH=fast elapsed=%.2fs msgid=%s openid=%s", elapsed, msg_id, openid)
        logger.info("快路径回复 (%.2fs): %r", elapsed, reply_text[:30])
        xml_reply = wechat.build_text_reply(openid, our_account, reply_text)
        return Response(content=xml_reply, media_type="application/xml")

    except asyncio.TimeoutError:
        # 超时：返回空串占位，后台线程完成后走客服消息
        logger.warning(
            "PATH=slow elapsed=%.2fs msgid=%s openid=%s",
            time.monotonic() - t0, msg_id, openid,
        )
        logger.warning("处理超时 %ss，走慢路径: msgid=%s", WECHAT_TIMEOUT, msg_id)

        def _on_done(t: asyncio.Task):
            """后台线程完成时触发：把结果推给用户（丢线程池，不阻塞事件循环）。"""
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "PATH=slow_failed elapsed=%.2fs msgid=%s openid=%s",
                    time.monotonic() - t0, msg_id, openid,
                )
                logger.error("后台任务异常: %s", exc, exc_info=exc)
                return
            reply_text = t.result()
            logger.info(
                "PATH=slow_done elapsed=%.2fs msgid=%s openid=%s reply_len=%d",
                time.monotonic() - t0, msg_id, openid, len(reply_text or ""),
            )
            if reply_text:
                # 异步丢到线程池推送，不阻塞事件循环
                asyncio.create_task(
                    asyncio.to_thread(_do_push_customer, openid, reply_text)
                )

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
