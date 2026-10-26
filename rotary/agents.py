"""AI agents on the notch: what Claude Code, Codex, a local model or any other AI is doing right now, and the
questions it has for you, answered from the notch.

RotaryUI runs a small web server on this computer only (127.0.0.1). Every address on it starts with a secret
token, so web pages and other people on the network can't use it. AI tools reach it four ways:

  * Claude Code (in a terminal, an IDE or the desktop app): hooks in ~/.claude/settings.json send each step
    ("Editing notch.py", the item of its to-do list it's on, finished, waiting for you), and its permission
    prompts and multiple-choice questions come to the notch, where a click answers them. No answer on the notch
    within a while and the question goes back to Claude Code's own prompt.
  * Codex CLI: its notify program tells the notch when a turn is done.
  * MCP: any agent that speaks MCP (Claude Code, Codex, Cursor, Gemini CLI, LM Studio…) gets two tools,
    notch_status and ask_user, so it can say what it's doing and ask you something on the notch.
  * A pass-through for model APIs: point an app's base URL at /proxy/ollama (or groq, openai, openrouter,
    anthropic, gemini, lmstudio, or one of your own) and the notch shows while that model is answering. The
    request goes on to the real service unchanged, API key and all.

Anything else can POST JSON to /status and /ask (see README.md: "AI agents on the notch").
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import re
import secrets
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

DEFAULTS = {"enabled": True, "port": 47823, "token": "", "ask_seconds": 120, "flash": True, "proxies": {}}
STATES = ("working", "waiting", "done", "error", "idle")
STALE_SECONDS = 15 * 60          # an agent that's been quiet this long while "working" has gone away
DONE_SHOW_SECONDS = 60           # "finished" stays on the resting notch this long
LIST_SECONDS = 30 * 60           # the hover card lists agents heard from this recently
MAX_TEXT = 140
MAX_DETAIL = 1200
MAX_BODY = 8_000_000             # bytes: the biggest request the bridge reads (a long prompt through the proxy)

# Model APIs the pass-through knows: name → (what the notch calls it, where requests go on to).
UPSTREAMS = {
    "ollama": ("Ollama", "http://localhost:11434"),
    "lmstudio": ("LM Studio", "http://localhost:1234"),
    "openai": ("ChatGPT", "https://api.openai.com"),
    "groq": ("Groq", "https://api.groq.com/openai"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api"),
    "anthropic": ("Claude", "https://api.anthropic.com"),
    "gemini": ("Gemini", "https://generativelanguage.googleapis.com"),
}
# What MCP clients call themselves → what the notch calls them.
CLIENT_NAMES = {"claude-code": "Claude Code", "claude-ai": "Claude", "claude": "Claude", "codex": "Codex",
                "codex-mcp-client": "Codex", "cursor": "Cursor", "cursor-vscode": "Cursor", "gemini-cli": "Gemini CLI",
                "windsurf": "Windsurf", "lm-studio": "LM Studio", "goose": "Goose", "cline": "Cline",
                "visual studio code": "VS Code", "zed": "Zed", "opencode": "opencode"}
MCP_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def settings(cfg: dict | None) -> dict:
    return {**DEFAULTS, **((cfg or {}).get("agents") or {})}


def ensure_token(cfg: dict) -> str:
    """The secret every address starts with, made the first time it's needed."""
    a = cfg.setdefault("agents", {})
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", str(a.get("token") or "")):
        a["token"] = secrets.token_urlsafe(18)
    return a["token"]


def base_url(cfg: dict) -> str:
    s = settings(cfg)
    return f"http://127.0.0.1:{int(s['port'])}/{s['token']}"


def _short(text, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _name(path) -> str:
    """The last part of a path or URL: "notch.py" for "C:\\code\\rotary\\ui\\notch.py"."""
    return re.split(r"[\\/]", str(path or "").rstrip("\\/"))[-1]


# --------------------------------------------------------------------------- what the notch shows
@dataclass
class Session:
    """One agent at work: a Claude Code session, a Codex thread, a model answering through the pass-through…"""
    key: str
    agent: str              # "Claude Code"
    where: str = ""         # the project folder, or the model
    state: str = "working"  # working | waiting | done | error | idle
    text: str = ""          # what it's doing right now: "Editing notch.py"
    step: str = ""          # the bigger step it's on (its to-do item, the request): changes flash on the notch
    started: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.monotonic)

    def label(self) -> str:
        return f"{self.agent} · {self.where}" if self.where else self.agent

    def now(self) -> str:
        """One line for the notch: what it's doing, or how it stands."""
        if self.state == "done":
            return self.text or "Finished"
        if self.state == "waiting":
            return self.text or "Waiting for you"
        if self.state == "error":
            return self.text or "Something went wrong"
        if self.state == "idle":
            return self.text or "Ready"
        return self.text or self.step or "Working"

    def busy(self, now: float | None = None) -> bool:
        """Worth showing on the resting notch."""
        age = (time.monotonic() if now is None else now) - self.updated
        if self.state in ("working", "waiting"):
            return age < STALE_SECONDS
        return self.state in ("done", "error") and age < DONE_SHOW_SECONDS


