import logging
import os
import stat

from ai_smartledger import main


def test_rotated_log_stays_private(tmp_path):
    """轮转后新文件也必须保持 640（含 openid/消息内容，不能让同机其他用户读）。"""
    path = tmp_path / "app.log"
    handler = main._PrivateRotatingFileHandler(
        str(path), maxBytes=50, backupCount=1
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test_rotated_log")
    logger.handlers[:] = []
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("x" * 100)
    handler.close()

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640
    assert stat.S_IMODE(os.stat(str(path) + ".1").st_mode) == 0o640
