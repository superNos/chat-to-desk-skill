#!/usr/bin/env python3
"""个人工作台的可选本机运行支持（仅 Python 标准库）。

它只做与工作内容无关的事：
- 只在 127.0.0.1 提供 Agent 为这位用户生成的页面（工作台目录下的 page/）；
- 保存页面记录和交接记录（data/state.json）；
- 按 workbench.json 的配置，把交接直接交给已核实的 Codex 对话，或生成可复制的交接内容；
- 只记录有证据的状态。

它不包含任何页面布局、职业字段或功能清单。页面长什么样、放哪些对象和输入，
由用户的 Agent 按工作台说明生成。用法见 references/runtime.md。
"""
from __future__ import annotations

import argparse
import contextlib
import hmac
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:  # 防止两个进程同时写同一个工作台；没有 fcntl 的系统只用进程内锁
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

SCRIPT_DIR = Path(__file__).resolve().parent
CLIENT_JS = SCRIPT_DIR / "client.js"
HOST = "127.0.0.1"
DEFAULT_PORT = 48760
PORT_TRIES = 20

MAX_BODY = 256 * 1024
MAX_TEXT = 8000
MAX_SHORT = 200
MAX_RECORD_BYTES = 64 * 1024
MAX_RECORDS = 2000
MAX_HANDOFFS = 5000
MAX_RESULT_TEXT = 20000
CODEX_START_TIMEOUT = 60
DEFAULT_TURN_MINUTES = 60
PROBE_MESSAGE = "这是个人工作台的连通测试，不需要做任何操作。请只回复：收到"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:\-\u4e00-\u9fff]{1,80}$")
HANDOFF_ID_RE = re.compile(r"^h[0-9]{1,6}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
PAGE_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
EXTERNAL_REF = re.compile(
    r"""(?:src|href|action)\s*=\s*["']?\s*(?:https?:)?//"""
    r"""|url\(\s*["']?\s*(?:https?:)?//|@import\s+["']?(?:url\()?["']?(?:https?:)?//"""
    r"""|(?:fetch|open|sendBeacon|WebSocket|EventSource)\(\s*["'`](?:https?:|wss?:)?//""",
    re.I,
)
HEAD_RE = re.compile(rb"<head(\s[^>]*)?>", re.I)
CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)

INTENTS = {"new": "新工作", "continue": "继续某件事", "revise": "修改结果"}
CAPABILITY_FOR_INTENT = {"new": "发起工作", "continue": "继续同一工作", "revise": "继续同一工作"}
CAPABILITY_NAMES = ("发起工作", "继续同一工作", "取回结果")
CAPABILITY_VALUES = ("已核实", "不支持", "待核实")
ADAPTERS = ("manual", "codex")
# 需手动完成 → 已发送 → 已开始 → 已完成 是一条线，只往前走；待确认、失败单独显示。
# “无法确认”不是单独的状态：保留上一个有证据的状态，另外标明最新情况未知（unknown）。
LINEAR = ("需手动完成", "已发送", "已开始", "已完成")
STATUSES = LINEAR + ("待确认", "失败")
DEFAULT_LABELS = {"destination": "去向", "background": "背景", "ask": "这次要做", "expect": "完成后给我"}


class WorkbenchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 配置


def validate_config(config) -> list[str]:
    """只检查形式与安全边界，不检查工作内容。"""
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["顶层必须是对象"]
    targets = config.get("targets")
    if not isinstance(targets, dict) or not targets:
        errors.append("至少要配置一个交付目标（targets）")
        targets = {}
    for key, target in targets.items():
        if not TARGET_RE.match(str(key)):
            errors.append(f"目标名 {key!r} 只能用字母、数字、下划线和连字符")
            continue
        if not isinstance(target, dict):
            errors.append(f"目标 {key} 必须是对象")
            continue
        if not isinstance(target.get("label"), str) or not target["label"].strip():
            errors.append(f"目标 {key} 缺少给用户看的名称（label）")
        adapter = target.get("adapter", "manual")
        if adapter not in ADAPTERS:
            errors.append(f"目标 {key} 的 adapter 只能是 manual 或 codex")
        caps = target.get("capabilities", {})
        if not isinstance(caps, dict):
            errors.append(f"目标 {key} 的 capabilities 必须是对象")
            caps = {}
        for name, value in caps.items():
            if name not in CAPABILITY_NAMES:
                errors.append(f"目标 {key} 有未知能力 {name}")
            elif not isinstance(value, str) or not value.startswith(CAPABILITY_VALUES):
                errors.append(f"目标 {key} 的 {name} 只能以 已核实、不支持 或 待核实 开头")
        if adapter == "codex":
            thread = target.get("thread")
            if not isinstance(thread, str) or not UUID_RE.match(thread):
                errors.append(f"目标 {key} 使用 codex 时必须写明对话编号（thread，UUID 格式）")
        elif "thread" in target:
            errors.append(f"目标 {key} 是手动交接，不应写 thread")
    labels = config.get("labels", {})
    if not isinstance(labels, dict) or any(
        k not in DEFAULT_LABELS or not isinstance(v, str) or not v.strip() for k, v in labels.items()
    ):
        errors.append("labels 只能改 destination、background、ask、expect 四个标签的说法")
    minutes = config.get("codex_turn_minutes", DEFAULT_TURN_MINUTES)
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 24 * 60:
        errors.append("codex_turn_minutes 要是 1 到 1440 之间的整数")
    return errors