@dataclass
class Question:
    """Something an agent asks you. The notch shows it until you answer, it's answered elsewhere, or it times out."""
    id: str
    session: str
    agent: str
    text: str
    detail: str = ""
    options: list[str] = field(default_factory=list)
    allow_text: bool = True          # a typed reply is fine too
    where: str = ""
    created: float = field(default_factory=time.monotonic)
    match: str = ""                  # Claude Code: the tool call it's about, so answering in the terminal clears it
    answer: str | None = None
    typed: bool = False
    _done: threading.Event = field(default_factory=threading.Event, repr=False)


class Hub:
    """Every agent the bridge has heard from, and the questions waiting on you. Thread-safe: the web server
    writes from its threads; `on_change` (called on those threads) tells the app to look again."""

    def __init__(self, on_change: Callable[[], None] | None = None):
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._questions: list[Question] = []
        self.on_change = on_change

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                log.debug("agent change callback failed", exc_info=True)

    def update(self, key: str, agent: str, *, state: str | None = None, text: str | None = None,
               step: str | None = None, where: str | None = None) -> Session:
        with self._lock:
            s = self._sessions.get(key)
            if s is None:
                s = self._sessions[key] = Session(key, _short(agent, 40) or "AI")
            elif agent:
                s.agent = _short(agent, 40)
            if state in STATES:
                if state == "working" and s.state != "working":
                    s.started = time.time()
                s.state = state
            if text is not None:
                s.text = _short(text, MAX_TEXT)
            if step is not None:
                s.step = _short(step, MAX_TEXT)
            if where:
                s.where = _short(where, 60)
            s.updated = time.monotonic()
            out = replace(s)
        self._changed()
        return out

    def end(self, key: str) -> None:
        with self._lock:
            gone = self._sessions.pop(key, None) is not None
            for q in self._questions:
                if q.session == key:
                    q._done.set()
        if gone:
            self._changed()

    def get(self, key: str) -> Session | None:
        with self._lock:
            s = self._sessions.get(key)
            return replace(s) if s else None

    def snapshot(self) -> tuple[list[Session], list[Question]]:
        """Copies for the notch: agents heard from lately (most recent first), and the questions (oldest first)."""
        now = time.monotonic()
        with self._lock:
            for k in [k for k, s in self._sessions.items() if now - s.updated > LIST_SECONDS]:
                del self._sessions[k]
            sessions = sorted((replace(s) for s in self._sessions.values()), key=lambda s: -s.updated)
            questions = [replace(q) for q in self._questions if q.answer is None and not q._done.is_set()]
        return sessions, questions

    # -- questions
    def ask(self, q: Question, timeout: float) -> Question | None:
        """Put `q` on the notch and wait for the answer (None: no answer in time, put away, or answered elsewhere)."""
        with self._lock:
            self._questions.append(q)
            s = self._sessions.get(q.session)
            if s is not None:
                s.state, s.updated = "waiting", time.monotonic()
        self._changed()
        try:
            q._done.wait(max(1.0, timeout))
        finally:
            with self._lock:
                if q in self._questions:
                    self._questions.remove(q)
            self._changed()
        return q if q.answer is not None else None

    def answer(self, qid: str, text: str | None, typed: bool = False) -> bool:
        """The notch answered (text None: put away, so the agent asks the usual way)."""
        with self._lock:
            q = next((q for q in self._questions if q.id == qid), None)
            if q is None:
                return False
            q.answer, q.typed = (None if text is None else str(text)), typed
            s = self._sessions.get(q.session)
            if s is not None and text is not None:
                s.state, s.text, s.updated = "working", "", time.monotonic()
            q._done.set()
        self._changed()
        return True

    def cancel(self, session: str, match: str | None = None) -> None:
        """Answered somewhere else (the terminal, say): take those questions off the notch."""
        with self._lock:
            hit = [q for q in self._questions if q.session == session and (match is None or q.match == match)]
            for q in hit:
                q._done.set()
                self._questions.remove(q)
        if hit:
            self._changed()


