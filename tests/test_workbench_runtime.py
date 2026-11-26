"""工作台本机运行支持（skills/personal-agent-workbench/scripts/workbench.py）的行为测试。

只用标准库。所有工作台目录都建在系统临时目录里；Codex 用假会话代替，不启动真实进程。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.client
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "personal-agent-workbench" / "scripts"
THREAD = "00000000-0000-4000-8000-" + "0" * 11 + "1"  # 测试用的假编号，运行时拼出

spec = importlib.util.spec_from_file_location("workbench", SCRIPTS / "workbench.py")
wb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wb)


def make_dir(base: Path, targets: dict | None = None, page: str | None = None) -> Path:
    root = base / "工作台"
    (root / "page").mkdir(parents=True)
    config = {"targets": targets or {"draft": {"label": "起草对话", "adapter": "manual"}}}
    (root / "workbench.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    (root / "page" / "index.html").write_text(page or "<!doctype html><html><head><title>t</title></head><body>页</body></html>", encoding="utf-8")
    return root


class FakeSession:
    """模拟 codex app-server 会话：按脚本给出结局。"""

    def __init__(self, outcome="completed", text="草稿：示例回复", start_error=None, declined=0, started=True,
                 no_reply=None):
        self.outcome = outcome
        # no_reply：None 表示正常；"before" 表示 turn/start 发出前就没了回应；"after" 表示 turn/start 已发出后没了回应
        self.no_reply = no_reply
        self.turn_sent = False
        self.text = text
        self.start_error = start_error
        self.declined = declined
        self.started = started
        self.sent = []
        self.closed = False

    def start_turn(self, thread_id, text, **_options):
        if self.start_error:
            raise wb.CodexReplyError(self.start_error)
        if self.no_reply == "before":
            raise wb.CodexNoReply("Codex 没有在 60 秒内响应 thread/resume")
        self.turn_sent = True
        if self.no_reply == "after":
            raise wb.CodexNoReply("Codex 进程提前退出")
        if self.no_reply == "after-timeout":
            raise wb.CodexNoReply("Codex 没有在 60 秒内响应 turn/start")
        self.sent.append((thread_id, text))
        return "turn-1"

    def wait_turn(self, turn_id, minutes, on_started=None, on_progress=None):
        if self.started and on_started:
            on_started()
        if self.outcome in ("timeout", "exit"):
            return self.outcome, []
        items = [{"type": "agentMessage", "phase": "commentary", "text": "我先看看"}]
        if self.text:
            items.append({"type": "agentMessage", "phase": "final_answer", "text": self.text})
        turn = {"id": turn_id, "status": self.outcome}
        if self.outcome == "failed":
            turn["error"] = {"message": "模型不可用"}
        return turn, items

    def request(self, method, params, timeout=0):
        return {"thread": {"turns": [{"id": "turn-1", "status": "completed", "items": [{"type": "agentMessage", "text": "刷新取回"}]}]}}

    def close(self):
        self.closed = True


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


CODEX_VERIFIED = {
    "label": "测试对话", "adapter": "codex", "thread": THREAD,
    "capabilities": {"发起工作": "已核实（测试）", "继续同一工作": "已核实（测试）", "取回结果": "已核实（测试）"},
}


class LogicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def bench(self, targets=None, session=None):
        root = make_dir(self.base, targets)
        self.sessions = []

        def factory(_bin):
            self.sessions.append(session)
            return session

        return wb.Workbench(root, session_factory=factory, codex_locator=lambda: "codex")

    def handoff(self, bench, **extra):
        payload = {"target": "draft", "intent": "new", "ask": "起草一段回复", "expect": "一段回复草稿", **extra}
        return bench.create_handoff(payload)["id"]

    def get(self, bench, handoff_id):
        return wb.find_handoff(bench.store.read(), handoff_id)

    def test_run_requires_verified_dispatch_and_return(self):
        payload = {"target": "draft", "intent": "new", "ask": "核对记录", "expect": "核对结论"}
        targets = {
            "send_only": dict(CODEX_VERIFIED, capabilities={"发起工作": "已核实"}),
            "read_only": dict(CODEX_VERIFIED, capabilities={"取回结果": "已核实"}),
            "manual": {"label": "旧工作台", "adapter": "manual"},
        }
        bench = self.bench(targets, FakeSession())
        for key in targets:
            with self.subTest(key=key):
                payload["target"] = key
                with self.assertRaises(wb.WorkbenchError) as error:
                    bench.create_handoff(payload, require_direct=True)
                self.assertEqual(error.exception.status, 409)
                self.assertEqual(bench.store.read()["handoffs"], [])
                self.assertEqual(self.sessions, [])

    def test_manual_handoff_starts_as_needs_manual(self):
        """手动交接生成可复制内容，状态为需手动完成，不会显示为已发送。"""
        bench = self.bench()
        item = self.get(bench, self.handoff(bench))
        self.assertEqual(item["status"], "需手动完成")
        self.assertFalse(item["direct"])
        self.assertIn("【去向】新工作", item["text"])
        self.assertIn("【完成后给我】一段回复草稿", item["text"])

    def test_manual_path_paste_then_result(self):
        """用户确认粘贴后才是已发送；粘贴回结果后才是已完成，结果挂在原交接上。"""
        bench = self.bench()
        handoff_id = self.handoff(bench)
        bench.act(handoff_id, "pasted", {})
        self.assertEqual(self.get(bench, handoff_id)["status"], "已发送")
        with self.assertRaises(wb.WorkbenchError):
            bench.act(handoff_id, "pasted", {})
        bench.act(handoff_id, "result", {"text": "对话里的结果"})
        item = self.get(bench, handoff_id)
        self.assertEqual(item["status"], "已完成")
        self.assertEqual(item["results"][0]["source"], "你粘贴回来的结果")

    def test_confirm_after_goes_to_pending_and_only_user_closes(self):
        """需要用户拍板的事拿到结果后是待确认；只有用户自己记下处理结果才变为已完成。"""
        bench = self.bench()
        handoff_id = self.handoff(bench, confirmAfter=True)
        with self.assertRaises(wb.WorkbenchError):
            bench.act(handoff_id, "done", {"note": "我已自己发出"})
        bench.act(handoff_id, "result", {"text": "草稿"})
        self.assertEqual(self.get(bench, handoff_id)["status"], "待确认")
        bench.act(handoff_id, "done", {"note": "我已自己发出"})
        item = self.get(bench, handoff_id)
        self.assertEqual(item["status"], "已完成")
        self.assertIn("你确认：我已自己发出", item["events"][-1]["evidence"])

    def test_destination_must_be_explicit(self):
        """没有去向不能交接；继续或修改必须说明是哪一件。"""
        bench = self.bench()
        with self.assertRaises(wb.WorkbenchError):
            bench.create_handoff({"target": "draft", "ask": "改一下", "expect": "结果"})
        with self.assertRaises(wb.WorkbenchError):
            bench.create_handoff({"target": "draft", "intent": "revise", "ask": "改一下", "expect": "结果"})
        handoff_id = bench.create_handoff({"target": "draft", "intent": "revise", "about": "某份草稿第 2 版", "ask": "语气温和些", "expect": "新版本"})["id"]
        self.assertIn("修改结果：某份草稿第 2 版（请粘贴到原来那个对话里）", self.get(bench, handoff_id)["text"])

    def test_target_whitelist(self):
        """只能交给配置里的目标；请求里带的对话编号不会被使用。"""
        bench = self.bench()
        with self.assertRaises(wb.WorkbenchError) as caught:
            bench.create_handoff({"target": "other", "intent": "new", "ask": "a", "expect": "b", "thread": THREAD})
        self.assertEqual(caught.exception.status, 403)

    def test_status_never_moves_backwards(self):
        """线上的状态只往前走；拿不到最新情况时保留原状态，只标明最新情况未知。"""
        item = {"status": "已开始", "unknown": False, "events": []}
        self.assertFalse(wb.set_status(item, "已发送", "迟到的旧消息"))
        self.assertEqual(item["status"], "已开始")
        wb.mark_unknown(item, "连接断了")
        self.assertEqual(item["status"], "已开始")
        self.assertTrue(item["unknown"])

    def test_codex_unverified_falls_back_to_manual(self):
        """Codex 目标的能力没核实时，不直接发送，改为可复制的交接内容。"""
        target = dict(CODEX_VERIFIED, capabilities={"发起工作": "待核实"})
        bench = self.bench({"draft": target}, FakeSession())
        item = self.get(bench, self.handoff(bench))
        self.assertEqual(item["status"], "需手动完成")
        self.assertEqual(self.sessions, [])

    def test_codex_verified_full_path(self):
        """能力已核实时直接交给配置里的对话：已发送 → 已开始 → 拿到回复后已完成。"""
        session = FakeSession()
        bench = self.bench({"draft": CODEX_VERIFIED}, session)
        handoff_id = bench.create_handoff({"target": "draft", "intent": "new", "ask": "起草回复", "expect": "回复草稿"}, require_direct=True)["id"]
        self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["status"] == "已完成"))
        item = self.get(bench, handoff_id)
        self.assertEqual([e["status"] for e in item["events"]], ["需手动完成", "已发送", "已开始", "已完成"])
        self.assertEqual(item["results"][0]["text"], "草稿：示例回复")
        self.assertEqual(session.sent[0][0], THREAD)
        self.assertTrue(session.closed)
        with self.assertRaises(wb.WorkbenchError):
            bench.act(handoff_id, "pasted", {})

    def test_completed_commentary_and_final_stay_with_their_turn(self):
        """仅保存本轮最新的完整进度，结束时取回本轮结论。"""
        def message(text, phase="commentary"):
            return {"type": "agentMessage", "phase": phase, "text": text}

        def event(text, turn="turn-1", method="item/completed"):
            return {"method": method, "params": {"turnId": turn, "item": message(text)}}

        finish = threading.Event()

        class EventSession(FakeSession):
            wait_turn = wb.CodexSession.wait_turn

            def next_event(self, deadline):
                return next(events)

        def notifications():
            yield event("正在核对")
            yield event("已核对两项")
            yield event("其他工作", turn="turn-2")
            yield event("半句话", method="item/agentMessage/delta")
            yield event("  ")
            finish.wait(3)
            yield {"method": "turn/completed", "params": {"turn": {
                "id": "turn-1", "status": "completed", "items": [message("本轮结论", "final_answer")]
            }}}

        events = notifications()
        bench = self.bench({"draft": CODEX_VERIFIED}, EventSession())
        handoff_id = self.handoff(bench, ref="示例对象")
        try:
            self.assertTrue(wait_for(lambda: self.get(bench, handoff_id).get("progress", {}).get("text") == "已核对两项"))
            item = wb.Workbench(bench.root).public_state()["handoffs"][0]
            self.assertEqual(item["ref"], "示例对象")
            self.assertEqual(item["progress"]["text"], "已核对两项")
            self.assertTrue(item["progress"]["time"])
            self.assertEqual(item["results"], [])
        finally:
            finish.set()
        self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["status"] == "已完成"))
        item = self.get(bench, handoff_id)
        self.assertEqual(item["progress"]["text"], "已核对两项")
        self.assertEqual([result["text"] for result in item["results"]], ["本轮结论"])
        legacy = {"type": "agentMessage", "text": "旧版回复"}
        self.assertEqual(wb.final_text([message("结论", "final"), legacy, message("进展")]), "结论")
        self.assertEqual(wb.final_text([legacy, message("进展")]), "旧版回复")
        self.assertEqual(wb.final_text([message("进展")]), "")

    def test_codex_without_result_capability_does_not_claim_done(self):
        """取回结果没核实时，这一轮结束也只记已开始，并提示用户手动带回结果。"""
        target = dict(CODEX_VERIFIED, capabilities={"发起工作": "已核实", "继续同一工作": "已核实", "取回结果": "待核实"})
        bench = self.bench({"draft": target}, FakeSession())
        handoff_id = self.handoff(bench)
        self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["note"]))
        item = self.get(bench, handoff_id)
        self.assertEqual(item["status"], "已开始")
        self.assertEqual(item["results"], [])

    def test_codex_failures_and_unknowns(self):
        """明确报错才算失败；连接断了或超时是最新情况未知，不写成失败。"""
        cases = [
            (FakeSession(start_error="对话不存在"), "失败", False),
            (FakeSession(no_reply="before"), "失败", False),
            (FakeSession(no_reply="after"), "需手动完成", True),
            (FakeSession(no_reply="after-timeout"), "需手动完成", True),
            (FakeSession(outcome="failed"), "失败", False),
            (FakeSession(outcome="exit", started=False), "已发送", True),
            (FakeSession(outcome="timeout"), "已开始", True),
            (FakeSession(text=""), "已开始", True),
        ]
        for index, (session, status, unknown) in enumerate(cases):
            with self.subTest(index=index):
                base = self.base / str(index)
                base.mkdir()
                root = make_dir(base, {"draft": CODEX_VERIFIED})
                bench = wb.Workbench(root, session_factory=lambda _b, s=session: s, codex_locator=lambda: "codex")
                handoff_id = self.handoff(bench)
                self.assertTrue(wait_for(lambda: session.closed))
                self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["status"] == status))
                time.sleep(0.05)
                item = self.get(bench, handoff_id)
                self.assertEqual(item["status"], status)
                self.assertEqual(item["unknown"], unknown)
                if session.no_reply in ("after", "after-timeout"):
                    # 消息可能已经交出：不写失败，不写已发送，并提醒先去 Codex 里看再决定是否重发
                    self.assertNotIn("没有交出去", json.dumps(item["events"], ensure_ascii=False))
                    self.assertIn("可能已经交出", item["note"])

    def test_declined_requests_are_reported(self):
        """Codex 请求权限时工作台一律拒绝，并在交接上写明。"""
        bench = self.bench({"draft": CODEX_VERIFIED}, FakeSession(declined=2))
        handoff_id = self.handoff(bench)
        self.assertTrue(wait_for(lambda: "拒绝" in self.get(bench, handoff_id)["note"]))
        for method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                       "execCommandApproval", "applyPatchApproval", "mcpServer/elicitation/request",
                       "item/permissions/requestApproval", "item/tool/requestUserInput"):
            reply = wb.decline_server_request({"id": 7, "method": method})
            text = json.dumps(reply)
            self.assertNotIn("accept", text)
            self.assertNotIn("approved", text)
        # 旧版审批方法按协议用对象形式拒绝
        self.assertEqual(wb.decline_server_request({"id": 1, "method": "execCommandApproval"})["result"]["decision"]["denied"]["rejection"],
                         "工作台不替用户批准任何操作")

    def test_refresh_reads_turn(self):
        """重新查询时按这一轮的编号找结果。"""
        session = FakeSession(outcome="exit", started=False)
        root = make_dir(self.base, {"draft": CODEX_VERIFIED})
        sessions = [session, FakeSession()]
        bench = wb.Workbench(root, session_factory=lambda _b: sessions.pop(0), codex_locator=lambda: "codex")
        handoff_id = self.handoff(bench)
        self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["unknown"]))
        bench.act(handoff_id, "refresh", {})
        item = self.get(bench, handoff_id)
        self.assertEqual(item["status"], "已完成")
        self.assertEqual(item["results"][-1]["text"], "刷新取回")

    def test_parallel_refresh_uses_live_watcher_until_session_closed(self):
        """监听和关闭会话期间，并发查询都从原会话取得进展；退出后才允许补查。"""
        closing, release = threading.Event(), threading.Event()

        class ClosingSession(FakeSession):
            def close(self):
                closing.set()
                release.wait(3)
                super().close()

        slow = ClosingSession(text="后台取回")
        bench = self.bench({"draft": CODEX_VERIFIED}, slow)
        handoff_id = self.handoff(bench, confirmAfter=True)
        try:
            self.assertTrue(closing.wait(3))
            with ThreadPoolExecutor(max_workers=4) as pool:
                replies = list(pool.map(lambda _: bench.act(handoff_id, "refresh", {}), range(4)))
            self.assertEqual(replies, [{"ok": True}] * 4)
            self.assertEqual(self.sessions, [slow])
            self.assertEqual(self.get(bench, handoff_id)["status"], "已开始")
        finally:
            release.set()
        self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["status"] == "待确认"))
        bench.act(handoff_id, "done", {"note": "我已自己发出"})
        bench.act(handoff_id, "refresh", {})
        self.assertEqual(len(self.sessions), 2)
        item = self.get(bench, handoff_id)
        self.assertEqual(item["status"], "已完成")
        self.assertEqual([r["text"] for r in item["results"]], ["后台取回"])

    def test_page_dir_must_be_real_folder(self):
        """page/ 是指向工作台以外的软链接时拒绝启动，检查也不通过。"""
        root = self.base / "工作台"
        root.mkdir()
        (root / "workbench.json").write_text(json.dumps({"targets": {"draft": {"label": "起草对话"}}}), encoding="utf-8")
        outside = self.base / "别处"
        outside.mkdir()
        (outside / "index.html").write_text("<p>别处</p>", encoding="utf-8")
        (root / "page").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(wb.WorkbenchError):
            wb.Workbench(root)
        self.assertTrue(any("软链接" in p for p in wb.check_dir(root)))

    def test_late_started_event_keeps_pending_confirmation(self):
        """用户已粘贴结果、等用户确认时，后台迟到的“已开始”不能把状态拉回去，用户之后仍能确认。"""
        release = threading.Event()

        class SlowSession(FakeSession):
            def wait_turn(self, turn_id, minutes, on_started=None, on_progress=None):
                release.wait(3)
                return super().wait_turn(turn_id, minutes, on_started, on_progress)

        slow = SlowSession(text="后台取回")
        root = make_dir(self.base, {"draft": CODEX_VERIFIED})
        sessions = [slow, FakeSession()]
        bench = wb.Workbench(root, session_factory=lambda _b: sessions.pop(0), codex_locator=lambda: "codex")
        handoff_id = self.handoff(bench, confirmAfter=True)
        bench.act(handoff_id, "refresh", {})
        self.assertEqual(len(sessions), 1)
        bench.act(handoff_id, "result", {"text": "我带回的回复"})
        release.set()
        self.assertTrue(wait_for(lambda: slow.closed))
        time.sleep(0.05)
        item = self.get(bench, handoff_id)
        self.assertEqual(item["status"], "待确认")
        self.assertEqual(len(item["results"]), 1)
        bench.act(handoff_id, "done", {"note": "我已自己发出"})
        self.assertEqual(self.get(bench, handoff_id)["status"], "已完成")

    def test_long_result_is_marked_partial(self):
        """结果恰好到上限时照常保存；超过上限时只存前面部分，用 truncated 和提示标明不完整，状态照常。"""
        for extra, status in ((0, "已完成"), (4, "已完成")):
            with self.subTest(extra=extra):
                base = self.base / str(extra)
                base.mkdir()
                session = FakeSession(text="字" * wb.MAX_RESULT_TEXT + "结论在此"[:extra])
                root = make_dir(base, {"draft": CODEX_VERIFIED})
                bench = wb.Workbench(root, session_factory=lambda _b, s=session: s, codex_locator=lambda: "codex")
                handoff_id = self.handoff(bench)
                self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["results"]))
                time.sleep(0.05)
                item = self.get(bench, handoff_id)
                self.assertEqual(item["status"], status)
                self.assertEqual(len(item["results"][-1]["text"]), wb.MAX_RESULT_TEXT)
                if extra:
                    self.assertTrue(item["results"][-1]["truncated"])
                    self.assertIn("只保存了前", item["note"])
                else:
                    self.assertNotIn("truncated", item["results"][-1])

    def test_failed_refresh_keeps_settled_result(self):
        """已经取回结果后，重新查询失败只提示这次没查到，不把结论改成最新情况未知。"""

        class BrokenRead(FakeSession):
            def request(self, method, params, timeout=0):
                raise wb.CodexNoReply("Codex 进程提前退出")

        done = FakeSession()
        root = make_dir(self.base, {"draft": CODEX_VERIFIED})
        sessions = [done, BrokenRead()]
        bench = wb.Workbench(root, session_factory=lambda _b: sessions.pop(0), codex_locator=lambda: "codex")
        handoff_id = self.handoff(bench)
        self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["status"] == "已完成"))
        bench.act(handoff_id, "refresh", {})
        item = self.get(bench, handoff_id)
        self.assertEqual(item["status"], "已完成")
        self.assertFalse(item["unknown"])
        self.assertIn("没有成功", item["note"])

    def test_manual_result_then_refresh_keeps_user_decision(self):
        """用户先手动带回 Codex 的回复并确认已处理，之后重新查询读到同一轮：不加一版、不撤销确认，只提示是否一致。"""
        for refreshed, hint in (("刷新取回", "一致"), ("另一段回复", "不同")):
            with self.subTest(refreshed=refreshed):
                base = self.base / hint
                base.mkdir()

                class Refreshed(FakeSession):
                    def request(self, method, params, timeout=0, _text=refreshed):
                        return {"thread": {"turns": [{"id": "turn-1", "status": "completed",
                                                      "items": [{"type": "agentMessage", "text": _text}]}]}}

                waiting = FakeSession(outcome="timeout")
                root = make_dir(base, {"draft": CODEX_VERIFIED})
                sessions = [waiting, Refreshed()]
                bench = wb.Workbench(root, session_factory=lambda _b: sessions.pop(0), codex_locator=lambda: "codex")
                handoff_id = self.handoff(bench, confirmAfter=True)
                self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["unknown"]))
                bench.act(handoff_id, "result", {"text": "刷新取回"})
                bench.act(handoff_id, "done", {"note": "我已自己发出"})
                bench.act(handoff_id, "refresh", {})
                item = self.get(bench, handoff_id)
                self.assertEqual(item["status"], "已完成")
                self.assertEqual([r["text"] for r in item["results"]], ["刷新取回"])
                self.assertIn(hint, item["note"])

    def test_delivery_error_keeps_user_decision(self):
        """交给 Codex 还没返回时用户已带回结果并确认，之后交付出错：不改用户的结论，只追加提示。"""
        for kind in ("reply-error", "no-reply-after"):
            with self.subTest(kind=kind):
                base = self.base / kind
                base.mkdir()
                entered, release = threading.Event(), threading.Event()

                class HangingStart(FakeSession):
                    def start_turn(self, thread_id, text, _kind=kind, **_options):
                        entered.set()
                        release.wait(3)
                        if _kind == "reply-error":
                            raise wb.CodexReplyError("对话不存在")
                        self.turn_sent = True
                        raise wb.CodexNoReply("Codex 进程提前退出")

                root = make_dir(base, {"draft": CODEX_VERIFIED})
                bench = wb.Workbench(root, session_factory=lambda _b: HangingStart(), codex_locator=lambda: "codex")
                worker = threading.Thread(target=self.handoff, args=(bench,), kwargs={"confirmAfter": True})
                worker.start()
                self.assertTrue(entered.wait(3))
                handoff_id = bench.store.read()["handoffs"][-1]["id"]
                bench.act(handoff_id, "result", {"text": "我从对话里复制的回复"})
                bench.act(handoff_id, "done", {"note": "我已自己发出"})
                release.set()
                worker.join(3)
                item = self.get(bench, handoff_id)
                self.assertEqual(item["status"], "已完成")
                self.assertFalse(item["unknown"])
                self.assertIn("结论不变", item["note"])

    def test_refresh_without_turn_id_explains(self):
        """消息可能已交出但没拿到这一轮编号时，重新查询说明原因，不让用户误以为这是手动交接。"""
        root = make_dir(self.base, {"draft": CODEX_VERIFIED})
        bench = wb.Workbench(root, session_factory=lambda _b: FakeSession(no_reply="after"), codex_locator=lambda: "codex")
        handoff_id = self.handoff(bench)
        with self.assertRaises(wb.WorkbenchError) as caught:
            bench.act(handoff_id, "refresh", {})
        self.assertIn("是否已收到", caught.exception.message)

    def test_user_failure_not_overridden_by_late_reply(self):
        """用户已记下失败后，Codex 才返回回复：不改用户记下的状态，只提示到原对话复制。"""
        release = threading.Event()

        class SlowSession(FakeSession):
            def wait_turn(self, turn_id, minutes, on_started=None, on_progress=None):
                release.wait(3)
                return super().wait_turn(turn_id, minutes, on_started, on_progress)

        slow = SlowSession(started=False)
        root = make_dir(self.base, {"draft": CODEX_VERIFIED})
        bench = wb.Workbench(root, session_factory=lambda _b: slow, codex_locator=lambda: "codex")
        handoff_id = self.handoff(bench)
        bench.act(handoff_id, "failed", {"reason": "对话里提示出错"})
        release.set()
        self.assertTrue(wait_for(lambda: slow.closed))
        time.sleep(0.05)
        item = self.get(bench, handoff_id)
        self.assertEqual(item["status"], "失败")
        self.assertEqual(item["results"], [])
        self.assertIn("没有改动", item["note"])

    def test_partial_result_hint_survives_later_notes(self):
        """结果只保存了一部分的提示，不会被之后的重新查询提示冲掉。"""

        class BrokenRead(FakeSession):
            def request(self, method, params, timeout=0):
                raise wb.CodexNoReply("Codex 进程提前退出")

        long = FakeSession(text="字" * (wb.MAX_RESULT_TEXT + 10))
        root = make_dir(self.base, {"draft": CODEX_VERIFIED})
        sessions = [long, BrokenRead()]
        bench = wb.Workbench(root, session_factory=lambda _b: sessions.pop(0), codex_locator=lambda: "codex")
        handoff_id = self.handoff(bench)
        self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["results"]))
        time.sleep(0.05)
        bench.act(handoff_id, "refresh", {})
        note = self.get(bench, handoff_id)["note"]
        self.assertIn("只保存了前", note)
        self.assertIn("没有成功", note)

    def test_records_limits_and_confirmation(self):
        """页面记录有大小上限；覆盖和删除都要用户确认这一次。"""
        bench = self.bench()
        bench.put_record("对象-1", {"value": {"说明": "示例"}})
        with self.assertRaises(wb.WorkbenchError) as caught:
            bench.put_record("对象-1", {"value": {"说明": "被静默覆盖"}})
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(wb.WorkbenchError):
            bench.put_record("对象-1", {"value": {}, "replace": True})
        self.assertEqual(bench.store.read()["records"]["对象-1"], {"说明": "示例"})
        bench.put_record("对象-1", {"value": {"说明": "改过"}, "replace": True, "confirmed": True})
        self.assertEqual(bench.store.read()["records"]["对象-1"], {"说明": "改过"})
        with self.assertRaises(wb.WorkbenchError) as caught:
            bench.put_record("大", {"value": "字" * (wb.MAX_RECORD_BYTES)})
        self.assertEqual(caught.exception.status, 413)
        with self.assertRaises(wb.WorkbenchError):
            bench.put_record("../逃出", {"value": 1})
        with self.assertRaises(wb.WorkbenchError):
            bench.delete_record("对象-1", {})
        bench.delete_record("对象-1", {"confirmed": True})
        self.assertEqual(bench.store.read()["records"], {})

    def test_text_limits(self):
        """交接文字有长度上限。"""
        bench = self.bench()
        with self.assertRaises(wb.WorkbenchError):
            self.handoff(bench, ask="字" * (wb.MAX_TEXT + 1))

    def test_config_validation(self):
        """配置只检查形式：Codex 目标必须有对话编号，手动目标不能写编号，能力取值受限。"""
        self.assertEqual(wb.validate_config({"targets": {"a": {"label": "x"}}}), [])
        self.assertTrue(wb.validate_config({"targets": {"a": {"label": "x", "adapter": "codex"}}}))
        self.assertTrue(wb.validate_config({"targets": {"a": {"label": "x", "thread": THREAD}}}))
        self.assertTrue(wb.validate_config({"targets": {"a": {"label": "x", "capabilities": {"发起工作": "应该可以"}}}}))
        self.assertTrue(wb.validate_config({"targets": {"a": {"label": "x", "adapter": "shell"}}}))
        self.assertTrue(wb.validate_config({"targets": {}}))

    def test_check_dir_flags_external_resources(self):
        """页面引用外部地址时自查不通过。"""
        root = make_dir(self.base, page='<html><head><script src="https://cdn.example.com/a.js"></script></head></html>')
        self.assertTrue(any("外部地址" in p for p in wb.check_dir(root)))
        (root / "page" / "index.html").write_text("<html><head></head><body><script src=\"/_workbench/client.js\"></script></body></html>", encoding="utf-8")
        self.assertEqual(wb.check_dir(root), [])


class GatedSession(FakeSession):
    """可控制何时结束这一轮的假会话，记录交出时的对话与选项。"""

    log: list = []

    def __init__(self, gate=None, new_thread=None, project_seen=None, **kwargs):
        super().__init__(**kwargs)
        self.gate = gate
        self.new_thread = new_thread
        self.project_seen = project_seen
        self.thread_id = None

    def start_turn(self, thread_id, text, **options):
        if options.get("project") and self.project_seen != options["project"]:
            raise wb.ProjectMismatch("对话所属的项目与这项工作绑定的项目不一致，没有发送")
        self.thread_id = thread_id or self.new_thread
        GatedSession.log.append((thread_id, self.thread_id, options))
        self.turn_sent = True
        return f"turn-{len(GatedSession.log)}"

    def wait_turn(self, turn_id, minutes, on_started=None, on_progress=None):
        if self.gate is not None:
            self.gate.wait(3)
        return super().wait_turn(turn_id, minutes, on_started, on_progress)


class LaneTest(unittest.TestCase):
    """同一对话排队、不同对话并行、每件事各开对话、项目绑定与权限。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        GatedSession.log = []

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, bench, handoff_id):
        return wb.find_handoff(bench.store.read(), handoff_id)

    def run_one(self, bench, target="draft", **extra):
        payload = {"target": target, "intent": "new", "ask": "核对", "expect": "结论", **extra}
        return bench.create_handoff(payload, require_direct=True)["id"]

    def test_same_thread_queues_and_other_thread_runs_in_parallel(self):
        other = {**CODEX_VERIFIED, "thread": THREAD[:-1] + "2"}
        root = make_dir(self.base, {"draft": CODEX_VERIFIED, "other": other})
        gate = threading.Event()
        bench = wb.Workbench(root, session_factory=lambda _b: GatedSession(gate=gate), codex_locator=lambda: "codex")
        first = self.run_one(bench)
        second = self.run_one(bench)
        third = self.run_one(bench, target="other")
        self.assertEqual(self.get(bench, second)["status"], "排队中")
        self.assertTrue(wait_for(lambda: self.get(bench, third)["status"] in ("已发送", "已开始")))
        self.assertEqual(len(GatedSession.log), 2)  # 第二条还没交出
        gate.set()
        self.assertTrue(wait_for(lambda: all(self.get(bench, h)["status"] == "已完成" for h in (first, second, third))))
        self.assertEqual(len(GatedSession.log), 3)

    def test_per_item_thread_created_once_and_reused(self):
        target = {"label": "每件事一个对话", "adapter": "codex", "perItem": True, "project": "p1",
                  "capabilities": CODEX_VERIFIED["capabilities"]}
        root = make_dir(self.base, {"item": target})
        new_id = THREAD[:-1] + "9"
        bench = wb.Workbench(root, session_factory=lambda _b: GatedSession(new_thread=new_id, project_seen="p1"),
                             codex_locator=lambda: "codex")
        with self.assertRaises(wb.WorkbenchError):
            self.run_one(bench, target="item")  # 缺事项编号
        first = self.run_one(bench, target="item", ref="A-1")
        self.assertTrue(wait_for(lambda: self.get(bench, first)["status"] == "已完成"))
        second = self.run_one(bench, target="item", ref="A-1", intent="continue", about="A-1 的结论")
        self.assertTrue(wait_for(lambda: self.get(bench, second)["status"] == "已完成"))
        self.assertEqual([entry[0] for entry in GatedSession.log], [None, new_id])
        self.assertEqual(bench.store.read()["threads"], {"item:A-1": new_id})
        self.assertNotIn(new_id, json.dumps(bench.public_state(), ensure_ascii=False))

    def test_project_mismatch_is_not_sent(self):
        root = make_dir(self.base, {"draft": {**CODEX_VERIFIED, "project": "p1"}})
        bench = wb.Workbench(root, session_factory=lambda _b: GatedSession(project_seen="p2"), codex_locator=lambda: "codex")
        handoff_id = self.run_one(bench)
        item = self.get(bench, handoff_id)
        self.assertEqual(item["status"], "失败")
        self.assertIn("项目", item["events"][-1]["evidence"])

    def test_access_policy_and_safety_line(self):
        self.assertEqual(wb.turn_policy("read-only")["sandboxPolicy"], {"type": "readOnly", "networkAccess": False})
        self.assertTrue(wb.turn_policy("read-only-network")["sandboxPolicy"]["networkAccess"])
        self.assertEqual(wb.turn_policy("inherit"), {})
        root = make_dir(self.base, {"draft": CODEX_VERIFIED})
        bench = wb.Workbench(root, session_factory=lambda _b: GatedSession(), codex_locator=lambda: "codex")
        handoff_id = self.run_one(bench)
        self.assertTrue(wait_for(lambda: self.get(bench, handoff_id)["status"] == "已完成"))
        self.assertIn(wb.SAFETY_LINE, self.get(bench, handoff_id)["text"])
        self.assertEqual(GatedSession.log[0][2].get("access", "read-only"), "read-only")  # 没写就按只读交出
        self.assertNotIn("allow_queue", GatedSession.log[0][2])  # 只读目标不排进原对话队列

    def test_queue_ticket_marks_queued_and_finds_turn(self):
        session = wb.CodexSession.__new__(wb.CodexSession)
        pages = [{"data": [{"id": "t9", "status": "completed", "items": [
            {"type": "userMessage", "content": [{"type": "text", "text": "要求\n" + wb.QUEUE_MARK + "m1"}]},
            {"type": "agentMessage", "phase": "final_answer", "text": "排队结果"}]}]}]
        session.request = lambda method, params, timeout=0: pages[0]
        self.assertEqual(session.queued_turn(THREAD, "queue:m1:t0")["id"], "t9")
        self.assertIsNone(session.queued_turn(THREAD, "queue:m2:t9"))

    def test_config_targets(self):
        ok = {"label": "x", "adapter": "codex", "perItem": True, "project": "p", "cwd": "/tmp", "access": "inherit"}
        self.assertEqual(wb.validate_config({"targets": {"a": ok}}), [])
        self.assertTrue(wb.validate_config({"targets": {"a": {**ok, "thread": THREAD}}}))
        self.assertTrue(wb.validate_config({"targets": {"a": {**ok, "access": "full"}}}))
        self.assertTrue(wb.validate_config({"targets": {"a": {**ok, "cwd": "relative"}}}))
        self.assertTrue(wb.validate_config({"targets": {"a": {"label": "x", "perItem": True}}}))

    def test_original_queue_never_resumes_or_opens_thread(self):
        """复用原对话直接入队，不通过恢复会话抢占输入；非零退出不盲目重发。"""
        session = wb.CodexSession.__new__(wb.CodexSession)
        session.initialized = True
        session.codex_bin = "codex"
        calls = []
        def request(method, params):
            calls.append(method)
            if method == "thread/read":
                return {"thread": {"id": THREAD, "projectId": "p1"}}
            return {"data": []}
        session.request = request
        with mock.patch.object(wb.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            ticket = session.start_turn(THREAD, "核对", project="p1", access="inherit", allow_queue=True)
            self.assertTrue(ticket.startswith("queue:"))
            self.assertEqual(calls, ["thread/read", "thread/turns/list"])
            self.assertIn("--resume", run.call_args.args[0])
        with mock.patch.object(wb.subprocess, "run", return_value=mock.Mock(returncode=1)):
            with self.assertRaises(wb.CodexNoReply):
                session.queue(THREAD, "核对", "p1")
            self.assertTrue(session.turn_sent)

    def test_new_thread_project_checked_before_turn(self):
        """创建响应的项目不符时不发送；匹配时给本轮实际施加只读联网权限。"""
        session = wb.CodexSession.__new__(wb.CodexSession)
        session.initialized = True
        calls = []
        def request(method, params):
            calls.append((method, params))
            return {"thread": {"id": THREAD, "projectId": "p1"}, "turn": {"id": "t1"}}
        session.request = request
        with self.assertRaises(wb.ProjectMismatch):
            session.start_turn(None, "核对", project="p2")
        self.assertEqual([c[0] for c in calls], ["thread/start"])
        calls.clear()
        session.start_turn(None, "核对", project="p1", access="read-only-network")
        self.assertEqual(calls[0][1]["projectId"], "p1")
        self.assertEqual(calls[1][1]["sandboxPolicy"], {"type": "readOnly", "networkAccess": True})


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.root = make_dir(base)
        (cls.root / "page" / ".hidden.html").write_text("隐藏", encoding="utf-8")
        (cls.root / "page" / "notes.txt").write_text("不该提供", encoding="utf-8")
        (base / "secret.html").write_text("页面目录外的文件", encoding="utf-8")
        try:
            os.symlink(base / "secret.html", cls.root / "page" / "link.html")
            # 页面目录里、但扩展名和目标文件不同的软链接：按解析后的文件类型判断
            os.symlink(cls.root / "page" / "notes.txt", cls.root / "page" / "renamed.html")
        except OSError:
            pass
        try:
            cls.server = wb.WorkbenchServer(wb.Workbench(cls.root), 0)
        except PermissionError as exc:  # 有些沙箱不允许监听端口
            cls.tmp.cleanup()
            raise unittest.SkipTest(f"当前环境不允许监听本机端口：{exc}")
        cls.port = cls.server.port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = f"127.0.0.1:{cls.port}"
        cls.origin = "http://" + cls.host

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None, raw_length=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        base = {"Host": self.host}
        if data is not None:
            base["Content-Type"] = "application/json"
            base["Content-Length"] = str(raw_length if raw_length is not None else len(data))
        base.update(headers or {})
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for key, value in base.items():
            if value is not None:
                conn.putheader(key, value)
        conn.endheaders()
        if data is not None and raw_length is None:
            conn.send(data)
        response = conn.getresponse()
        content = response.read()
        result = (response.status, content, dict(response.getheaders()))
        conn.close()
        return result

    def good(self, write=False):
        headers = {"X-Workbench-Token": self.server.token}
        if write:
            headers["Origin"] = self.origin
        return headers

    def test_page_gets_token_and_security_headers(self):
        """首页由运行支持提供，令牌只注入到页面里，并带同源内容安全策略。"""
        status, content, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(f'<meta name="workbench-token" content="{self.server.token}">'.encode(), content)
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_private_files_are_not_served(self):
        """配置、状态、隐藏文件、非页面类型、目录外文件和路径穿越都不提供。"""
        for path in ("/workbench.json", "/data/state.json", "/../workbench.json", "/%2e%2e/workbench.json",
                     "/.hidden.html", "/notes.txt", "/link.html", "/renamed.html", "/page/index.html", "/..%2fsecret.html"):
            with self.subTest(path=path):
                status, content, _ = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertNotIn("页面目录外".encode(), content)
                self.assertNotIn("不该提供".encode(), content)

    def test_host_check_blocks_rebinding(self):
        """Host 不是本机地址时，页面和接口都拒绝。"""
        for path in ("/", "/api/state"):
            status, _, _ = self.request("GET", path, headers={"Host": "evil.example", **self.good()})
            self.assertEqual(status, 403)

    def test_token_required(self):
        """接口必须带正确令牌。"""
        self.assertEqual(self.request("GET", "/api/state")[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"X-Workbench-Token": "wrong"})[0], 403)
        # 令牌里有非 ASCII 字符也只是 403，不会让连接直接断开
        self.assertEqual(self.request("GET", "/api/state", headers={"X-Workbench-Token": "令牌".encode("utf-8")})[0], 403)
        status, content, _ = self.request("GET", "/api/state", headers=self.good())
        self.assertEqual(status, 200)
        data = json.loads(content)
        self.assertNotIn("thread", json.dumps(data["targets"]))

    def test_origin_required_for_writes(self):
        """写操作必须来自同源页面；跨站请求被拒绝。"""
        body = {"target": "draft", "intent": "new", "ask": "a", "expect": "b"}
        self.assertEqual(self.request("POST", "/api/handoffs", body, {"X-Workbench-Token": self.server.token})[0], 403)
        self.assertEqual(self.request("POST", "/api/handoffs", body, {**self.good(), "Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/handoffs", body, {**self.good(True), "Sec-Fetch-Site": "cross-site"})[0], 403)
        status, content, _ = self.request("POST", "/api/handoffs", body, self.good(True))
        self.assertEqual(status, 200, content)

    def test_request_size_limit(self):
        """超过上限的请求体直接拒绝，不读取内容。"""
        status, _, _ = self.request("POST", "/api/handoffs", {"x": 1}, self.good(True), raw_length=wb.MAX_BODY + 1)
        self.assertEqual(status, 413)

    def test_non_json_rejected(self):
        """只接受 JSON 请求。"""
        status, _, _ = self.request("POST", "/api/handoffs", {"x": 1}, {**self.good(True), "Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_client_js_is_served(self):
        """接口封装由运行支持提供。"""
        status, content, _ = self.request("GET", "/_workbench/client.js")
        self.assertEqual(status, 200)
        self.assertIn(b"window.Workbench", content)


class NoBuiltInLayoutTest(unittest.TestCase):
    def test_skill_scripts_have_no_page_assets(self):
        """技能只带运行脚本和接口封装，不带页面、样式或图片，避免形成默认工作台。"""
        names = sorted(p.name for p in SCRIPTS.iterdir() if p.is_file())
        self.assertEqual(names, ["client.js", "workbench.py"])
        skill = SCRIPTS.parent
        for path in skill.rglob("*"):
            self.assertNotIn(path.suffix, {".html", ".css", ".png", ".svg"}, path)

    def test_client_has_no_dom_layout(self):
        """接口封装不创建页面元素（复制文字时临时用的输入框除外）。"""
        text = (SCRIPTS / "client.js").read_text(encoding="utf-8")
        created = [part.split(")")[0] for part in text.split("createElement(")[1:]]
        self.assertEqual(created, ['"textarea"'])
        self.assertNotIn("innerHTML", text)


if __name__ == "__main__":
    unittest.main()
