"""AI agents on the notch: the hub, Claude Code's hooks, Codex, MCP, the pass-through, connecting the tools, and
how the notch shows agents and their questions."""
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from rotary import agents
from rotary.actions import DEFAULT_CONFIG
from rotary.ui.notch import NotchWindow

app = QApplication.instance() or QApplication([])


def _answer_soon(hub: agents.Hub, pick, typed: bool = False, delay: float = 0.05):
    """Answer the first question that shows up, from another thread (like a click on the notch)."""
    def run():
        for _ in range(200):
            qs = hub.snapshot()[1]
            if qs:
                hub.answer(qs[0].id, pick(qs[0]) if callable(pick) else pick, typed)
                return
            time.sleep(delay)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# ------------------------------------------------------------------ words
def test_claude_code_tools_in_words():
    assert agents.describe_tool("Edit", {"file_path": r"C:\code\rotary\ui\notch.py"}) == "Editing notch.py"
    assert agents.describe_tool("Read", {"file_path": "/home/me/app.py"}) == "Reading app.py"
    assert agents.describe_tool("Bash", {"command": "pytest -q", "description": "Run the tests"}) == "Run the tests"
    assert agents.describe_tool("Bash", {"command": "ls"}) == "Running ls"
    assert agents.describe_tool("WebFetch", {"url": "https://docs.python.org/3/"}) == "Reading docs.python.org"
    assert agents.describe_tool("mcp__github__create_pull_request", {}) == "Using github"
    assert agents.describe_tool("Frobnicate", {}) == "Using Frobnicate"
    todos = {"todos": [{"content": "Plan", "status": "completed", "activeForm": "Planning"},
                       {"content": "Build the UI", "status": "in_progress", "activeForm": "Building the UI"}]}
    assert agents.current_todo(todos) == "Building the UI"
    assert agents.current_todo({"todos": []}) == ""


def test_defaults_and_token():
    assert DEFAULT_CONFIG["agents"]["enabled"] and DEFAULT_CONFIG["agents"]["port"] == agents.DEFAULTS["port"]
    cfg = {}
    tok = agents.ensure_token(cfg)
    assert len(tok) >= 16 and agents.ensure_token(cfg) == tok            # made once, then kept
    assert agents.base_url(cfg) == f"http://127.0.0.1:47823/{tok}"


# ------------------------------------------------------------------ the hub
def test_hub_tracks_sessions_and_forgets_them():
    hub = agents.Hub()
    hub.update("a", "Claude Code", state="working", text="Reading x.py", where="proj")
    s = hub.get("a")
    assert s.label() == "Claude Code · proj" and s.now() == "Reading x.py" and s.busy()
    hub.update("a", "", state="done", text="")
    assert hub.get("a").now() == "Finished" and hub.get("a").busy()
    hub._sessions["a"].updated -= agents.DONE_SHOW_SECONDS + 1
    assert not hub.get("a").busy()                                   # finished a while ago: off the resting notch
    hub._sessions["a"].updated -= agents.LIST_SECONDS
    assert hub.snapshot()[0] == []                                   # and off the hover card later
    hub.update("b", "X", state="nonsense")
    assert hub.get("b").state == "working"


def test_hub_questions_wait_for_an_answer_or_time_out():
    hub = agents.Hub()
    changes = []
    hub.on_change = lambda: changes.append(1)
    q = agents.Question("q1", "s", "Codex", "Ship it?", options=["Yes", "No"])
    _answer_soon(hub, "Yes")
    got = hub.ask(q, 5)
    assert got is not None and got.answer == "Yes" and not got.typed
    assert hub.snapshot()[1] == [] and changes
    q2 = agents.Question("q2", "s", "Codex", "Ship it?")
    _answer_soon(hub, None)                                          # put away on the notch
    assert hub.ask(q2, 5) is None
    q3 = agents.Question("q3", "s", "Codex", "Anyone?")
    t0 = time.monotonic()
    assert hub.ask(q3, 1) is None and time.monotonic() - t0 < 3       # nobody answered in time