# --------------------------------------------------------------------------- Claude Code
CLAUDE_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
                 "Notification", "Stop", "SessionEnd", "PreCompact")


def describe_tool(name: str, inp: dict | None) -> str:
    """What Claude Code is doing, in words: "Editing notch.py", "Running the tests"."""
    inp = inp if isinstance(inp, dict) else {}
    path = _name(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
    if name == "Read":
        return f"Reading {path}" if path else "Reading"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"Editing {path}" if path else "Editing"
    if name == "Write":
        return f"Writing {path}" if path else "Writing"
    if name in ("Bash", "PowerShell"):
        desc = str(inp.get("description") or "").strip()
        return _short(desc, 60) if desc else "Running " + _short(str(inp.get("command") or "a command"), 50)
    if name == "Grep":
        return f"Searching for {_short(inp.get('pattern'), 40)}"
    if name in ("Glob", "LS"):
        return "Looking through files"
    if name == "WebFetch":
        return "Reading " + (urllib.parse.urlsplit(str(inp.get("url") or "")).hostname or "a web page")
    if name == "WebSearch":
        return "Searching the web for " + _short(inp.get("query"), 40)
    if name in ("Task", "Agent"):
        return "Handing off: " + _short(inp.get("description") or inp.get("prompt") or "a helper", 50)
    if name in ("TodoWrite", "TaskCreate", "TaskUpdate"):
        return "Updating its plan"
    if name == "AskUserQuestion":
        return "Asking you something"
    if name == "ExitPlanMode":
        return "Showing you its plan"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"Using {parts[1]}" if len(parts) > 1 else "Using a tool"
    return f"Using {name}" if name else "Working"


def current_todo(inp: dict | None) -> str:
    """The to-do item Claude Code says it's on, in its own words ("Building the agent bridge")."""
    todos = (inp or {}).get("todos") if isinstance(inp, dict) else None
    if not isinstance(todos, list):
        return ""
    for t in todos:
        if isinstance(t, dict) and t.get("status") == "in_progress":
            return str(t.get("activeForm") or t.get("content") or "")
    return ""


def _tool_match(data: dict) -> str:
    return json.dumps([data.get("tool_name"), data.get("tool_input")], sort_keys=True, default=str)[:4000]


def permission_question(data: dict, key: str, agent: str, where: str) -> list[Question]:
    """The notch's questions for Claude Code's permission prompt (several for an AskUserQuestion with several)."""
    name = str(data.get("tool_name") or "")
    inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    match = _tool_match(data)
    if name == "AskUserQuestion" and isinstance(inp.get("questions"), list):
        out = []
        for item in inp["questions"]:
            if not isinstance(item, dict):
                continue
            opts = [str(o.get("label") if isinstance(o, dict) else o) for o in item.get("options") or []]
            descs = [str(o.get("description") or "") for o in item.get("options") or [] if isinstance(o, dict)]
            detail = "\n".join(f"{label}: {d}" for label, d in zip(opts, descs) if d)
            out.append(Question(uuid.uuid4().hex, key, agent, _short(item.get("question"), 300),
                                _short(detail, MAX_DETAIL), opts[:6], True, where, match=match))
        return out
    if name in ("Bash", "PowerShell"):
        text, detail = (_short(inp.get("description"), 120) + "?" if inp.get("description") else "Run this command?",
                        str(inp.get("command") or ""))
    elif name in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        verb = "Write" if name == "Write" else "Edit"
        text, detail = f"{verb} {_name(inp.get('file_path') or inp.get('notebook_path')) or 'a file'}?", str(
            inp.get("file_path") or inp.get("notebook_path") or "")
    elif name == "WebFetch":
        text, detail = f"Open {urllib.parse.urlsplit(str(inp.get('url') or '')).hostname or 'a web page'}?", str(
            inp.get("url") or "")
    elif name == "ExitPlanMode":
        text, detail = "Start on this plan?", str(inp.get("plan") or "")
    else:
        text = f"Use {describe_tool(name, inp).removeprefix('Using ')}?" if name.startswith("mcp__") else f"Use {name}?"
        detail = json.dumps(inp, ensure_ascii=False)[:MAX_DETAIL] if inp else ""
    opts = ["Allow", "Always allow", "Deny"] if data.get("permission_suggestions") else ["Allow", "Deny"]
    return [Question(uuid.uuid4().hex, key, agent, _short(text, 300), _short(detail, MAX_DETAIL), opts, True, where,
                     match=match)]


def permission_reply(data: dict, answers: list[Question]) -> dict:
    """Claude Code's answer for its PermissionRequest hook, from what was picked on the notch."""
    name = str(data.get("tool_name") or "")
    inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    out = {"hookEventName": "PermissionRequest"}
    if name == "AskUserQuestion":
        picked = {q.text: q.answer for q in answers if q.answer is not None}
        full = {str(item.get("question")): picked.get(_short(item.get("question"), 300))
                for item in inp.get("questions") or [] if isinstance(item, dict)}
        out["decision"] = {"behavior": "allow", "updatedInput": {**inp, "answers": {k: v for k, v in full.items()
                                                                                   if v is not None}}}
        return {"hookSpecificOutput": out}
    q = answers[0]
    if q.typed:
        out["decision"] = {"behavior": "deny", "message": f"The user answered from the notch instead: {q.answer}"}
    elif q.answer in ("Allow", "Always allow"):
        out["decision"] = {"behavior": "allow"}
        if q.answer == "Always allow" and isinstance(data.get("permission_suggestions"), list):
            out["decision"]["updatedPermissions"] = data["permission_suggestions"]
    else:
        out["decision"] = {"behavior": "deny", "message": "The user said no from the notch."}
    return {"hookSpecificOutput": out}


def claude_hook(hub: Hub, data: dict, ask_seconds: float) -> dict | None:
    """One Claude Code hook event → the notch. Returns what the hook prints back to Claude Code (None: nothing)."""
    event = str(data.get("hook_event_name") or "")
    sid = str(data.get("session_id") or "default")
    key = f"claude:{sid}"
    agent = "Claude Code"
    where = _name(data.get("cwd") or "")
    if event == "SessionEnd":
        hub.end(key)
        return None
    if event == "SessionStart":
        hub.update(key, agent, state="idle", text="Ready", step="", where=where)
    elif event == "UserPromptSubmit":
        hub.cancel(key)
        hub.update(key, agent, state="working", text="Thinking", step=_short(data.get("prompt"), 80), where=where)
    elif event == "PreToolUse":
        name, inp = str(data.get("tool_name") or ""), data.get("tool_input")
        todo = current_todo(inp) if name == "TodoWrite" else ""
        hub.update(key, agent, state="working", text=describe_tool(name, inp), where=where,
                   **({"step": todo} if todo else {}))
    elif event in ("PostToolUse", "PostToolUseFailure"):
        hub.cancel(key, _tool_match(data))
        s = hub.get(key)
        if s is not None and s.state == "waiting":
            hub.update(key, agent, state="working")
    elif event == "PreCompact":
        hub.update(key, agent, state="working", text="Tidying up its memory of the chat", where=where)
    elif event == "Notification":
        kind = str(data.get("notification_type") or "")
        if kind in ("idle_prompt", "permission_prompt", "elicitation_dialog") or not kind:
            msg = str(data.get("message") or "")
            hub.update(key, agent, state="waiting",
                       text="Waiting for you" if kind == "idle_prompt" or not msg else msg, where=where)
    elif event == "Stop":
        hub.cancel(key)
        last = _short(data.get("last_assistant_message"), 100)
        hub.update(key, agent, state="done", text=last or "Finished", where=where)
    elif event == "PermissionRequest":
        if ask_seconds <= 0:
            return None
        hub.update(key, agent, state="waiting", where=where)
        answers = []
        for q in permission_question(data, key, agent, where):
            got = hub.ask(q, ask_seconds)
            if got is None:
                return {}                       # not answered on the notch: Claude Code asks in its own window
            answers.append(got)
        return permission_reply(data, answers) if answers else {}
    return None


def codex_notify(hub: Hub, data: dict) -> None:
    """Codex CLI's notify program: a turn finished (or it needs an approval)."""
    kind = str(data.get("type") or "")
    sid = str(data.get("thread-id") or data.get("thread_id") or data.get("session-id") or "default")
    key = f"codex:{sid}"
    where = _name(data.get("cwd") or "")
    if kind == "agent-turn-complete":
        hub.update(key, "Codex", state="done", text=_short(data.get("last-assistant-message"), 100) or "Finished",
                   where=where)
    elif "approval" in kind:
        hub.update(key, "Codex", state="waiting", text="Needs your approval", where=where)
    else:
        hub.update(key, "Codex", state="working", text=_short(kind.replace("-", " "), 60) or "Working", where=where)


# --------------------------------------------------------------------------- the plain JSON API
def post_status(hub: Hub, data: dict) -> dict:
    """POST /status {"agent", "text", "state"?, "step"?, "where"?, "session"?}  ("state": "end" removes it)."""
    agent = _short(data.get("agent") or "AI", 40)
    key = "api:" + _short(data.get("session") or agent, 120)
    if data.get("state") == "end":
        hub.end(key)
        return {"ok": True}
    state = data.get("state") if data.get("state") in STATES else "working"
    s = hub.update(key, agent, state=state, text=data.get("text"), step=data.get("step"), where=data.get("where"))
    return {"ok": True, "state": s.state}


def post_ask(hub: Hub, data: dict, ask_seconds: float) -> dict:
    """POST /ask {"question", "agent"?, "options"?: [...], "detail"?, "allow_text"?, "timeout"?} → waits for the
    answer: {"answered": true, "answer": "...", "typed": false} (or answered false when nobody did in time)."""
    agent = _short(data.get("agent") or "AI", 40)
    key = "api:" + _short(data.get("session") or agent, 120)
    opts = [_short(o, 40) for o in data.get("options") or [] if str(o).strip()][:6]
    if hub.get(key) is None:
        hub.update(key, agent, state="waiting", where=data.get("where"))
    q = Question(uuid.uuid4().hex, key, agent, _short(data.get("question") or "?", 300),
                 _short(data.get("detail"), MAX_DETAIL), opts, bool(data.get("allow_text", True)) or not opts,
                 _short(data.get("where"), 60))
    try:
        wait = float(data.get("timeout") or ask_seconds)
    except (TypeError, ValueError):
        wait = ask_seconds
    got = hub.ask(q, min(max(5.0, wait), 3600.0))
    if got is None:
        return {"answered": False, "answer": None}
    return {"answered": True, "answer": got.answer, "typed": got.typed}


# --------------------------------------------------------------------------- MCP (streamable HTTP)
MCP_TOOLS = [
    {"name": "notch_status",
     "description": "Show the user, on the RotaryUI notch at the top of their screen, what you're doing right now. "
                    "Call it whenever you move on to a new phase of the work (for example from designing the UI to "
                    "testing it), and with state 'done' when you finish.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "A few words: what you're doing now, e.g. 'Writing the tests'"},
         "state": {"type": "string", "enum": ["working", "waiting", "done", "error"], "default": "working"},
         "step": {"type": "string", "description": "Optional: the bigger step or goal this is part of"}},
         "required": ["text"]}},
    {"name": "ask_user",
     "description": "Ask the user a question on the RotaryUI notch and wait for the answer. They can click one of "
                    "the options or type a reply. Use it when you need a decision to go on.",
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string"},
         "options": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                     "description": "Short answers to pick from (optional)"},
         "detail": {"type": "string", "description": "Optional: more context shown under the question"},
         "allow_text": {"type": "boolean", "default": True, "description": "Can they type their own answer?"}},
         "required": ["question"]}},
]


