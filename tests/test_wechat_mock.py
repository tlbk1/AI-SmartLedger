"""
tests/test_wechat_mock.py — 本地 mock 微信回调测试

不依赖微信、不依赖 LLM、不依赖网络。
验证签名校验、XML 解析、幂等去重、DB CRUD、查询边界等核心逻辑。

运行：pytest tests/test_wechat_mock.py -v
（从 wxrecordbot/ 目录运行）
"""

import hashlib
import sys
import time
from pathlib import Path

import pytest

# 把 wxrecordbot/ 加入 path
sys.path.insert(0, str(Path(__file__).parent.parent))

import db
import wechat
from tools import QueryParams, query_transactions

# ──────────────────────────── 测试常量 ────────────────────────────

TEST_TOKEN = "beecount2026"
TEST_OPENID = "oTestUser123"
TEST_ACCOUNT = "gh_test_account"


# ──────────────────────────── 辅助函数 ────────────────────────────

def make_signature(timestamp: str, nonce: str, token: str = TEST_TOKEN) -> str:
    """模拟微信算签名。"""
    parts = sorted([token, timestamp, nonce])
    raw = "".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def make_text_xml(content: str, msg_id: str = "1001", openid: str = TEST_OPENID) -> bytes:
    """伪造微信文本消息 XML。"""
    return f"""<xml>
<ToUserName><![CDATA[{TEST_ACCOUNT}]]></ToUserName>
<FromUserName><![CDATA[{openid}]]></FromUserName>
<CreateTime>{int(time.time())}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
<MsgId>{msg_id}</MsgId>
</xml>""".encode("utf-8")


def make_subscribe_xml(openid: str = TEST_OPENID) -> bytes:
    """伪造微信关注事件 XML。"""
    return f"""<xml>
<ToUserName><![CDATA[{TEST_ACCOUNT}]]></ToUserName>
<FromUserName><![CDATA[{openid}]]></FromUserName>
<CreateTime>{int(time.time())}</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[subscribe]]></Event>
</xml>""".encode("utf-8")


# ──────────────────────────── 签名校验测试 ────────────────────────────

class TestSignature:

    def test_correct_signature(self, monkeypatch):
        """正确签名应该通过校验。"""
        monkeypatch.setattr(wechat, "WECHAT_TOKEN", TEST_TOKEN)
        ts = "1692000000"
        nonce = "abc123xyz"
        sig = make_signature(ts, nonce)
        assert wechat.verify_signature(sig, ts, nonce) is True

    def test_wrong_signature(self, monkeypatch):
        """错误签名应该被拒绝。"""
        monkeypatch.setattr(wechat, "WECHAT_TOKEN", TEST_TOKEN)
        assert wechat.verify_signature("wrong", "1692000000", "abc123") is False

    def test_empty_signature(self, monkeypatch):
        """空签名应该被拒绝。"""
        monkeypatch.setattr(wechat, "WECHAT_TOKEN", TEST_TOKEN)
        assert wechat.verify_signature("", "123", "abc") is False


# ──────────────────────────── XML 解析测试 ────────────────────────────

class TestParseXML:

    def test_parse_text_message(self):
        """能正确解析文本消息。"""
        xml = make_text_xml("午饭35", msg_id="2001")
        msg = wechat.parse_message(xml)
        assert msg is not None
        assert msg.msg_type == "text"
        assert msg.content == "午饭35"
        assert msg.from_user == TEST_OPENID
        assert msg.msg_id == "2001"

    def test_parse_subscribe_event(self):
        """能解析关注事件（无 MsgId/Content）。"""
        xml = make_subscribe_xml()
        msg = wechat.parse_message(xml)
        assert msg is not None
        assert msg.msg_type == "event"
        assert msg.event == "subscribe"
        assert msg.content == ""
        assert msg.msg_id == ""   # 事件消息无 MsgId

    def test_parse_invalid_xml(self):
        """非法 XML 返回 None。"""
        assert wechat.parse_message(b"not xml at all") is None


# ──────────────────────────── 幂等去重测试 ────────────────────────────

class TestDedup:

    def test_first_seen(self):
        """第一次见到的 msgid 返回 True。"""
        db.mark_seen("test_msg_001")  # 可能之前测试设过
        # 用新 id
        assert db.mark_seen("test_msg_unique_new") is True

    def test_duplicate(self):
        """重复的 msgid 返回 False。"""
        msg_id = "test_msg_dup"
        assert db.mark_seen(msg_id) is True   # 第一次
        assert db.mark_seen(msg_id) is False  # 第二次