def load_config(root: Path) -> dict:
    path = root / "workbench.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise WorkbenchError(500, "工作台目录里没有 workbench.json") from None
    except json.JSONDecodeError as exc:
        raise WorkbenchError(500, f"workbench.json 不是有效的 JSON：第 {exc.lineno} 行") from None
    errors = validate_config(config)
    if errors:
        raise WorkbenchError(500, "workbench.json 有问题：" + "；".join(errors))
    return config


def capability(target: dict, name: str) -> str:
    return str((target.get("capabilities") or {}).get(name, "待核实"))


def verified(target: dict, name: str) -> bool:
    return capability(target, name).startswith("已核实")


def auto_send(target: dict, intent: str) -> bool:
    """只有 codex 目标、且这类交接需要的能力已核实时才直接交付；其余一律生成可复制的交接内容。"""
    return target.get("adapter") == "codex" and verified(target, CAPABILITY_FOR_INTENT[intent])


def auto_result(target: dict) -> bool:
    return target.get("adapter") == "codex" and verified(target, "取回结果")


def public_targets(config: dict) -> dict:
    """给页面看的目标信息，不含对话编号。"""
    return {
        key: {
            "label": target["label"],
            "platform": str(target.get("platform", "")),
            "capabilities": {name: capability(target, name) for name in CAPABILITY_NAMES},
            "direct": {intent: auto_send(target, intent) for intent in INTENTS},
            "autoResult": auto_result(target),
        }
        for key, target in config["targets"].items()
    }


# ---------------------------------------------------------------- 状态文件