class Mcp:
    """Just enough MCP for two tools: initialize, tools/list, tools/call and ping, answered as plain JSON."""

    def __init__(self, hub: Hub, ask_seconds: Callable[[], float]):
        self.hub = hub
        self.ask_seconds = ask_seconds
        self.clients: dict[str, str] = {}       # Mcp-Session-Id → the agent's name

    def handle(self, msg, session: str) -> tuple[object | None, str]:
        """One JSON-RPC message (or a batch) → (the reply, or None for notifications; the session id)."""
        if isinstance(msg, list):
            replies = []
            for m in msg:
                r, session = self.handle(m, session)
                if r is not None:
                    replies.append(r)
            return (replies or None), session
        if not isinstance(msg, dict) or "method" not in msg:
            return None, session
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:
            return None, session                # a notification: nothing to say back
        try:
            if method == "initialize":
                session = session or uuid.uuid4().hex
                info = params.get("clientInfo") or {}
                raw = str(info.get("name") or "AI")
                self.clients[session] = CLIENT_NAMES.get(raw.lower(), _short(raw, 40))
                want = params.get("protocolVersion")
                result = {"protocolVersion": want if want in MCP_VERSIONS else MCP_VERSIONS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "rotaryui-notch", "title": "RotaryUI notch", "version": "1"},
                          "instructions": "Use notch_status to show the user what you're working on, and ask_user "
                                          "to ask them something; both appear on the notch at the top of their "
                                          "screen."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": MCP_TOOLS}
            elif method == "tools/call":
                result = self._call(str(params.get("name") or ""), params.get("arguments") or {}, session)
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"No method {method}"}}, session
        except Exception as e:
            log.exception("MCP %s failed", method)
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}, session
        return {"jsonrpc": "2.0", "id": mid, "result": result}, session

    def _call(self, name: str, args: dict, session: str) -> dict:
        agent = self.clients.get(session, "AI")
        key = f"mcp:{session or agent}"
        if name == "notch_status":
            state = args.get("state") if args.get("state") in STATES else "working"
            self.hub.update(key, agent, state=state, text=args.get("text"), step=args.get("step") or None)
            return {"content": [{"type": "text", "text": "Shown on the notch."}]}
        if name == "ask_user":
            opts = [_short(o, 40) for o in args.get("options") or [] if str(o).strip()][:6]
            if self.hub.get(key) is None:
                self.hub.update(key, agent, state="waiting")
            q = Question(uuid.uuid4().hex, key, agent, _short(args.get("question") or "?", 300),
                         _short(args.get("detail"), MAX_DETAIL), opts, bool(args.get("allow_text", True)) or not opts)
            got = self.hub.ask(q, self.ask_seconds())
            if got is None:
                return {"content": [{"type": "text", "text": "The user didn't answer on the notch. Ask them in "
                                                             "the chat instead."}]}
            return {"content": [{"type": "text", "text": f"The user answered: {got.answer}"}]}
        return {"content": [{"type": "text", "text": f"No tool called {name}"}], "isError": True}


