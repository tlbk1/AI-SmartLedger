# Ponytail 评审修复 — Plan

设计：`docs/superpowers/specs/2026-11-25-ponytail-review-fixes-design.md`
执行方式：内联（executing-plans）。每个任务开始前跑 `gate.py check`。

## 任务 1：归一化 CRLF（机械，不改行为）

文件：`.gitattributes`（新建）、所有被 Git 跟踪的文本文件。

1. 新建 `.gitattributes`：

```gitattributes
* text=auto eol=lf
*.bat text eol=crlf
*.ps1 text eol=crlf
```

2. 用 Python 把工作区文本文件转成 LF，跳过 `.bat`/`.ps1` 和二进制（前 8KB 含 NUL）：

```bash
python - <<'PY'
import subprocess, pathlib
files = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
skip = (".bat", ".ps1")
for raw in files:
    if not raw: continue
    p = pathlib.Path(raw.decode())
    if p.suffix.lower() in skip or not p.is_file(): continue
    data = p.read_bytes()
    if b"\0" in data[:8192]: continue
    fixed = data.replace(b"\r\n", b"\n")
    if fixed != data: p.write_bytes(fixed)
PY
```

3. 验证：
   - `git status --short` 只剩 14 个已跟踪改动 + `.gitattributes`
   - `git diff --stat` 与 `git diff --ignore-cr-at-eol --stat` 输出一致
   - `.venv/bin/python -m pytest -q` 全绿（117）

## 任务 2：日志轮转权限（TDD）

测试文件：`tests/test_logging.py`（新建）。

先写失败测试：

```python
import logging, os, stat
import main

def test_rotated_log_stays_private(tmp_path):
    p = tmp_path / "app.log"
    h = main._PrivateRotatingFileHandler(str(p), maxBytes=50, backupCount=1)
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("test_rotated_log")
    lg.handlers[:] = []
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg.info("x" * 100)
    h.close()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o640
    assert stat.S_IMODE(os.stat(str(p) + ".1").st_mode) == 0o640
```

跑测试，确认 AttributeError。然后 `main.py` 实现：

```python
class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def _open(self):
        f = super()._open()
        try:
            os.chmod(self.baseFilename, 0o640)
        except OSError:
            pass
        return f
```

`_setup_logging` 改用它，删除 `os.chmod(LOG_FILE, 0o640)` 那三行。

验证：单测通过 + 全量 pytest。

## 任务 3：提示词构建函数与测试（TDD）

测试文件：`tests/test_prompts.py`（新建），先失败：

```python
from prompts import build_agent_prompt, GREETING_TEXT, HELP_TEXT

def test_agent_prompt_contains_greeting_and_help():
    p = build_agent_prompt("2026-11-25T12:00:00+08:00")
    assert GREETING_TEXT in p
    assert HELP_TEXT in p
    assert "{greeting}" not in p
    assert "{help_text}" not in p
```

`prompts.py` 实现：

```python
def build_agent_prompt(now: str) -> str:
    return AGENT_SYSTEM_PROMPT.format(
        now=now, greeting=GREETING_TEXT, help_text=HELP_TEXT
    )
```

`agent.py` 导入改为 `build_agent_prompt`，`run_agent` 里替换原来的 `.format(...)`。

验证：单测 + 全量 pytest。

## 任务 4：日志转义（一行）

`main.py` 的 `处理消息` 行：

```python
logger.info("处理消息: openid=%s content=%s msgid=%s", openid, content[:30]!r, msg_id)
```

没有新分支，不单独加单测；在任务 7 用真实换行消息验证。

## 任务 5：兜底帮助（TDD）

测试文件：`tests/test_llm_fallback.py`（新建），先失败：

```python
import pytest
import llm
from prompts import HELP_TEXT

@pytest.mark.parametrize("text", ["帮助", "怎么用", "有什么功能"])
def test_chat_reply_returns_help_without_llm(monkeypatch, text):
    def boom():
        raise AssertionError("LLM 不应被调用")
    monkeypatch.setattr(llm, "_get_client", boom)
    assert llm.chat_reply(text, "2026-11-25T12:00:00+08:00") == HELP_TEXT
```

`llm.py` 导入 `HELP_TEXT`，在 `chat_reply` 的 `client = _get_client()` 之前加：

```python
if any(k in content for k in ("帮助", "怎么用", "有什么功能")):
    return HELP_TEXT
```

验证：单测 + 全量 pytest。

## 任务 6：文档修正

`prompts.py` 顶部：

```python
占位符：{now}（当前时间）、{greeting}（欢迎语）、{help_text}（帮助清单）；
统一由 build_agent_prompt(now) 注入。
```

## 任务 7：全量验证

1. `.venv/bin/python -m pytest -q`：117 + 新增，全绿。
2. `.venv/bin/ruff check main.py prompts.py llm.py agent.py tests/test_logging.py tests/test_prompts.py tests/test_llm_fallback.py`
3. 重启服务（`kill -TERM` 触发 systemd 拉起），HTTP 发「帮助」和「带\n换行」的消息：
   - 回复是 `HELP_TEXT`
   - `logs/app.log` 里换行显示为 `\n`，没有伪造行
   - `stat -c %a logs/app.log` 是 640
4. 清理测试用户，只留真实用户。
5. `git diff --stat` 与 `git diff --ignore-cr-at-eol --stat` 一致。

## 任务 8：审查与提交

1. 规格符合性 + 代码质量检查（对照本 plan）。
2. `ocr review`（若 CLI 可用）；结果写入 `.delivery-gate/review.md` 四项。
3. `gate.py verify --cmd "pytest -q" --cmd "ruff check ..."`。
4. `gate.py commit-check`。
5. `git add` 明确文件，`git commit`（pre-commit 自动 gitleaks）。
6. 报告 commit hash 与验证结果，不合并、不推送。

## 审查后修订

- **R1 首次关注欢迎语**：新增任务 5.5（TDD）——`_build_welcome` 新用户分支恢复 spec 003 FR-001 五要素；`test_welcome_first_vs_returning` 恢复五要素断言；聊天问候保持短版。
- **R2 兜底帮助**：关键词改为精确匹配（`content.strip() in (...)），新增「这个月怎么用了这么多」负向测试。
- **R3 日志转义**：同一规则扩展到 `快路径回复`、`agent.py`、`graph.py`。
- **R4 计划笔误**：Task 4 的 `content[:30]!r` 无效，实现用 `%r` + `content[:30]`。
- **R5 提示词导入**：`AGENT_SYSTEM_PROMPT` 移除后，两个测试改从 `prompts` 导入。