class Store:
    """data/state.json 的读写。每次操作都重新读文件，命令行和服务看到的是同一份数据。"""

    def __init__(self, root: Path):
        self.data_dir = root / "data"
        self.path = self.data_dir / "state.json"
        self.lock = threading.RLock()

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.data_dir.mkdir(mode=0o700, exist_ok=True)
            with open(self.data_dir / "state.lock", "a+") as handle:
                if fcntl:
                    fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    state = self.read()
                    yield state
                    self.write(state)
                finally:
                    if fcntl:
                        fcntl.flock(handle, fcntl.LOCK_UN)

    def read(self) -> dict:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = {}
        except json.JSONDecodeError:
            raise WorkbenchError(500, "data/state.json 已损坏，请先备份再处理") from None
        state.setdefault("schema", 1)
        state.setdefault("records", {})
        state.setdefault("handoffs", [])
        state.setdefault("next", 1)
        return state

    def write(self, state: dict) -> None:
        text = json.dumps(state, ensure_ascii=False, indent=1)
        fd, tmp = tempfile.mkstemp(prefix=".state-", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise


def find_handoff(state: dict, handoff_id: str) -> dict:
    for handoff in state["handoffs"]:
        if handoff["id"] == handoff_id:
            return handoff
    raise WorkbenchError(404, "没有这条交接记录")


def set_status(handoff: dict, status: str, evidence: str) -> bool:
    """按证据改状态。线上的状态只往前走，不会因为后到的旧消息退回；返回是否改了。"""
    current = handoff["status"]
    if status in LINEAR and current in LINEAR and LINEAR.index(status) < LINEAR.index(current):
        return False
    if status in ("已发送", "已开始") and current in ("待确认", "已完成", "失败"):
        # 后到的“已发送/已开始”只是过程消息，不能盖掉已经有的结论（例如重新查询已取回结果、等用户确认）
        return False
    if status == current and not handoff.get("unknown"):
        return False
    handoff["status"] = status
    handoff["unknown"] = False
    handoff["events"].append({"time": now(), "status": status, "evidence": evidence})
    return True


def mark_unknown(handoff: dict, evidence: str) -> None:
    """拿不到最新情况：保留上一个有证据的状态，只标明最新情况未知。"""
    handoff["unknown"] = True
    handoff["events"].append({"time": now(), "status": handoff["status"], "unknown": True, "evidence": evidence})


def add_result(handoff: dict, text: str, source: str) -> None:
    previous = handoff["results"][-1]["n"] if handoff["results"] else 0
    truncated = len(text) > MAX_RESULT_TEXT
    result = {"n": previous + 1, "time": now(), "source": source, "text": text[:MAX_RESULT_TEXT]}
    if truncated:
        result["truncated"] = True
    handoff["results"].append(result)
    if truncated:
        # 只拿到了一部分：不能说已完成，请用户到原对话看完整内容
        add_note(handoff, f"第 {result['n']} 版结果有 {len(text)} 字，工作台只保存了前 {MAX_RESULT_TEXT} 字；完整内容请到原对话里看")
        set_status(handoff, "待确认", f"{source}，但只保存了前 {MAX_RESULT_TEXT} 字；请到原对话看完整结果")
        return
    if handoff.get("confirmAfter"):
        set_status(handoff, "待确认", f"{source}；下一步要你自己决定")
    else:
        set_status(handoff, "已完成", f"{source}；拿到了这次要的结果")


def compose_text(config: dict, handoff: dict, manual: bool) -> str:
    labels = {**DEFAULT_LABELS, **config.get("labels", {})}
    destination = INTENTS[handoff["intent"]]
    if handoff["intent"] != "new":
        destination += f"：{handoff['about']}"
        if manual:
            destination += "（请粘贴到原来那个对话里）"
    return "\n".join([
        f"【{labels['destination']}】{destination}",
        f"【{labels['background']}】{handoff['background'] or '无'}",
        f"【{labels['ask']}】{handoff['ask']}",
        f"【{labels['expect']}】{handoff['expect']}",
    ])


def text_field(payload: dict, name: str, limit: int, required: bool) -> str:
    value = payload.get(name)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise WorkbenchError(400, f"{name} 必须是文字")
    value = value.strip()
    if required and not value:
        raise WorkbenchError(400, f"{name} 不能为空")
    if len(value) > limit:
        raise WorkbenchError(400, f"{name} 超过 {limit} 字")
    return value


# ---------------------------------------------------------------- Codex 适配器


def decline_server_request(message: dict) -> dict:
    """Codex 在这一轮里请求审批或用户输入时一律拒绝：工作台不替用户批准任何操作。"""
    method = message.get("method", "")
    if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
        return {"id": message["id"], "result": {"decision": "decline"}}
    if method in ("execCommandApproval", "applyPatchApproval"):
        return {"id": message["id"], "result": {"decision": {"denied": {"rejection": "工作台不替用户批准任何操作"}}}}
    if method == "mcpServer/elicitation/request":
        return {"id": message["id"], "result": {"action": "decline"}}
    return {"id": message["id"], "error": {"code": -32601, "message": "工作台不处理这类请求"}}


class CodexReplyError(RuntimeError):
    """Codex 对某个请求明确回了错误（例如对话不存在）。"""


class CodexNoReply(RuntimeError):
    """等不到 Codex 的回应：超时或进程退出。请求是否已被处理无法确认。"""


def find_codex() -> str:
    found = shutil.which("codex")
    if not found:
        raise WorkbenchError(502, "这台电脑上找不到 codex 命令")
    return found


class CodexSession:
    """一次性的 codex app-server（stdio）会话。参数列表调用，不经过 shell。"""

    def __init__(self, codex_bin: str):
        self.proc = subprocess.Popen(
            [codex_bin, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
        )
        self.events: queue.Queue = queue.Queue()
        self.pending: list = []
        self.next_id = 1
        self.declined = 0
        self.turn_sent = False  # turn/start 是否已经写给 Codex；之后再出错就不能说“没有交出去”
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:
                with contextlib.suppress(json.JSONDecodeError):
                    self.events.put(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            self.events.put(None)

    def send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def next_event(self, deadline: float):
        """下一条消息；顺手拒绝服务端发来的审批请求。到期返回 "timeout"，进程退出返回 None。"""
        if self.pending:
            return self.pending.pop(0)
        return self._next_raw(deadline)

    def request(self, method: str, params: dict, timeout: float = CODEX_START_TIMEOUT) -> dict:
        request_id = self.next_id
        self.next_id += 1
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        held = []
        try:
            while True:
                item = self._next_raw(deadline)
                if item == "timeout":
                    raise CodexNoReply(f"Codex 没有在 {int(timeout)} 秒内响应 {method}")
                if item is None:
                    raise CodexNoReply("Codex 进程提前退出")
                if item.get("id") == request_id and "method" not in item:
                    if item.get("error"):
                        error = item["error"]
                        raise CodexReplyError(str(error.get("message") if isinstance(error, dict) else error)[:300])
                    return item.get("result") or {}
                held.append(item)
        finally:
            self.pending.extend(held)

    def _next_raw(self, deadline: float):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"
            try:
                item = self.events.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            if item is None:
                self.events.put(None)  # 之后再读也能立刻知道进程已退出
                return None
            if isinstance(item, dict) and "method" in item and "id" in item:
                self.declined += 1
                with contextlib.suppress(OSError, ValueError):
                    self.send(decline_server_request(item))
                continue
            return item

    def start_turn(self, thread_id: str, text: str) -> str:
        self.request("initialize", {"clientInfo": {"name": "personal-agent-workbench", "title": "Personal Agent Workbench", "version": "0.2.0"}})
        self.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
        self.turn_sent = True
        result = self.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": text}]})
        turn_id = (result.get("turn") or {}).get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise CodexNoReply("Codex 没有返回这一轮的编号")
        return turn_id

    def wait_turn(self, turn_id: str, minutes: int, on_started=None, on_progress=None):
        """等这一轮结束。返回 (结局, 这一轮的回复条目)；结局是 turn 对象、"timeout" 或 "exit"。"""
        items: list = []
        deadline = time.monotonic() + minutes * 60
        while True:
            item = self.next_event(deadline)
            if item in (None, "timeout"):
                return (item or "exit"), items
            method = item.get("method")
            params = item.get("params") or {}
            turn = params.get("turn") or {}
            if method == "turn/started" and turn.get("id") == turn_id and on_started:
                on_started()
            elif method == "item/completed" and params.get("turnId") == turn_id:
                message = params.get("item") or {}
                items.append(message)
                if (on_progress and isinstance(message, dict) and message.get("type") == "agentMessage"
                        and message.get("phase") == "commentary"):
                    text = message.get("text")
                    if isinstance(text, str) and text.strip():
                        on_progress(text.strip())
            elif method == "turn/completed" and turn.get("id") == turn_id:
                return turn, items

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        for stream in (self.proc.stdin, self.proc.stdout):
            with contextlib.suppress(OSError, ValueError):
                stream.close()


def final_text(items: list) -> str:
    """优先取最后一条明确的最终回复；旧版没有 phase 时取最后一条无标记回复。"""
    legacy = ""
    for item in reversed(items):
        if not isinstance(item, dict) or item.get("type") != "agentMessage":
            continue
        text = str(item.get("text") or "").strip()
        if text and item.get("phase") in ("final_answer", "final"):
            return text
        if not legacy and not item.get("phase"):
            legacy = text
    return legacy


def is_settled(handoff: dict) -> bool:
    """这条交接已经有了结论：Codex 这一轮已失败或已取回回复，或者已有结果（包括用户自己粘贴回来的），
    或者状态已是待确认、已完成、失败（包括用户自己记下的）。之后再来的平台消息不能改动它。"""
    return (bool((handoff.get("delivery") or {}).get("settled")) or bool(handoff["results"])
            or handoff["status"] in ("待确认", "已完成", "失败"))


def add_note(handoff: dict, text: str) -> None:
    """追加提示，不冲掉已有的提示（例如结果只保存了一部分）。"""
    note = handoff.get("note") or ""
    if text in note:
        return
    handoff["note"] = f"{note}；{text}" if note else text


def compare_late_result(handoff: dict, outcome, items: list) -> None:
    """已有结论时，同一轮的平台结局只用来提示，不改结果和状态。"""
    if not isinstance(outcome, dict) or outcome.get("status") != "completed":
        return
    text = final_text(items + (outcome.get("items") or []))
    if not text:
        return
    saved = {r.get("text", "").strip() for r in handoff["results"]}
    if not saved:
        add_note(handoff, "Codex 里这一轮已结束并有回复；这里的状态是之前记下的，工作台没有改动，需要的话请到原对话复制回复再粘贴回来")
    elif text.strip()[:MAX_RESULT_TEXT] in saved:
        add_note(handoff, "Codex 里这一轮也已结束，回复与这里保存的一致")
    else:
        add_note(handoff, "Codex 里这一轮的最后回复与这里保存的不同，请到原对话核对；工作台没有改动这里的结果和状态")


def apply_outcome(handoff: dict, target: dict, outcome, items: list) -> None:
    """把 Codex 这一轮的结局写成有证据的状态。

    后台等待、“重新查询”和用户手动粘贴可能先后带来同一轮的结局；这条交接已有结论后，
    之后再来的结局不会再加一版结果，也不会改动状态或用户已经做出的确认，只在提示里说明是否一致。
    """
    delivery = handoff.setdefault("delivery", {})
    if is_settled(handoff):
        delivery["settled"] = True
        compare_late_result(handoff, outcome, items)
        return
    if outcome == "timeout":
        mark_unknown(handoff, "等了很久，这一轮还没有结束的消息")
        return
    if not isinstance(outcome, dict):
        mark_unknown(handoff, "和 Codex 的连接断了，拿不到这一轮的最新情况")
        return
    status = outcome.get("status")
    if status == "failed":
        error = (outcome.get("error") or {}).get("message") or "没有错误说明"
        delivery["settled"] = True
        set_status(handoff, "失败", f"Codex 报告这一轮失败：{str(error)[:200]}")
        return
    if status == "interrupted":
        delivery["settled"] = True
        set_status(handoff, "失败", "Codex 报告这一轮被中断，没有完成（关闭工作台或等待超时也会中断这一轮）")
        return
    if status == "inProgress":
        set_status(handoff, "已开始", "Codex 显示这一轮正在进行")
        return
    if status != "completed":
        mark_unknown(handoff, f"Codex 返回了没见过的状态：{status}")
        return
    set_status(handoff, "已开始", "Codex 报告这一轮已结束")
    if not auto_result(target):
        add_note(handoff, "对话这一轮已结束。取回结果还没核实，请到对话里复制结果，再粘贴回这里")
        return
    text = final_text(items + (outcome.get("items") or []))
    if not text:
        mark_unknown(handoff, "这一轮结束了，但没有找到回复文字")
        return
    delivery["settled"] = True
    add_result(handoff, text, "从 Codex 对话取回")


# ---------------------------------------------------------------- 工作台逻辑


def page_dir_problem(root: Path) -> str | None:
    """page/ 不能是软链接，解析后也必须在工作台目录里，否则会把别处的文件当成页面公开。"""
    page = root / "page"
    if page.is_symlink() or (page.exists() and not page.resolve().is_relative_to(root.resolve())):
        return "page/ 必须是工作台目录里的真实文件夹，不能是指向别处的软链接"
    return None


def refresh_failed(handoff: dict, evidence: str) -> None:
    """重新查询没查到：还没有结论时标最新情况未知；已有结论时保留结论，只提示这次没查到。"""
    if is_settled(handoff):
        add_note(handoff, f"这次重新查询没有成功（{evidence[:120]}），上面的结论不变")
    else:
        mark_unknown(handoff, evidence)


class Workbench:
    def __init__(self, root: Path, session_factory=CodexSession, codex_locator=find_codex):
        self.root = root.resolve()
        problem = page_dir_problem(self.root)
        if problem:
            raise WorkbenchError(500, problem)
        self.page_dir = (self.root / "page").resolve()
        self.store = Store(self.root)
        self.watched_handoffs: set[str] = set()  # 与轮次编号一起在 store.lock 下读写，仅在本服务内有效
        self.session_factory = session_factory
        self.codex_locator = codex_locator

    def config(self) -> dict:
        return load_config(self.root)

    def public_state(self) -> dict:
        config = self.config()
        state = self.store.read()
        return {
            "ok": True,
            "title": str(config.get("title", "")),
            "targets": public_targets(config),
            "labels": {**DEFAULT_LABELS, **config.get("labels", {})},
            "records": state["records"],
            "handoffs": state["handoffs"],
        }

    # 页面记录：页面自己决定存什么（例如用户录入的对象），运行支持不理解内容

    def put_record(self, key: str, payload) -> dict:
        if not KEY_RE.match(key):
            raise WorkbenchError(400, "记录名不合规")
        if not isinstance(payload, dict) or "value" not in payload:
            raise WorkbenchError(400, "需要 {\"value\": ...}")
        replace = payload.get("replace") is True
        if replace and payload.get("confirmed") is not True:
            raise WorkbenchError(400, "覆盖已有记录要先请用户确认这一次（confirmed: true）")
        size = len(json.dumps(payload["value"], ensure_ascii=False).encode("utf-8"))
        if size > MAX_RECORD_BYTES:
            raise WorkbenchError(413, f"单条记录不能超过 {MAX_RECORD_BYTES // 1024} KB")
        with self.store.transaction() as state:
            if key in state["records"] and not replace:
                raise WorkbenchError(409, "这条记录已经存在；要覆盖，先请用户确认这一次，再用 replaceRecord")
            if key not in state["records"] and len(state["records"]) >= MAX_RECORDS:
                raise WorkbenchError(413, "记录太多了")
            state["records"][key] = payload["value"]
        return {"ok": True}

    def delete_record(self, key: str, payload) -> dict:
        if not isinstance(payload, dict) or payload.get("confirmed") is not True:
            raise WorkbenchError(400, "删除要先请用户确认这一次（confirmed: true）")
        with self.store.transaction() as state:
            if key not in state["records"]:
                raise WorkbenchError(404, "没有这条记录")
            del state["records"][key]
        return {"ok": True}

    # 交接

    def create_handoff(self, payload, *, require_direct: bool = False) -> dict:
        if not isinstance(payload, dict):
            raise WorkbenchError(400, "需要 JSON 对象")
        config = self.config()
        target_key = payload.get("target")
        if not isinstance(target_key, str) or target_key not in config["targets"]:
            raise WorkbenchError(403, "交付目标不在配置里")
        target = config["targets"][target_key]
        intent = payload.get("intent")
        if intent not in INTENTS:
            raise WorkbenchError(400, "要先选这是新工作、继续某件事，还是修改结果")
        handoff = {
            "target": target_key,
            "intent": intent,
            "about": text_field(payload, "about", MAX_SHORT, required=intent != "new"),
            "ref": text_field(payload, "ref", MAX_SHORT, required=False),
            "background": text_field(payload, "background", MAX_TEXT, required=False),
            "ask": text_field(payload, "ask", MAX_TEXT, required=True),
            "expect": text_field(payload, "expect", MAX_TEXT, required=True),
            "confirmAfter": payload.get("confirmAfter") is True,
            "continues": text_field(payload, "continues", 10, required=False),
        }
        direct = auto_send(target, intent)
        if require_direct and (not direct or not auto_result(target)):
            raise WorkbenchError(409, "执行或结果回传尚未接通，本次工作未发送")
        handoff["text"] = compose_text(config, handoff, manual=not direct)
        if len(handoff["text"]) > MAX_TEXT:
            raise WorkbenchError(400, f"交接内容超过 {MAX_TEXT} 字")
        with self.store.transaction() as state:
            if handoff["continues"]:
                find_handoff(state, handoff["continues"])
            if len(state["handoffs"]) >= MAX_HANDOFFS:
                raise WorkbenchError(413, "交接记录太多了")
            handoff_id = f"h{state['next']}"
            state["next"] += 1
            evidence = "交接内容已备好，正在交给 Agent" if direct else "交接内容已备好，等你复制到对话里"
            handoff.update({
                "id": handoff_id, "createdAt": now(), "status": "需手动完成", "unknown": False,
                "note": "", "direct": direct, "results": [],
                "events": [{"time": now(), "status": "需手动完成", "evidence": evidence}],
            })
            state["handoffs"].append(handoff)
        if direct:
            self.deliver(handoff_id, target, handoff["text"], config)
        return {"ok": True, "id": handoff_id}

    def deliver(self, handoff_id: str, target: dict, text: str, config: dict) -> None:
        """同步完成 initialize → thread/resume → turn/start；之后在后台等这一轮结束。"""
        try:
            session = self.session_factory(self.codex_locator())
        except (WorkbenchError, OSError) as exc:
            message = exc.message if isinstance(exc, WorkbenchError) else str(exc)
            with self.store.transaction() as state:
                set_status(find_handoff(state, handoff_id), "失败", f"没能启动 Codex：{message[:200]}")
            return
        try:
            turn_id = session.start_turn(target["thread"], text)
        except Exception as exc:
            sent = getattr(session, "turn_sent", False)
            session.close()
            with self.store.transaction() as state:
                handoff = find_handoff(state, handoff_id)
                if is_settled(handoff):
                    # 等 Codex 回应期间用户已经有了结论（例如自己带回了结果），不改它
                    add_note(handoff, f"工作台交给 Codex 时出错（{str(exc)[:120]}），上面的结论不变")
                elif isinstance(exc, CodexReplyError):
                    # Codex 明确拒绝了这次请求（例如对话不存在），这条消息没有被接受
                    set_status(handoff, "失败", f"Codex 明确报错，没有接受这条消息：{str(exc)[:200]}")
                elif sent:
                    # 消息已经写给 Codex，只是没等到回应：可能已经交出，不能说失败，也不能说已发送
                    mark_unknown(handoff, f"消息可能已经交给 Codex，但没等到确认（{str(exc)[:120]}）")
                    add_note(handoff, "可能已经交出：请先到 Codex 对话里看一眼，确认没有这条消息再重新发起，避免重复")
                else:
                    set_status(handoff, "失败", f"还没把消息交给 Codex 就出错了，没有交出去：{str(exc)[:200]}")
            return
        minutes = config.get("codex_turn_minutes", DEFAULT_TURN_MINUTES)
        watcher = threading.Thread(target=self.watch, args=(session, handoff_id, target, turn_id, minutes), daemon=True)
        with self.store.lock:
            try:
                with self.store.transaction() as state:
                    handoff = find_handoff(state, handoff_id)
                    handoff.setdefault("delivery", {}).update({"adapter": "codex", "turn": turn_id})
                    set_status(handoff, "已发送", "Codex 接受了这条消息")
                watcher.start()
            except RuntimeError:
                session.close()
                with self.store.transaction() as state:
                    refresh_failed(find_handoff(state, handoff_id), "未能启动本机监听，请重新查询这一轮")
                return
            except Exception:
                session.close()
                raise
            self.watched_handoffs.add(handoff_id)

    def watch(self, session, handoff_id: str, target: dict, turn_id: str, minutes: int) -> None:
        def started():
            with self.store.transaction() as state:
                set_status(find_handoff(state, handoff_id), "已开始", "Codex 报告这一轮已开始")

        def progress(text):
            with self.store.transaction() as state:
                handoff = find_handoff(state, handoff_id)
                if not is_settled(handoff):
                    handoff["progress"] = {"text": text, "time": now()}

        outcome, items = "exit", []
        try:
            outcome, items = session.wait_turn(turn_id, minutes, started, progress)
        except Exception:
            pass  # 读不到结局就保留 exit，按“无法确认”处理
        finally:
            try:
                session.close()
            finally:
                with self.store.lock:
                    self.watched_handoffs.discard(handoff_id)
                    with self.store.transaction() as state:
                        handoff = find_handoff(state, handoff_id)
                        if session.declined:
                            add_note(handoff, f"这一轮里 Codex 请求了 {session.declined} 次权限或输入，工作台都拒绝了；需要的话请到 Codex 里处理")
                        apply_outcome(handoff, target, outcome, items)

    def refresh(self, handoff_id: str) -> None:
        """优先使用原会话的实时事件。仅在没有本机监听时按原轮次重新查询。"""
        with self.store.lock:
            if handoff_id in self.watched_handoffs:
                return
            config = self.config()
            handoff = find_handoff(self.store.read(), handoff_id)
            target = config["targets"].get(handoff["target"])
            turn_id = (handoff.get("delivery") or {}).get("turn")
            if handoff.get("direct") and not turn_id:
                raise WorkbenchError(409, "没拿到 Codex 这一轮的编号，没法重新查询；请先到 Codex 对话里看是否已收到这条消息，再决定粘贴结果还是重新发起")
            if not target or target.get("adapter") != "codex" or not turn_id:
                raise WorkbenchError(400, "这条交接不是直接交给 Codex 的，请手动粘贴结果")
        try:
            session = self.session_factory(self.codex_locator())
        except (WorkbenchError, OSError) as exc:
            with self.store.transaction() as state:
                refresh_failed(find_handoff(state, handoff_id), f"查不到最新情况：{str(exc)[:200]}")
            return
        try:
            session.request("initialize", {"clientInfo": {"name": "personal-agent-workbench", "title": "Personal Agent Workbench", "version": "0.2.0"}})
            data = session.request("thread/read", {"threadId": target["thread"], "includeTurns": True})
        except Exception as exc:
            with self.store.transaction() as state:
                refresh_failed(find_handoff(state, handoff_id), f"查不到最新情况：{str(exc)[:200]}")
            return
        finally:
            session.close()
        turns = (data.get("thread") or {}).get("turns") or []
        turn = next((t for t in turns if isinstance(t, dict) and t.get("id") == turn_id), None)
        with self.store.transaction() as state:
            handoff = find_handoff(state, handoff_id)
            if turn is None:
                refresh_failed(handoff, "对话里找不到这一轮")
            else:
                apply_outcome(handoff, target, turn, turn.get("items") or [])

    def act(self, handoff_id: str, action: str, payload) -> dict:
        if not HANDOFF_ID_RE.match(handoff_id):
            raise WorkbenchError(400, "交接编号不合规")
        payload = payload if isinstance(payload, dict) else {}
        if action == "refresh":
            self.refresh(handoff_id)
            return {"ok": True}
        with self.store.transaction() as state:
            handoff = find_handoff(state, handoff_id)
            if action == "pasted":
                if handoff.get("direct") or handoff["status"] != "需手动完成":
                    raise WorkbenchError(409, "只有等你手动粘贴的交接，才能记为已粘贴")
                set_status(handoff, "已发送", "你确认已粘贴到对话里")
            elif action == "result":
                text = text_field(payload, "text", MAX_RESULT_TEXT, required=True)
                add_result(handoff, text, "你粘贴回来的结果")
            elif action == "done":
                note = text_field(payload, "note", MAX_SHORT, required=True)
                if handoff["status"] != "待确认":
                    raise WorkbenchError(409, "只有等你决定的事，才能由你记为已处理")
                set_status(handoff, "已完成", f"你确认：{note}")
            elif action == "failed":
                reason = text_field(payload, "reason", MAX_SHORT, required=True)
                set_status(handoff, "失败", f"你记下的失败信息：{reason}")
            else:
                raise WorkbenchError(404, "没有这个操作")
        return {"ok": True}


# ---------------------------------------------------------------- HTTP


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, workbench: Workbench, port: int):
        super().__init__((HOST, port), Handler)
        self.workbench = workbench
        self.port = self.server_address[1]
        self.token = secrets.token_urlsafe(32)
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.allowed_origins = {"http://" + host for host in self.allowed_hosts}


class Handler(BaseHTTPRequestHandler):
    server: WorkbenchServer
    server_version = "workbench"
    sys_version = ""

    def log_message(self, format, *args):  # 只记方法、路径和状态码，不记请求内容
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), format % args))

    def reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, status: int, data: dict) -> None:
        self.reply(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def guard(self, write: bool) -> None:
        """接口共用校验：Host、来源、令牌。同源 GET 浏览器可能不带 Origin，此时看 Sec-Fetch-Site。"""
        if not self.host_ok():
            raise WorkbenchError(403, "Host 不被允许")
        origin = self.headers.get("Origin")
        if (write or origin is not None) and origin not in self.server.allowed_origins:
            raise WorkbenchError(403, "来源不被允许")
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            raise WorkbenchError(403, "来源不被允许")
        given = self.headers.get("X-Workbench-Token", "").encode("utf-8", "replace")
        if not hmac.compare_digest(given, self.server.token.encode("utf-8")):
            raise WorkbenchError(403, "令牌无效，请刷新页面")

    def read_body(self):
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise WorkbenchError(415, "需要 JSON 请求")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise WorkbenchError(411, "需要 Content-Length") from None
        if length < 0 or length > MAX_BODY:
            raise WorkbenchError(413, "请求太大")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WorkbenchError(400, "JSON 无法解析") from None

    def handle_api(self, method: str) -> None:
        path = urllib.parse.urlsplit(self.path).path
        parts = [urllib.parse.unquote(p) for p in path.split("/")[2:]]
        workbench = self.server.workbench
        try:
            self.guard(write=method != "GET")
            payload = self.read_body() if method != "GET" else None
            if method == "GET" and parts == ["state"]:
                return self.reply_json(200, workbench.public_state())
            if len(parts) == 2 and parts[0] == "records" and method == "PUT":
                return self.reply_json(200, workbench.put_record(parts[1], payload))
            if len(parts) == 2 and parts[0] == "records" and method == "DELETE":
                return self.reply_json(200, workbench.delete_record(parts[1], payload))
            if parts == ["runs"] and method == "POST":
                return self.reply_json(200, workbench.create_handoff(payload, require_direct=True))
            if parts == ["handoffs"] and method == "POST":
                return self.reply_json(200, workbench.create_handoff(payload))
            if len(parts) == 3 and parts[0] == "handoffs" and method == "POST":
                return self.reply_json(200, workbench.act(parts[1], parts[2], payload))
            raise WorkbenchError(404, "未找到")
        except WorkbenchError as exc:
            return self.reply_json(exc.status, {"ok": False, "error": exc.message})

    def serve_file(self) -> None:
        """只提供 page/ 下的页面文件和接口封装；配置、状态、日志和其它文件一律不提供。"""
        if not self.host_ok():
            return self.reply_json(403, {"ok": False, "error": "Host 不被允许"})
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path == "/_workbench/client.js":
            return self.reply(200, CLIENT_JS.read_bytes(), PAGE_TYPES[".js"])
        rel = path.lstrip("/") or "index.html"
        parts = Path(rel).parts
        suffix = Path(rel).suffix.lower()
        if not parts or "\\" in rel or "\x00" in rel or any(p.startswith(".") for p in parts) or suffix not in PAGE_TYPES:
            return self.reply_json(404, {"ok": False, "error": "未找到"})
        page_dir = self.server.workbench.page_dir
        target = (page_dir / rel).resolve()
        if page_dir not in target.parents or not target.is_file():
            return self.reply_json(404, {"ok": False, "error": "未找到"})
        if any(p.startswith(".") for p in target.relative_to(page_dir).parts) or target.suffix.lower() != suffix:
            return self.reply_json(404, {"ok": False, "error": "未找到"})
        content = target.read_bytes()
        if suffix == ".html":
            meta = f'<meta name="workbench-token" content="{self.server.token}">'.encode()
            match = HEAD_RE.search(content)
            content = content[: match.end()] + meta + content[match.end():] if match else meta + content
        return self.reply(200, content, PAGE_TYPES[suffix])

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.handle_api("GET")
        return self.serve_file()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self.handle_api("POST")
        return self.reply_json(404, {"ok": False, "error": "未找到"})

    def do_PUT(self):
        if self.path.startswith("/api/"):
            return self.handle_api("PUT")
        return self.reply_json(404, {"ok": False, "error": "未找到"})

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            return self.handle_api("DELETE")
        return self.reply_json(404, {"ok": False, "error": "未找到"})


# ---------------------------------------------------------------- 命令行


def check_dir(root: Path) -> list[str]:
    """交付前自查：配置有效、首页存在、页面不引用外部地址。"""
    problems: list[str] = []
    problem = page_dir_problem(root)
    if problem:
        return [problem]
    try:
        load_config(root)
    except WorkbenchError as exc:
        problems.append(exc.message)
    page_dir = root / "page"
    if not (page_dir / "index.html").is_file():
        problems.append("缺少 page/index.html")
    if page_dir.is_dir():
        for path in sorted(page_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in (".html", ".css", ".js", ".svg"):
                if EXTERNAL_REF.search(path.read_text(encoding="utf-8", errors="ignore")):
                    problems.append(f"{path.relative_to(root)} 引用了外部地址，工作台只能用本机资源")
    return problems


def make_server(root: Path, port: int | None) -> WorkbenchServer:
    workbench = Workbench(root)
    workbench.config()
    if port is not None:
        return WorkbenchServer(workbench, port)
    last_error = None
    for candidate in range(DEFAULT_PORT, DEFAULT_PORT + PORT_TRIES):
        try:
            return WorkbenchServer(workbench, candidate)
        except OSError as exc:
            last_error = exc
    raise SystemExit(f"找不到空闲端口：{last_error}")


def cmd_serve(args) -> int:
    root = Path(args.dir).expanduser()
    problems = check_dir(root)
    if problems:
        print("工作台还不能打开：\n" + "\n".join(f"- {p}" for p in problems), file=sys.stderr)
        return 1
    server = make_server(root, args.port)
    url = f"http://127.0.0.1:{server.port}/"
    print(f"工作台已打开：{url}（只有这台电脑能访问）。按 Ctrl+C 或关掉这个窗口就会停止。", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_check(args) -> int:
    problems = check_dir(Path(args.dir).expanduser())
    if problems:
        print("检查没有通过：\n" + "\n".join(f"- {p}" for p in problems))
        return 1
    print("检查通过：配置有效，首页存在，页面没有引用外部地址。")
    return 0


def cmd_codex_probe(args) -> int:
    """核实 Codex 的发起和取回：向用户同意的测试对话发一句无害的话，再读回回复。"""
    if not UUID_RE.match(args.thread):
        print("对话编号格式不对（应为 UUID）", file=sys.stderr)
        return 2
    try:
        session = CodexSession(find_codex())
    except (WorkbenchError, OSError) as exc:
        print(f"没能启动 Codex：{getattr(exc, 'message', exc)}", file=sys.stderr)
        return 1
    try:
        turn_id = session.start_turn(args.thread, args.message)
        print(f"发起：Codex 接受了这条消息（这一轮的编号 {turn_id}）", flush=True)
        outcome, items = session.wait_turn(turn_id, args.minutes)
    except Exception as exc:
        if session.turn_sent and not isinstance(exc, CodexReplyError):
            print(f"发起：消息可能已经交出，但没等到确认，无法确认（{str(exc)[:200]}）", file=sys.stderr)
        else:
            print(f"发起失败：{str(exc)[:300]}", file=sys.stderr)
        return 1
    finally:
        session.close()
    if not isinstance(outcome, dict):
        print("取回：没有等到这一轮结束，无法确认" if outcome == "timeout" else "取回：连接断了，无法确认")
        return 1
    text = final_text(items + (outcome.get("items") or []))
    print(f"取回：这一轮的状态是 {outcome.get('status')}；回复：{text[:500] or '（没有找到回复文字）'}")
    return 0 if outcome.get("status") == "completed" and text else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="个人工作台的本机运行支持（只监听 127.0.0.1）")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="打开工作台")
    serve.add_argument("--dir", required=True, help="工作台目录（含 workbench.json 和 page/）")
    serve.add_argument("--port", type=int, help="指定端口；不写就从 48760 开始找空闲端口")
    serve.add_argument("--open", action="store_true", help="启动后用默认浏览器打开")
    serve.set_defaults(func=cmd_serve)
    check = sub.add_parser("check", help="检查工作台目录")
    check.add_argument("--dir", required=True)
    check.set_defaults(func=cmd_check)
    probe = sub.add_parser("codex-probe", help="经用户同意，向测试对话发一句话并读回回复，用来核实能力")
    probe.add_argument("--thread", required=True, help="用户同意用于测试的 Codex 对话编号")
    probe.add_argument("--message", default=PROBE_MESSAGE)
    probe.add_argument("--minutes", type=int, default=10)
    probe.set_defaults(func=cmd_codex_probe)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