# ------------------------------------------------------------------ Claude Code
def test_claude_code_steps_show_on_the_hub():
    hub = agents.Hub()
    base = {"session_id": "s1", "cwd": "/home/me/Gesture_Rotary"}
    assert agents.claude_hook(hub, {**base, "hook_event_name": "SessionStart"}, 60) is None
    assert hub.get("claude:s1").state == "idle"
    agents.claude_hook(hub, {**base, "hook_event_name": "UserPromptSubmit", "prompt": "Make the notch talk to AI"}, 60)
    s = hub.get("claude:s1")
    assert (s.state, s.step, s.where) == ("working", "Make the notch talk to AI", "Gesture_Rotary")
    agents.claude_hook(hub, {**base, "hook_event_name": "PreToolUse", "tool_name": "TodoWrite", "tool_input": {
        "todos": [{"content": "UI", "status": "in_progress", "activeForm": "Designing the UI"}]}}, 60)
    assert hub.get("claude:s1").step == "Designing the UI"
    agents.claude_hook(hub, {**base, "hook_event_name": "PreToolUse", "tool_name": "Edit",
                             "tool_input": {"file_path": "/x/notch.py"}}, 60)
    s = hub.get("claude:s1")
    assert (s.text, s.step) == ("Editing notch.py", "Designing the UI")    # a tool doesn't change the step
    agents.claude_hook(hub, {**base, "hook_event_name": "Notification", "notification_type": "idle_prompt",
                             "message": "Claude is waiting for your input"}, 60)
    assert hub.get("claude:s1").state == "waiting"
    agents.claude_hook(hub, {**base, "hook_event_name": "Stop"}, 60)
    assert hub.get("claude:s1").state == "done"
    agents.claude_hook(hub, {**base, "hook_event_name": "SessionEnd"}, 60)
    assert hub.get("claude:s1") is None


def test_claude_code_permission_answered_on_the_notch():
    hub = agents.Hub()
    data = {"session_id": "s1", "cwd": "/p", "hook_event_name": "PermissionRequest", "tool_name": "Bash",
            "tool_input": {"command": "rm -rf build", "description": "Delete the build folder"},
            "permission_suggestions": [{"type": "addRules", "rules": [{"toolName": "Bash"}], "behavior": "allow",
                                        "destination": "localSettings"}]}
    seen = []
    _answer_soon(hub, lambda q: seen.append(q) or "Always allow")
    out = agents.claude_hook(hub, data, 5)
    q = seen[0]
    assert q.text == "Delete the build folder?" and q.detail == "rm -rf build"
    assert q.options == ["Allow", "Always allow", "Deny"] and q.allow_text
    d = out["hookSpecificOutput"]
    assert d["hookEventName"] == "PermissionRequest" and d["decision"]["behavior"] == "allow"
    assert d["decision"]["updatedPermissions"] == data["permission_suggestions"]
    _answer_soon(hub, "Deny")
    assert agents.claude_hook(hub, data, 5)["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    _answer_soon(hub, "Use the clean script instead", typed=True)
    d = agents.claude_hook(hub, data, 5)["hookSpecificOutput"]["decision"]
    assert d["behavior"] == "deny" and "Use the clean script instead" in d["message"]   # Claude hears the reply
    assert agents.claude_hook(hub, data, 1) == {}               # no answer: Claude Code asks in its own window
    assert agents.claude_hook(hub, data, 0) is None             # waiting switched off


def test_claude_code_multiple_choice_question():
    hub = agents.Hub()
    data = {"session_id": "s1", "hook_event_name": "PermissionRequest", "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [
                {"question": "Which layout?", "header": "Layout", "multiSelect": False,
                 "options": [{"label": "Cards", "description": "One card per agent"}, {"label": "List"}]},
                {"question": "Colour?", "header": "Colour", "options": [{"label": "Purple"}, {"label": "Blue"}]}]}}
    picks = iter(["List", "Purple"])
    for _ in range(2):
        _answer_soon(hub, lambda q: next(picks))
    out = agents.claude_hook(hub, data, 5)
    d = out["hookSpecificOutput"]["decision"]
    assert d["behavior"] == "allow"
    assert d["updatedInput"]["answers"] == {"Which layout?": "List", "Colour?": "Purple"}
    assert d["updatedInput"]["questions"] == data["tool_input"]["questions"]


def test_answering_in_the_terminal_takes_the_question_off_the_notch():
    hub = agents.Hub()
    data = {"session_id": "s1", "hook_event_name": "PermissionRequest", "tool_name": "Write",
            "tool_input": {"file_path": "/p/a.txt", "content": "hi"}}
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("out", agents.claude_hook(hub, data, 10)), daemon=True)
    t.start()
    for _ in range(100):
        if hub.snapshot()[1]:
            break
        time.sleep(0.02)
    assert hub.snapshot()[1][0].text == "Write a.txt?"
    agents.claude_hook(hub, {**data, "hook_event_name": "PostToolUse"}, 10)   # it ran: answered in the terminal
    t.join(3)
    assert result["out"] == {} and hub.snapshot()[1] == []