# --------------------------------------------------------------------------- the pass-through
def proxy_model(path: str, body: bytes) -> str:
    """Which model a request is for: the JSON body's "model", or Gemini's …/models/<model>:generateContent."""
    m = re.search(r"/models/([^/:?]+)", path)
    if m:
        return m.group(1)
    try:
        data = json.loads(body) if body[:1] == b"{" else {}
    except ValueError:
        data = {}
    return _short(data.get("model"), 50) if isinstance(data, dict) else ""


def is_generation(path: str) -> bool:
    """A request that makes the model answer (not one that lists models or checks the version)."""
    return bool(re.search(r"(chat|completions|messages|generate|responses|embed|Content)", path, re.I))


HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "te", "trailer", "upgrade", "host",
       "content-length", "proxy-authorization"}


# --------------------------------------------------------------------------- the server
class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class Bridge:
    """The web server on 127.0.0.1 that AI tools talk to."""

    def __init__(self, hub: Hub, cfg_getter: Callable[[], dict]):
        self.hub = hub
        self.cfg = cfg_getter
        self.mcp = Mcp(hub, lambda: float(settings(self.cfg())["ask_seconds"]))
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None
        self.port = 0
        self.token = ""
        self.error = ""
        self._inflight: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self, port: int, token: str) -> bool:
        if self._httpd is not None and (int(port), token) == (self.port, self.token):
            return True
        self.stop()
        bridge = self

        class Handler(_Handler):
            pass
        Handler.bridge = bridge
        try:
            self._httpd = _Server(("127.0.0.1", int(port)), Handler)
        except OSError as e:
            self.error = f"Port {port} is in use by another app" if getattr(e, "errno", 0) in (48, 98, 10048) \
                else f"Couldn't start on port {port} ({e.strerror or e})"
            log.warning("agent bridge: %s", self.error)
            self._httpd = None
            return False
        self.port, self.token, self.error = self._httpd.server_address[1], token, ""
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.5},
                                        daemon=True, name="agent-bridge")
        self._thread.start()
        log.info("agent bridge listening on 127.0.0.1:%d", self.port)
        return True

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            log.info("agent bridge stopped")

    # -- the pass-through's status
    def _proxy_begin(self, key: str, agent: str, model: str) -> None:
        with self._lock:
            self._inflight[key] = self._inflight.get(key, 0) + 1
        self.hub.update(key, agent, state="working", text=f"Answering with {model}" if model else "Answering",
                        where=model)

    def _proxy_end(self, key: str, agent: str, ok: bool, why: str = "") -> None:
        with self._lock:
            n = self._inflight[key] = max(0, self._inflight.get(key, 1) - 1)
        if n == 0:
            self.hub.update(key, agent, state="done" if ok else "error", text="Answered" if ok else why or "Failed")


