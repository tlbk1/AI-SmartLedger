"""兼容旧启动路径：systemd 仍以 `uvicorn main:app` 启动。

新入口是 `ai_smartledger.main:app`；更新 systemd 单元后可删除本文件。
"""
from ai_smartledger.main import app  # noqa: F401