def test_codex_turn_complete():
    hub = agents.Hub()
    agents.codex_notify(hub, {"type": "agent-turn-complete", "thread-id": "t1", "cwd": "/w/app",
                              "last-assistant-message": "Added the tests."})
    s = hub.get("codex:t1")
    assert (s.agent, s.state, s.text, s.where) == ("Codex", "done", "Added the tests.", "app")


# ------------------------------------------------------------------ the server
@pytest.fixture
def bridge():
    hub = agents.Hub()
    cfg = {"agents": {"ask_seconds": 5, "proxies": {}}}
    b = agents.Bridge(hub, lambda: cfg)
    assert b.start(0, "T" * 24)
    b.cfg_dict = cfg
    yield b
    b.stop()


def _req(b, method, path, body=None, headers=None, host="127.0.0.1"):
    c = http.client.HTTPConnection("127.0.0.1", b.port, timeout=10)
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    h = {"Host": f"{host}:{b.port}", "Content-Type": "application/json", **(headers or {})}
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw), r


def test_server_needs_the_token_and_our_own_name(bridge):
    tok = bridge.token
    assert _req(bridge, "POST", "/status", {"agent": "x", "text": "hi"})[0] == 404
    assert _req(bridge, "POST", "/WRONGTOKENWRONGTOKEN/status", {"agent": "x", "text": "hi"})[0] == 404
    assert _req(bridge, "POST", f"/{tok}/status", {"text": "hi"}, host="evil.example")[0] == 403
    code, out, _ = _req(bridge, "POST", f"/{tok}/status", {"agent": "Ollama script", "text": "Summarising"})
    assert code == 200 and out["ok"]
    code, out, _ = _req(bridge, "GET", f"/{tok}/status")
    assert out["agents"][0]["text"] == "Summarising"
    _req(bridge, "POST", f"/{tok}/status", {"agent": "Ollama script", "state": "end"})
    assert bridge.hub.snapshot()[0] == []


def test_server_ask_waits_for_the_notch(bridge):
    _answer_soon(bridge.hub, "Blue")
    code, out, _ = _req(bridge, "POST", f"/{bridge.token}/ask",
                        {"agent": "Groq bot", "question": "Which colour?", "options": ["Purple", "Blue"]})
    assert code == 200 and out == {"answered": True, "answer": "Blue", "typed": False}