class _Handler(http.server.BaseHTTPRequestHandler):
    bridge: Bridge
    protocol_version = "HTTP/1.0"         # one request per connection: a streamed answer ends when it closes
    server_version = "RotaryUI"

    def log_message(self, fmt, *args) -> None:
        log.debug("agent bridge: " + fmt, *args)

    # -- helpers
    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json", headers: dict | None = None):
        self.send_response(code)
        if body:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, data, headers: dict | None = None) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), headers=headers)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("too big")
        return self.rfile.read(n) if n > 0 else b""

    def _route(self) -> str | None:
        """The path after the token, or None (and a 404 sent) when the token's wrong or the Host isn't us."""
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            self._send(403)                       # another site's name pointed at this computer: not for us
            return None
        parts = self.path.split("?", 1)[0].lstrip("/").split("/", 1)
        if not self.bridge.token or not secrets.compare_digest(parts[0], self.bridge.token):
            self._send(404)
            return None
        return "/" + (parts[1] if len(parts) > 1 else "")

    def _cfg(self) -> dict:
        return settings(self.bridge.cfg())

    # -- methods
    def do_GET(self) -> None:
        route = self._route()
        if route is None:
            return
        if route.startswith("/proxy/"):
            return self._proxy(route)
        if route in ("/", "/status"):
            sessions, questions = self.bridge.hub.snapshot()
            return self._json(200, {
                "app": "RotaryUI",
                "agents": [{"agent": s.agent, "where": s.where, "state": s.state, "text": s.text, "step": s.step}
                           for s in sessions],
                "questions": [{"agent": q.agent, "question": q.text, "options": q.options} for q in questions]})
        if route == "/mcp":
            return self._send(405, headers={"Allow": "POST"})   # no server-to-client stream: every reply is JSON
        self._send(404)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_DELETE(self) -> None:
        route = self._route()
        if route is None:
            return
        if route.startswith("/proxy/"):
            return self._proxy(route)
        if route == "/mcp":
            sid = self.headers.get("Mcp-Session-Id") or ""
            self.bridge.mcp.clients.pop(sid, None)
            self.bridge.hub.end(f"mcp:{sid}")
            return self._send(200)
        self._send(404)

    def do_PUT(self) -> None:
        route = self._route()
        if route is not None:
            self._proxy(route) if route.startswith("/proxy/") else self._send(404)

    def do_POST(self) -> None:
        route = self._route()
        if route is None:
            return
        if route.startswith("/proxy/"):
            return self._proxy(route)
        try:
            raw = self._body()
            data = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "send JSON"})
        hub = self.bridge.hub
        ask = float(self._cfg()["ask_seconds"])
        try:
            if route == "/claude-code":
                reply = claude_hook(hub, data if isinstance(data, dict) else {}, ask)
                # Most hooks print nothing (anything printed for a prompt would be added to the chat)
                return self._send(200) if reply is None else self._json(200, reply)
            if route == "/codex":
                codex_notify(hub, data if isinstance(data, dict) else {})
                return self._send(200)
            if route == "/status" and isinstance(data, dict):
                return self._json(200, post_status(hub, data))
            if route == "/ask" and isinstance(data, dict):
                return self._json(200, post_ask(hub, data, ask))
            if route == "/mcp":
                if isinstance(data, dict) and data.get("method") == "initialize":
                    sid = ""
                else:
                    sid = self.headers.get("Mcp-Session-Id") or ""
                reply, sid = self.bridge.mcp.handle(data, sid)
                headers = {"Mcp-Session-Id": sid} if sid else {}
                return self._send(202, headers=headers) if reply is None else self._json(200, reply, headers)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            log.exception("agent bridge: %s failed", route)
            return self._json(500, {"error": "failed (details in RotaryUI's log)"})
        self._send(404)

    # -- the pass-through
    def _proxy(self, route: str) -> None:
        rest = route[len("/proxy/"):]
        name, _, tail = rest.partition("/")
        custom = self._cfg().get("proxies") or {}
        if name in custom and str(custom[name]).startswith(("http://", "https://")):
            agent, base = name, str(custom[name])
        elif name in UPSTREAMS:
            agent, base = UPSTREAMS[name]
        else:
            return self._json(404, {"error": f"No model service called {name!r}"})
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        url = base.rstrip("/") + "/" + tail + ("?" + query if query else "")
        try:
            body = self._body() if self.command in ("POST", "PUT") else None
        except ValueError:
            return self._json(413, {"error": "too big"})
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        model = proxy_model("/" + tail, body or b"")
        key = f"proxy:{name}:{model or 'model'}"
        track = self.command == "POST" and is_generation(tail)
        if track:
            self.bridge._proxy_begin(key, agent, model)
        ok, why = False, ""
        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        try:
            from .assist import _ssl_context
            try:
                resp = urllib.request.urlopen(req, timeout=600, context=_ssl_context())
            except urllib.error.HTTPError as e:
                resp, why = e, f"Failed (error {e.code})"
            with resp:
                self.send_response(resp.status if hasattr(resp, "status") else resp.code)
                for k, v in resp.headers.items():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            ok = not why
        except urllib.error.URLError as e:
            why = f"Couldn't reach {agent}"
            self._json(502, {"error": f"{why} ({getattr(e, 'reason', e)})"})
        except (BrokenPipeError, ConnectionResetError):
            why = "Stopped"
        except Exception as e:
            why = "Failed"
            log.info("agent bridge: passing a request on to %s failed: %s", agent, e)
        finally:
            if track:
                self.bridge._proxy_end(key, agent, ok, why)