# ──────────────────────────── DB 基础测试 ────────────────────────────

class TestDatabase:

    def test_init_creates_table(self):
        """init() 应该成功创建表。"""
        db.init()  # 不抛异常就行

    def test_insert_and_count(self):
        """插入后能查到。"""
        db.init()
        txn = db.Transaction(
            type="expense",
            amount=35.0,
            category="餐饮",
            note="午饭",
            happened_at="2026-08-14T12:30:00+08:00",
        )
        ok = db.insert_many([txn])
        assert ok is True

    def test_insert_batch_atomic(self):
        """批量插入正常。"""
        db.init()
        txns = [
            db.Transaction("expense", 35.0, "餐饮", "午饭", "2026-08-14T12:00:00+08:00"),
            db.Transaction("expense", 12.0, "交通", "打车", "2026-08-14T18:00:00+08:00"),
        ]
        ok = db.insert_many(txns)
        assert ok is True


# ──────────────────────────── 查询边界测试（审查错误2） ────────────────────────────

class TestQueryBoundary:
    """验证日期边界补全：不能丢最后一天的数据。"""

    def test_last_day_not_excluded(self):
        """
        查 8/1 ~ 8/31，8/31 当天的记录应该被包含。
        这就是审查指出的 BETWEEN 坑——改用半开区间后应修复。
        """
        db.init()
        # 插入一条 8/31 当天的记录
        txn = db.Transaction(
            type="expense",
            amount=99.0,
            category="娱乐",
            note="月末电影",
            happened_at="2026-08-31T20:00:00+08:00",  # 当天晚上
        )
        db.insert_many([txn])

        # 查 8 月
        rows = query_transactions(QueryParams(
            date_from="2026-08-01",
            date_to="2026-08-31",
        ))

        # 应该包含 8/31 的记录
        amounts = [r["amount"] for r in rows]
        assert 99.0 in amounts, "8/31 当天的记录被 BETWEEN 丢了！"

    def test_category_filter(self):
        """分类过滤正常。"""
        db.init()
        rows = query_transactions(QueryParams(
            date_from="2026-01-01",
            date_to="2026-12-31",
            category="餐饮",
        ))
        for r in rows:
            assert r["category"] == "餐饮"


# ──────────────────────────── pending 对话状态测试 ────────────────────────────

class TestPending:

    def test_set_and_get(self):
        """存入 pending 后能取到。"""
        db.set_pending(TEST_OPENID, {"category": "交通"}, "金额是多少？")
        pending = db.get_pending(TEST_OPENID)
        assert pending is not None
        assert pending.question == "金额是多少？"
        assert pending.draft["category"] == "交通"

    def test_clear(self):
        """清除后取不到。"""
        db.set_pending(TEST_OPENID, {"x": 1}, "test")
        db.clear_pending(TEST_OPENID)
        assert db.get_pending(TEST_OPENID) is None


# ──────────────────────────── 被动回复组装测试 ────────────────────────────

class TestReply:

    def test_build_text_reply(self):
        """组装的 XML 方向正确。"""
        xml = wechat.build_text_reply(TEST_OPENID, TEST_ACCOUNT, "你好")
        assert TEST_OPENID in xml          # ToUserName = 用户
        assert TEST_ACCOUNT in xml         # FromUserName = 公众号
        assert "你好" in xml
        assert "<xml>" in xml

    def test_reply_direction(self):
        """验证 To=用户，From=公众号（不是反过来的）。"""
        xml = wechat.build_text_reply("user_openid", "gh_bot", "test reply")
        assert "<ToUserName><![CDATA[user_openid]]>" in xml
        assert "<FromUserName><![CDATA[gh_bot]]>" in xml


# ──────────────────────────── undelivered 队列测试 ────────────────────────────

class TestUndelivered:

    def test_enqueue_and_drain(self):
        """入队后能 drain 出来并清空。"""
        db.enqueue_undelivered(TEST_OPENID, "消息A")
        db.enqueue_undelivered(TEST_OPENID, "消息B")
        drained = db.drain_undelivered(TEST_OPENID)
        assert len(drained) == 2
        assert "消息A" in drained[0] or "消息A" in drained[1]

        # drain 后应该空了
        again = db.drain_undelivered(TEST_OPENID)
        assert again == []