def test_server_claude_hooks_print_nothing_but_permission_answers(bridge):
    code, out, r = _req(bridge, "POST", f"/{bridge.token}/claude-code",
                        {"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "hello"})
    assert code == 200 and out == b""                 # anything printed would be added to the chat
    _answer_soon(bridge.hub, "Allow")
    code, out, _ = _req(bridge, "POST", f"/{bridge.token}/claude-code",
                        {"hook_event_name": "PermissionRequest", "session_id": "s", "tool_name": "Bash",
                         "tool_input": {"command": "make"}})
    assert out["hookSpecificOutput"]["decision"] == {"behavior": "allow"}


def test_server_speaks_mcp(bridge):
    tok = bridge.token
    code, out, r = _req(bridge, "POST", f"/{tok}/mcp", {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-code"}}})
    sid = r.getheader("Mcp-Session-Id")
    assert code == 200 and sid and out["result"]["protocolVersion"] == "2025-06-18"
    h = {"Mcp-Session-Id": sid}
    assert _req(bridge, "POST", f"/{tok}/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, h)[0] == 202
    out = _req(bridge, "POST", f"/{tok}/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, h)[1]
    assert [t["name"] for t in out["result"]["tools"]] == ["notch_status", "ask_user"]
    out = _req(bridge, "POST", f"/{tok}/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "notch_status", "arguments": {"text": "Testing the UX", "step": "Polish"}}}, h)[1]
    assert not out["result"].get("isError")
    s = bridge.hub.get(f"mcp:{sid}")
    assert (s.agent, s.text, s.step) == ("Claude Code", "Testing the UX", "Polish")
    _answer_soon(bridge.hub, "Go ahead", typed=True)
    out = _req(bridge, "POST", f"/{tok}/mcp", {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
        "name": "ask_user", "arguments": {"question": "Merge it?"}}}, h)[1]
    assert out["result"]["content"][0]["text"] == "The user answered: Go ahead"
    out = _req(bridge, "POST", f"/{tok}/mcp", {"jsonrpc": "2.0", "id": 5, "method": "nope"}, h)[1]
    assert out["error"]["code"] == -32601
    assert _req(bridge, "GET", f"/{tok}/mcp")[0] == 405


class _Upstream(BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        _Upstream.seen = {"path": self.path, "auth": self.headers.get("Authorization"),
                          "body": json.loads(self.rfile.read(n))}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for word in ("Hel", "lo"):
            self.wfile.write(f"data: {word}\n\n".encode())
            self.wfile.flush()


def test_pass_through_streams_and_shows_the_model(bridge):
    up = HTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    try:
        bridge.cfg_dict["agents"]["proxies"] = {"mybox": f"http://127.0.0.1:{up.server_address[1]}"}
        states = []
        bridge.hub.on_change = lambda: states.extend(s.state for s in bridge.hub.snapshot()[0])
        code, out, r = _req(bridge, "POST", f"/{bridge.token}/proxy/mybox/v1/chat/completions?x=1",
                            {"model": "llama3.2", "stream": True}, {"Authorization": "Bearer sk-1"})
        assert code == 200 and out == b"data: Hel\n\ndata: lo\n\n"
        assert _Upstream.seen == {"path": "/v1/chat/completions?x=1", "auth": "Bearer sk-1",
                                  "body": {"model": "llama3.2", "stream": True}}
        s = bridge.hub.get("proxy:mybox:llama3.2")
        assert "working" in states and (s.agent, s.where, s.state) == ("mybox", "llama3.2", "done")
        assert _req(bridge, "POST", f"/{bridge.token}/proxy/unknown/v1/x", {})[0] == 404
    finally:
        up.shutdown()
        up.server_close()


def test_port_in_use_is_reported():
    hub = agents.Hub()
    a = agents.Bridge(hub, dict)
    assert a.start(0, "T" * 20)
    b = agents.Bridge(hub, dict)
    try:
        assert not b.start(a.port, "T" * 20) and str(a.port) in b.error and not b.running
    finally:
        a.stop()


# ------------------------------------------------------------------ connecting the tools
def test_connect_claude_code_keeps_other_hooks(tmp_path):
    path = tmp_path / "settings.json"
    mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
    path.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": [mine]}}))
    url = "http://127.0.0.1:47823/TOKEN"
    agents.connect_claude(url, 120, path)
    data = json.loads(path.read_text())
    assert data["model"] == "opus" and data["hooks"]["PreToolUse"][0] == mine
    assert set(agents.CLAUDE_EVENTS) <= set(data["hooks"])
    perm = data["hooks"]["PermissionRequest"][0]["hooks"][0]
    assert perm["timeout"] >= 120 and "-m 130" in perm["command"] and url + "/claude-code" in perm["command"]
    assert agents.claude_connected(url, path) and not agents.claude_connected("http://127.0.0.1:1/X", path)
    agents.connect_claude(url + "2", 60, path)                         # again, say with a new token: replaced
    assert len(json.loads(path.read_text())["hooks"]["PreToolUse"]) == 2
    agents.disconnect_claude(path)
    assert json.loads(path.read_text()) == {"model": "opus", "hooks": {"PreToolUse": [mine]}}


def test_connect_codex(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-6"\n\n[profiles.fast]\nmodel = "mini"\n')
    url = "http://127.0.0.1:47823/TOKEN"
    assert agents.connect_codex(url, path) == ""
    text = path.read_text()
    assert text.startswith("notify = [")                       # a top-level key: before any [table]
    assert f'url = "{url}/mcp"' in text and agents.codex_connected(url, path)
    assert text.count("[mcp_servers.rotaryui]") == 1
    agents.connect_codex(url, path)
    assert path.read_text().count("notify =") == 1             # connecting again doesn't add it twice
    agents.disconnect_codex(path)
    assert path.read_text() == 'model = "gpt-6"\n\n[profiles.fast]\nmodel = "mini"\n'
    path.write_text('notify = ["my-notifier"]\n')
    assert "another notify" in agents.connect_codex(url, path)
    assert path.read_text().count("notify =") == 1 and "[mcp_servers.rotaryui]" in path.read_text()


# ------------------------------------------------------------------ the notch
def _settle(n: NotchWindow):
    for _ in range(90):
        for sp in n._springs:
            sp.step(1 / 60)


def _paint(n: NotchWindow) -> None:
    img = QImage(n.width(), n.height(), QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    n.paint(p)
    p.end()


class _Press:
    def __init__(self, pt, button=Qt.MouseButton.LeftButton):
        self.pt, self.b = pt, button

    def button(self):
        return self.b

    def position(self):
        return self.pt

    def globalPosition(self):
        return self.pt


def test_notch_shows_an_agent_at_work_and_flashes_new_steps():
    n = NotchWindow()
    _settle(n)
    rest = n.body_rect().width()
    hub = agents.Hub()
    hub.update("c", "Claude Code", state="working", text="Reading app.py", step="Designing the UI", where="proj")
    n.set_agents(*hub.snapshot())
    assert n.activity() == "agent" and n.flash_text() == "Claude Code · Designing the UI"
    _settle(n)
    _paint(n)
    n._flash_until = 0                                       # the moment's over
    n._retarget()
    _settle(n)
    assert n.flash_text() == "" and n.body_rect().width() > rest      # the resting notch spreads out for it
    _paint(n)
    hub.update("c", "", text="Editing notch.py")                       # just a tool: no flash
    n.set_agents(*hub.snapshot())
    assert n.flash_text() == ""
    hub.update("c", "", step="Testing the UX")                         # a new step: flash
    n.set_agents(*hub.snapshot())
    assert n.flash_text() == "Claude Code · Testing the UX"
    n.hovered = True
    n._retarget()
    _settle(n)
    assert n._card_kind() == "agents" and n.body_rect().height() > 60
    _paint(n)
    hub.update("c", "", state="done", text="")
    n.hovered = False
    n.set_agents(*hub.snapshot())
    assert n.flash_text() == "Claude Code · Finished" and n._agent_side() == "Done"
    n.agent_flash = False
    hub.update("c", "", state="working", step="Again")
    n.set_agents(*hub.snapshot())
    _settle(n)
    _paint(n)


def test_notch_question_answered_by_hovering():
    n = NotchWindow()
    hub = agents.Hub()
    hub.update("c", "Claude Code", state="working", where="proj")
    q = agents.Question("q1", "c", "Claude Code", "Run the tests?", "uv run pytest -q", ["Allow", "Deny"], True,
                        "proj")
    n.set_agents(hub.snapshot()[0], [q])
    _settle(n)
    assert n.question_headline() == "Claude Code · Run the tests?" and n._card_kind() == ""
    _paint(n)
    n.hovered = True
    n._retarget()
    _settle(n)
    assert n._card_kind() == "question" and n.body_rect().width() > 400
    _paint(n)
    assert {"q_opt_0", "q_opt_1", "q_reply", "q_close"} <= set(n._buttons)
    got = []
    n.agent_answer.connect(lambda qid, ans, typed: got.append((qid, ans, typed)))
    opened = []
    n.clicked.connect(lambda: opened.append(1))
    n.mousePressEvent(_Press(QPointF(n.body_rect().center().x(), n.body_rect().bottom() - 4)))
    n.mouseReleaseEvent(_Press(QPointF(n.body_rect().center().x(), n.body_rect().bottom() - 4)))
    assert opened == [] and got == []                 # a click on the card isn't an answer, nor the rotary
    n.mousePressEvent(_Press(n._buttons["q_opt_0"].center()))
    assert got == [("q1", "Allow", False)] and n.question() is None


def test_notch_reply_box_sends_a_typed_answer():
    n = NotchWindow()
    q = agents.Question("q9", "c", "Codex", "What should the button say?", options=[], allow_text=True)
    n.set_agents([], [q])
    got = []
    n.agent_answer.connect(lambda qid, ans, typed: got.append((qid, ans, typed)))
    n.open_reply()
    box = n.reply_box
    assert box is not None and box.isVisible() and "What should the button say?" in box.title.text()
    box.edit.setText("Send it")
    box._send()
    assert got == [("q9", "Send it", True)] and not box.isVisible()
    n.set_agents([], [agents.Question("q10", "c", "Codex", "Another?")])
    n.open_reply()
    n.set_agents([], [])                               # answered somewhere else: the box goes
    assert not n.reply_box.isVisible()
    n.reply_box.close()