# --------------------------------------------------------------------------- connecting the tools
MARK = "rotaryui-notch"


def claude_settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"


def claude_hook_command(url: str, wait: float = 0) -> str:
    """curl comes with Windows 10 and later, macOS and Linux. If RotaryUI isn't running, it fails quietly and
    Claude Code carries on as usual."""
    secs = int(wait) + 10 if wait else 3
    curl = "curl.exe" if os.name == "nt" else "curl"      # PowerShell calls Invoke-WebRequest "curl"
    return (f'{curl} -s -m {secs} -X POST -H "Content-Type: application/json" -H "X-RotaryUI: {MARK}" '
            f'--data-binary @- "{url}/claude-code"')


def claude_hooks(url: str, ask_seconds: float) -> dict:
    hooks = {}
    for event in CLAUDE_EVENTS:
        wait = ask_seconds if event == "PermissionRequest" else 0
        entry = {"type": "command", "command": claude_hook_command(url, wait), "timeout": int(wait) + 20 if wait else 5}
        hooks[event] = [{"hooks": [entry]}]
    return hooks


def _ours(group) -> bool:
    return isinstance(group, dict) and any(MARK in str(h.get("command", "")) for h in group.get("hooks") or []
                                           if isinstance(h, dict))


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} isn't a JSON object")
    return data


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".rotaryui-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def claude_connected(url: str, path: Path | None = None) -> bool:
    try:
        data = _read_json(path or claude_settings_path())
    except (OSError, ValueError):
        return False
    groups = (data.get("hooks") or {}).get("PreToolUse") or []
    return any(_ours(g) and url in json.dumps(g) for g in groups)


