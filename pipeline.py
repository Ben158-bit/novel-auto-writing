# -*- coding: utf-8 -*-
"""DeterminFlow-dy 全自动小说产出链路编排脚本。

用法:
    python pipeline.py --daily 2            # 每天跑一批（默认 2 章，含润色+后验）
    python pipeline.py --chapters 6 7       # 指定章节区间（覆盖 --daily）
    python pipeline.py --status             # 查看当前进度
    python pipeline.py --book 东野异象-3 --daily 2 --review   # 人工审阅模式（不自动确认）
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# 配置区（所有路径/标识均可用环境变量覆盖，便于他人套用）
# ============================================================
import os as _os

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"

# PostgreSQL（本机默认 D:/pg17，可用 PG_BIN_DIR / PG_DATA_DIR / PG_LOG_FILE 覆盖）
PG_CTL = Path(_os.environ.get("PG_BIN_DIR", "D:/pg17/pgsql/bin")) / "pg_ctl.exe"
PG_DATA = Path(_os.environ.get("PG_DATA_DIR", "D:/pg17/data"))
PG_LOG = Path(_os.environ.get("PG_LOG_FILE", "D:/pg17/pg.log"))

CORE_URL = _os.environ.get("CORE_URL", "http://127.0.0.1:8020")
STUDIO_URL = _os.environ.get("STUDIO_URL", "http://127.0.0.1:8031")
CORE_PORT = int(_os.environ.get("CORE_PORT", "8020"))
STUDIO_PORT = int(_os.environ.get("STUDIO_PORT", "8031"))
PG_PORT = int(_os.environ.get("PG_PORT", "5432"))

BOOKS_ROOT = ROOT / "data" / "books"
LOGS_DIR = ROOT / "logs"
OUTPUT_DIR = Path(_os.environ.get("OUTPUT_DIR", "D:/DeterminFlow-output"))

PUSHPLUS_URL = "https://www.pushplus.plus/send"
DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
DEFAULT_MIN_BALANCE = float(_os.environ.get("MIN_BALANCE", "1.0"))  # 余额低于此值（元）时停止
DEFAULT_MAX_TOKENS = int(_os.environ.get("MAX_TOKENS", "200000"))   # 单批 token 估算上限（熔断）

DEFAULT_BOOK = _os.environ.get("DEFAULT_BOOK", "东野异象-3")  # 单本书
DEFAULT_CHAPTERS_PER_BATCH = int(_os.environ.get("CHAPTERS_PER_BATCH", "2"))  # 每天产出章节数
STAGES = ["build", "character", "story-plan", "outline"]  # 策划四阶段，顺序执行
DEFAULT_RETRY = 3                  # 单任务失败重试次数
REQUEST_TIMEOUT = 30               # HTTP 请求超时（秒）
WORKFLOW_POLL_SECONDS = 10         # 工作流任务轮询间隔
CRUISE_POLL_SECONDS = 20           # 巡航轮询间隔
TOKEN_EST_PER_CHAPTER = 45_000     # 每章 token 估算（3-6 万的中值，以阶段 4 实测为准）

TERMINAL_TASK_STATES = {"completed", "failed", "stopped", "error", "unavailable"}
TERMINAL_CRUISE_STATES = {"completed", "failed", "stopped"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with (LOGS_DIR / "pipeline.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ============================================================
# 服务自检与拉起
# ============================================================
def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_postgres() -> bool:
    """确保本地 PostgreSQL 在 5432 运行；已运行则跳过。v0.2.2 插件不依赖，失败仅警告。"""
    if port_open(PG_PORT):
        log("PostgreSQL 已在运行（5432）")
        return True
    log("PostgreSQL 未运行，尝试启动（v0.2.2 不依赖，失败不阻断）...")
    if not PG_CTL.exists():
        log("WARN: 未找到 pg_ctl（D:/pg17/pgsql/bin/pg_ctl.exe），跳过 PostgreSQL")
        return False
    try:
        # 用 detached Popen + DEVNULL 输出，避免 postgres 继承 stdout/stderr 管道
        # 导致 subprocess.run 等不到 EOF 而阻塞（Windows 经典问题）。
        subprocess.Popen(
            [str(PG_CTL), "-D", str(PG_DATA), "-l", str(PG_LOG), "-o", "-p 5432", "-w", "start"],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        log(f"WARN: PostgreSQL 启动失败（不影响 v0.2.2）: {exc}")
        return False
    for _ in range(15):
        if port_open(PG_PORT):
            log("PostgreSQL 启动成功")
            return True
        time.sleep(2)
    log("WARN: PostgreSQL 启动超时（不影响 v0.2.2）")
    return False


def ensure_core() -> bool:
    """确保 DeterminFlow Core 在 8020 运行。"""
    if http_ok(CORE_URL + "/"):
        log("Core 已在运行（8020）")
        return True
    log("Core 未运行，尝试启动...")
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        subprocess.Popen(
            [str(VENV_PY), "run.py"], cwd=str(ROOT), env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        log(f"ERROR: Core 拉起失败: {exc}")
        return False
    for _ in range(45):  # Core 启动较慢（加载插件），最多等 90 秒
        if http_ok(CORE_URL + "/"):
            log("Core 启动成功（8020）")
            return True
        time.sleep(2)
    log("ERROR: Core 启动超时（90 秒）")
    return False


def ensure_studio() -> bool:
    """确保 Novel Studio BFF 在 8031 运行。"""
    if http_ok(STUDIO_URL + "/"):
        log("Novel Studio 已在运行（8031）")
        return True
    log("Novel Studio 未运行，尝试启动...")
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        subprocess.Popen(
            [str(VENV_PY), "-m", "uvicorn", "server:app", "--app-dir", "novel-studio",
             "--host", "127.0.0.1", "--port", "8031"],
            cwd=str(ROOT), env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        log(f"ERROR: Novel Studio 拉起失败: {exc}")
        return False
    for _ in range(20):
        if http_ok(STUDIO_URL + "/"):
            log("Novel Studio 启动成功（8031）")
            return True
        time.sleep(1)
    log("ERROR: Novel Studio 启动超时")
    return False


def ensure_services() -> bool:
    """依次确保 PostgreSQL（可选）/ Core / Novel Studio 可用。"""
    ensure_postgres()
    ok = ensure_core()
    ok = ensure_studio() and ok
    return ok


# ============================================================
# HTTP API 封装（Novel Studio BFF）
# ============================================================
def api(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    # 对路径各段做 percent-encode（中文书名等非 ASCII 需要转义）
    encoded_path = "/".join(urllib.parse.quote(seg) for seg in path.split("/"))
    request = urllib.request.Request(
        STUDIO_URL + encoded_path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法访问 Novel Studio（8031）: {exc}") from exc


def get_book(book_id: str) -> dict:
    return api("GET", f"/api/books/{book_id}")


# ============================================================
# 书管理
# ============================================================
def book_exists(book_id: str) -> bool:
    path = BOOKS_ROOT / book_id
    return path.is_dir() and (path / ".studio" / "book.json").is_file()


def resolve_or_create_book(book_id: str, title: str, premise: str, genre: str) -> str:
    """书不存在则建书，返回实际 book_id（建书时 slug 可能带后缀）。"""
    if book_exists(book_id):
        log(f"书已存在：{book_id}")
        return book_id
    if not title or not premise or not genre:
        raise RuntimeError(
            f"书「{book_id}」不存在；建书需提供 --title / --premise / --genre"
        )
    payload = {
        "title": title,
        "premise": premise,
        "genre": genre,
        "language": "中文",
        "estimated_length": "长篇",
        "words_per_chapter": "3000-4000",
    }
    created = api("POST", "/api/books", payload)
    new_id = str(created.get("id") or "")
    log(f"已建书：{new_id}")
    return new_id


# ============================================================
# 断点续跑状态（pipeline_state.json）
# ============================================================
def state_path(book_id: str) -> Path:
    return BOOKS_ROOT / book_id / ".studio" / "pipeline_state.json"


def load_state(book_id: str) -> dict:
    path = state_path(book_id)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(book_id: str, state: dict) -> None:
    path = state_path(book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {**state, "updated_at": now_iso()}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ============================================================
# 策划阶段链（build → character → story-plan → outline）
# ============================================================
def wait_workflow(book_id: str, stage: str) -> None:
    """轮询当前任务直到终态；失败抛 RuntimeError。"""
    last_status = None
    while True:
        record = api("GET", f"/api/books/{book_id}/task")
        status = str(record.get("status", "unknown"))
        if status != last_status:
            completed = record.get("completed_nodes")
            total = record.get("total_nodes")
            progress = f" {completed}/{total}" if completed is not None else ""
            log(f"  任务状态={status}{progress}")
            last_status = status
        if status in TERMINAL_TASK_STATES:
            if status == "completed":
                return
            error = record.get("error") or f"任务结束：{status}"
            raise RuntimeError(f"[{stage}] {error}")
        time.sleep(WORKFLOW_POLL_SECONDS)


def run_stage(book_id: str, stage: str, review: bool) -> None:
    """启动单个策划阶段工作流 → 等待完成 → （非 review 模式）自动确认。含重试。"""
    for attempt in range(1, DEFAULT_RETRY + 1):
        try:
            log(f"启动工作流：{stage}（第 {attempt}/{DEFAULT_RETRY} 次）")
            record = api("POST", f"/api/books/{book_id}/workflow/{stage}", {"parameters": {}})
            log(f"  任务已创建：{record.get('task_id')}")
            wait_workflow(book_id, stage)
            break
        except RuntimeError as exc:
            log(f"  [FAIL] {stage} 失败：{exc}")
            if attempt >= DEFAULT_RETRY:
                raise RuntimeError(f"阶段 {stage} 连续失败 {DEFAULT_RETRY} 次，停止") from exc
            log(f"  等待 15 秒后重试...")
            time.sleep(15)

    if review:
        log(f"  （--review 模式）阶段 {stage} 已生成，跳过自动确认，等待人工审阅")
        return
    log(f"确认阶段：{stage}")
    api("POST", f"/api/books/{book_id}/stage/{stage}/confirm")


def ensure_stages(book_id: str, review: bool) -> None:
    """按顺序推进四阶段：complete 跳过、review 补确认、ready 跑、locked 报错。"""
    meta = get_book(book_id)
    status = meta.get("stage_status", {})
    for stage in STAGES:
        st = status.get(stage, "ready")
        if st == "complete":
            log(f"阶段 {stage}：已完成，跳过")
            continue
        if st == "review":
            if review:
                log(f"阶段 {stage}：已生成，等待人工审阅（--review）")
                continue
            log(f"阶段 {stage}：已生成未确认，自动确认")
            api("POST", f"/api/books/{book_id}/stage/{stage}/confirm")
            continue
        if st == "locked":
            raise RuntimeError(f"阶段 {stage} 前置阶段未完成，无法继续（当前状态 {st}）")
        # ready：启动并确认
        run_stage(book_id, stage, review)


# ============================================================
# 正文巡航（mvp → polish → post-hoc）
# ============================================================
def next_chapter_number(book_id: str) -> int:
    chapters = get_book(book_id).get("chapters", [])
    if not chapters:
        return 1
    return max(int(c["number"]) for c in chapters) + 1


def run_cruise(book_id: str, start: int, end: int, polish: bool) -> None:
    """启动巡航并轮询直到完成；失败抛 RuntimeError。"""
    payload = {
        "start_chapter": start,
        "end_chapter": end,
        "polish": polish,
        "writer_type": "single",
        "target_word_count": "3000-4000",
        "human_intent": "",
    }
    try:
        resp = api("POST", f"/api/books/{book_id}/cruise", payload)
    except RuntimeError as exc:
        if "已经在运行" in str(exc) or "409" in str(exc):
            log("巡航已在运行，直接进入轮询等待其结束")
        else:
            raise
    log(f"巡航已启动：第 {start}~{end} 章（polish={polish}）")

    last_phase = None
    while True:
        cruise = get_book(book_id).get("cruise", {})
        status = str(cruise.get("status", "unknown"))
        phase = cruise.get("phase")
        chapter = cruise.get("chapter")
        marker = f"{status}"
        if chapter is not None:
            marker += f" 第{chapter}章"
        if phase:
            marker += f" {phase}"
        if marker != last_phase:
            log(f"  巡航：{marker}")
            last_phase = marker
        if status in TERMINAL_CRUISE_STATES:
            if status == "completed":
                return
            error = cruise.get("error") or f"巡航结束：{status}"
            raise RuntimeError(f"巡航失败：{error}")
        time.sleep(CRUISE_POLL_SECONDS)


# ============================================================
# 章节完整性检查（产出保障）
# ============================================================
def chapter_complete(book_id: str, number: int) -> bool:
    """章节是否完整产出（正文 + post-hoc 后验都完成）。"""
    story = BOOKS_ROOT / book_id / "story" / f"{number:04d}"
    return (story / "chapter.md").is_file() and (story / "diff_story_confirmed.md").is_file()


def missing_chapters(book_id: str, start: int, end: int) -> list[int]:
    """返回区间内未完整产出的章节号。"""
    return [n for n in range(start, end + 1) if not chapter_complete(book_id, n)]


# ============================================================
# 批次报告
# ============================================================
def report(book_id: str, produced: list[dict], elapsed: float, daily: int) -> None:
    total_words = sum(int(c.get("words", 0)) for c in produced)
    est_tokens = len(produced) * TOKEN_EST_PER_CHAPTER
    lines = [
        "=" * 60,
        f"批次报告（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）",
        f"书：{book_id}",
        f"本批目标：{daily} 章，实际产出：{len(produced)} 章",
        f"耗时：{elapsed / 60:.1f} 分钟",
        f"本批总字数：{total_words}",
        f"token 估算：约 {est_tokens / 10000:.1f} 万（每章按 {TOKEN_EST_PER_CHAPTER} 估算，阶段 4 实测为准）",
        "-" * 60,
    ]
    for c in produced:
        lines.append(f"  第 {int(c['number'])} 章 · {c.get('title', '')}  {c.get('words', 0)} 字")
    lines.append("=" * 60)
    text = "\n".join(lines) + "\n"

    log("\n".join(lines))
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    with (LOGS_DIR / f"pipeline_{day}.log").open("a", encoding="utf-8") as f:
        f.write(text + "\n")


# ============================================================
# 产出导出（每章一个 txt）
# ============================================================
def sanitize_filename(name: str) -> str:
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "")
    return name.strip() or "未命名"


def export_chapters(book_id: str, produced: list[dict]) -> list[Path]:
    """把本批每章导出为独立 txt，存到固定文件夹。"""
    book_dir = BOOKS_ROOT / book_id
    out_dir = OUTPUT_DIR / book_id
    out_dir.mkdir(parents=True, exist_ok=True)
    exported = []
    for c in produced:
        number = int(c["number"])
        title = sanitize_filename(str(c.get("title") or "").strip() or f"第{number}章")
        body_path = book_dir / "story" / f"{number:04d}" / "chapter.md"
        content = body_path.read_text(encoding="utf-8") if body_path.is_file() else ""
        target = out_dir / f"第{number}章-{title}.txt"
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        exported.append(target)
        log(f"已导出：{target}")
    return exported


# ============================================================
# 微信推送（PushPlus，每章完整全文）
# ============================================================
def load_env_token(key: str) -> str:
    """从 ROOT/.env 读取配置，其次回退到环境变量。"""
    env_file = ROOT / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
        except OSError:
            pass
    return os.environ.get(key, "")


def get_deepseek_balance() -> tuple[bool, float]:
    """查询 DeepSeek 账户余额（元）。返回 (是否可用, 余额)。"""
    key = load_env_token("DEEPSEEK_API_KEY")
    if not key:
        log("未配置 DEEPSEEK_API_KEY，跳过余额检查")
        return False, 0.0
    request = urllib.request.Request(
        DEEPSEEK_BALANCE_URL,
        method="GET",
        headers={"Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("is_available"):
            for info in data.get("balance_infos", []):
                if info.get("currency") == "CNY":
                    try:
                        return True, float(info.get("total_balance", 0))
                    except (TypeError, ValueError):
                        return True, 0.0
    except Exception as exc:
        log(f"DeepSeek 余额查询失败：{exc}")
    return False, 0.0


def push_to_wechat(token: str, title: str, content: str, retry: int = 3) -> bool:
    """推送一条消息到手机微信。失败重试 retry 次，均失败返回 False。"""
    body = {"token": token, "title": title, "content": content, "template": "txt"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        PUSHPLUS_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(1, retry + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            code = result.get("code")
            if code == 200:
                log(f"微信推送成功：{title}")
                return True
            log(f"微信推送返回 code={code}：{result.get('msg')}")
            # 900=当日额度用尽/被封，905=未实名——重试无意义，直接放弃
            if code in (900, 905, 906):
                return False
        except Exception as exc:
            log(f"微信推送异常（第 {attempt}/{retry} 次）：{exc}")
        if attempt < retry:
            time.sleep(5)
    return False


def push_chapters(book_id: str, produced: list[dict]) -> None:
    """把本批每章完整正文推送到手机微信（每章一条）。失败不阻断主流程。"""
    token = load_env_token("PUSHPLUS_TOKEN")
    if not token:
        log("未配置 PUSHPLUS_TOKEN，跳过微信推送")
        return
    book_dir = BOOKS_ROOT / book_id
    state = load_state(book_id)
    pushed = set(state.get("pushed", []))
    for c in produced:
        number = int(c["number"])
        if number in pushed:
            log(f"第{number}章 已推送过，跳过（幂等去重）")
            continue
        title = str(c.get("title") or f"第{number}章").strip()
        body_path = book_dir / "story" / f"{number:04d}" / "chapter.md"
        if not body_path.is_file():
            continue
        text = body_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        # 去掉首行 "# 标题"（标题已放在消息 title 里，避免重复）
        if lines and lines[0].lstrip().startswith("#"):
            lines = lines[1:]
        content = "\n".join(lines).strip()
        if not content:
            continue
        if push_to_wechat(token, f"第{number}章 · {title}", content):
            pushed.add(number)
            save_state(book_id, {**state, "pushed": sorted(pushed)})


# ============================================================
# 状态查看
# ============================================================
def show_status(book_id: str) -> int:
    if not book_exists(book_id):
        log(f"书不存在：{book_id}")
        return 1
    meta = get_book(book_id)
    log(f"书：{book_id}（{meta.get('title', '')}）")
    log(f"阶段状态：{meta.get('stage_status', {})}")
    chapters = meta.get("chapters", [])
    log(f"已产出章节：{len(chapters)} 章")
    for c in chapters[-5:]:
        log(f"  第 {int(c['number'])} 章 · {c.get('title', '')}  {c.get('words', 0)} 字")
    log(f"巡航状态：{meta.get('cruise', {})}")
    return 0


# ============================================================
# 入口
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="DeterminFlow-dy 自动产出链路")
    parser.add_argument("--daily", type=int, default=DEFAULT_CHAPTERS_PER_BATCH,
                        help="本批产出章节数（默认 2）")
    parser.add_argument("--chapters", nargs=2, type=int, metavar=("START", "END"),
                        help="指定章节区间（覆盖 --daily）")
    parser.add_argument("--book", default=DEFAULT_BOOK, help="目标书（默认 东野异象-3）")
    parser.add_argument("--title", default="", help="建书用：书名（书不存在时）")
    parser.add_argument("--premise", default="", help="建书用：核心设定/前提（书不存在时）")
    parser.add_argument("--genre", default="", help="建书用：类型（书不存在时）")
    parser.add_argument("--status", action="store_true", help="查看进度")
    parser.add_argument("--review", action="store_true", help="人工审阅模式（不自动确认阶段）")
    parser.add_argument("--no-polish", action="store_true", help="巡航跳过 polish 润色")
    parser.add_argument("--skip-services", action="store_true", help="跳过服务检查")
    parser.add_argument("--min-balance", type=float, default=DEFAULT_MIN_BALANCE,
                        help="DeepSeek 余额低于此值（元）则停止（默认 1.0）")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help="单批 token 估算上限，超过则停止（默认 200000）")
    parser.add_argument("--skip-balance-check", action="store_true", help="跳过余额检查")
    args = parser.parse_args()

    if args.status:
        return show_status(args.book)

    started = time.time()

    if not args.skip_services and not ensure_services():
        log("依赖服务不可用，中止")
        return 1
    log("依赖服务就绪")

    book_id = resolve_or_create_book(args.book, args.title, args.premise, args.genre)

    # 1) 策划阶段链（幂等：已完成/已确认的不重跑）
    log("---- 策划阶段链 ----")
    try:
        ensure_stages(book_id, args.review)
    except RuntimeError as exc:
        log(f"阶段链中止：{exc}")
        return 1
    log("策划阶段链就绪")

    # 2) 正文巡航（默认续写：已产出章节数 + 1 开始）
    if args.chapters:
        start, end = args.chapters
    else:
        start = next_chapter_number(book_id)
        end = start + args.daily - 1
    if end < start:
        log(f"ERROR: 结束章节 {end} 小于开始章节 {start}")
        return 1

    # 熔断检查：余额 + 单批 token 上限
    balance_before = None
    if not args.skip_balance_check:
        available, balance = get_deepseek_balance()
        if available:
            balance_before = balance
            log(f"DeepSeek 余额：{balance:.2f} 元")
            if balance < args.min_balance:
                log(f"熔断：余额 {balance:.2f} 元低于阈值 {args.min_balance} 元，停止本批")
                save_state(book_id, {"status": "stopped", "reason": "insufficient_balance", "balance": balance})
                return 1
        else:
            log("WARN: 无法查询 DeepSeek 余额，跳过余额熔断")
    batch_tokens = (end - start + 1) * TOKEN_EST_PER_CHAPTER
    if batch_tokens > args.max_tokens:
        log(f"熔断：本批估算 {batch_tokens} token 超过上限 {args.max_tokens}，停止")
        save_state(book_id, {"status": "stopped", "reason": "token_limit", "estimated_tokens": batch_tokens})
        return 1

    log(f"---- 正文巡航：第 {start}~{end} 章 ----")
    cruise_ok = False
    cursor = start
    for attempt in range(1, DEFAULT_RETRY + 1):
        try:
            run_cruise(book_id, cursor, end, polish=not args.no_polish)
            cruise_ok = True
            break
        except RuntimeError as exc:
            log(f"巡航失败（第 {attempt}/{DEFAULT_RETRY} 次）：{exc}")
            missing = missing_chapters(book_id, cursor, end)
            if not missing:
                # 区间内章节其实已完整（巡航状态误报/已被外部补跑），视为成功
                cruise_ok = True
                break
            cursor = missing[0]
            log(f"缺失章节：{missing}，将从第 {cursor} 章补跑")
            if attempt < DEFAULT_RETRY:
                log("等待 20 秒后补跑...")
                time.sleep(20)
    if not cruise_ok:
        log("巡航连续失败，继续对已产出章节做收尾（导出+推送）")
        save_state(book_id, {"status": "partial_failed", "target": [start, end],
                             "missing": missing_chapters(book_id, start, end)})

    # 3) 批次报告 + 导出 txt（只处理已完整产出的章节，保证收尾不因部分失败而缺失）
    chapters = get_book(book_id).get("chapters", [])
    produced = [c for c in chapters
                if start <= int(c["number"]) <= end and chapter_complete(book_id, int(c["number"]))]
    elapsed = time.time() - started
    if not args.skip_balance_check and balance_before is not None:
        avail2, balance_after = get_deepseek_balance()
        if avail2:
            log(f"本批后余额：{balance_after:.2f} 元，本批实际花费约 {balance_before - balance_after:.2f} 元")
    report(book_id, produced, elapsed, args.daily)
    export_chapters(book_id, produced)
    push_chapters(book_id, produced)

    save_state(book_id, {
        "status": "completed",
        "last_batch": [start, end],
        "produced": [int(c["number"]) for c in produced],
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