def connect_claude(url: str, ask_seconds: float, path: Path | None = None) -> None:
    """Add the notch's hooks to Claude Code's user settings, keeping everything else there (and replacing the
    notch's own hooks from before, say for another port)."""
    path = path or claude_settings_path()
    data = _read_json(path)
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    for event, groups in claude_hooks(url, ask_seconds).items():
        kept = [g for g in hooks.get(event) or [] if not _ours(g)]
        hooks[event] = kept + groups
    data["hooks"] = hooks
    _write(path, json.dumps(data, indent=2) + "\n")


def disconnect_claude(path: Path | None = None) -> None:
    path = path or claude_settings_path()
    data = _read_json(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in list(hooks):
        if isinstance(hooks[event], list):
            hooks[event] = [g for g in hooks[event] if not _ours(g)]
            if not hooks[event]:
                del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    _write(path, json.dumps(data, indent=2) + "\n")


def codex_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


def codex_lines(url: str) -> tuple[str, str]:
    """(the notify line, which goes before any [table]; the MCP server's table, which goes at the end)."""
    notify = ('notify = ["curl", "-s", "-m", "3", "-X", "POST", "-H", "Content-Type: application/json", '
              f'"{url}/codex", "--data-binary"]  # {MARK}')
    mcp = f'[mcp_servers.rotaryui]  # {MARK}\nurl = "{url}/mcp"  # {MARK}'
    return notify, mcp


def _strip_codex(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if MARK not in line)


def codex_connected(url: str, path: Path | None = None) -> bool:
    try:
        return f"{url}/codex" in (path or codex_config_path()).read_text(encoding="utf-8")
    except OSError:
        return False


def connect_codex(url: str, path: Path | None = None) -> str:
    """Tell Codex about the notch: its notify program, and the MCP tools. Returns a note ('' = all of it) — Codex
    takes one notify program, so one that's already set up is left alone."""
    path = path or codex_config_path()
    text = _strip_codex(path.read_text(encoding="utf-8")) if path.exists() else ""
    notify, mcp = codex_lines(url)
    note = ""
    if re.search(r"(?m)^\s*notify\s*=", text):
        note = "Codex already runs another notify program, so only the MCP tools were added"
        head = ""
    else:
        head = notify + "\n"
    if re.search(r"(?m)^\s*\[mcp_servers\.rotaryui\]", text):
        note = note or "Codex already has an MCP server called rotaryui; it was left as it is"
        mcp = ""
    body = text.strip("\n")
    _write(path, head + body + ("\n\n" if body else "") + (mcp + "\n" if mcp else ""))
    return note


def disconnect_codex(path: Path | None = None) -> None:
    path = path or codex_config_path()
    if path.exists():
        _write(path, _strip_codex(path.read_text(encoding="utf-8")).strip("\n") + "\n")
