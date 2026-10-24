"""Screen assist: building each service's request, reading its reply, errors, and the notch's answer card."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from rotary import assist
from rotary.actions import ACTION_TYPES, ASSIST_ITEM, DEFAULT_CONFIG, execute
from rotary.ui.notch import NotchWindow

app = QApplication.instance() or QApplication([])
JPEG = b"\xff\xd8fake-jpeg\xff\xd9"


# ------------------------------------------------------------------ requests
def test_gemini_request_sends_the_image_and_asks_for_json():
    p = assist.BY_ID["gemini"]
    url, headers, body = assist.build_request(p, "gemini-flash-latest", p.url, "KEY", JPEG)
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    assert headers["x-goog-api-key"] == "KEY" and "Authorization" not in headers
    parts = body["contents"][0]["parts"]
    assert base64.b64decode(parts[0]["inline_data"]["data"]) == JPEG
    assert parts[0]["inline_data"]["mime_type"] == "image/jpeg"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "JSON" in body["systemInstruction"]["parts"][0]["text"]


def test_openai_style_request_for_groq_openrouter_and_chatgpt():
    p = assist.BY_ID["openrouter"]
    url, headers, body = assist.build_request(p, p.model, p.url, "sk-or", JPEG)
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-or" and headers["X-Title"] == "RotaryUI"
    assert p.model.endswith(":free")
    user = body["messages"][1]["content"]
    img = next(c for c in user if c["type"] == "image_url")["image_url"]["url"]
    assert img.startswith("data:image/jpeg;base64,") and base64.b64decode(img.split(",", 1)[1]) == JPEG
    assert body["messages"][0]["role"] == "system"
    g = assist.BY_ID["groq"]
    assert assist.build_request(g, g.model, g.url, "k", JPEG)[0] == "https://api.groq.com/openai/v1/chat/completions"


def test_ollama_request_runs_locally_without_a_key():
    p = assist.BY_ID["ollama"]
    url, headers, body = assist.build_request(p, "gemma3", p.url, "", JPEG)
    assert url == "http://localhost:11434/api/chat"
    assert "Authorization" not in headers
    assert body["format"] == "json" and body["stream"] is False
    assert base64.b64decode(body["messages"][1]["images"][0]) == JPEG


def test_claude_request_sends_the_image_with_the_api_key():
    p = assist.BY_ID["anthropic"]
    url, headers, body = assist.build_request(p, p.model, p.url, "sk-ant", JPEG)
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-ant" and "Authorization" not in headers
    assert headers["anthropic-version"] == assist.ANTHROPIC_VERSION
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] > 0 and "JSON" in body["system"]
    img, text = body["messages"][0]["content"]
    assert img["source"]["media_type"] == "image/jpeg" and base64.b64decode(img["source"]["data"]) == JPEG
    assert text == {"type": "text", "text": assist.ASK}
    assert body["fallbacks"] == "default" and headers["anthropic-beta"].startswith("server-side-fallback")
    # older models don't take fallbacks
    _url, headers, body = assist.build_request(p, "claude-haiku-4-5", p.url, "sk-ant", JPEG)
    assert "fallbacks" not in body and "anthropic-beta" not in headers


def test_every_provider_has_a_free_or_local_option_and_a_model():
    for p in assist.PROVIDERS:
        assert p.model and p.url.startswith("http") and p.style in ("gemini", "openai", "ollama", "anthropic")
    free = [p for p in assist.PROVIDERS if not p.paid]
    assert {p.id for p in free} == {"gemini", "groq", "openrouter", "ollama"}
    assert {p.id for p in assist.PROVIDERS if p.paid} == {"anthropic", "openai"}
    assert assist.BY_ID["openai"].model == "gpt-6-astra"
    assert DEFAULT_CONFIG["assist"]["provider"] == "gemini"


# ------------------------------------------------------------------ replies
def test_reply_text_from_each_api():
    assert assist.reply_text("gemini", {"candidates": [{"content": {"parts": [
        {"text": "thinking…", "thought": True}, {"text": '{"found": true}'}]}}]}) == '{"found": true}'
    assert assist.reply_text("openai", {"choices": [{"message": {"content": "hi"}}]}) == "hi"
    assert assist.reply_text("openai", {"choices": [{"message": {"content": [{"type": "text", "text": "a"}]}}]}) == "a"
    assert assist.reply_text("ollama", {"message": {"content": "ok"}}) == "ok"
    assert assist.reply_text("anthropic", {"stop_reason": "end_turn", "content": [
        {"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"found": '}, {"type": "text", "text": "true}"}]}
    ) == '{"found": true}'
    with pytest.raises(assist.AssistError, match="declined"):
        assist.reply_text("anthropic", {"stop_reason": "refusal", "content": []})
    with pytest.raises(assist.AssistError, match="SAFETY"):
        assist.reply_text("gemini", {"promptFeedback": {"blockReason": "SAFETY"}})


def test_parse_multiple_choice():
    a = assist.parse_reply('{"found": true, "choice": "(b)", "answer": "Paris", "detail": "Capital of France."}')
    assert (a.found, a.choice, a.answer, a.detail) == (True, "B", "Paris", "Capital of France.")
    assert a.headline() == "B · Paris"
    assert a.spoken() == "Answer B. Paris"


@pytest.mark.parametrize("raw,want", [("Option C.", "C"), ("a, c", "A, C"), ("B and D", "B, D"), ("3", "3"),
                                      ("iv", "iv"), ("", "")])
def test_choice_labels_are_tidied(raw, want):
    assert assist._clean_choice(raw) == want


def test_parse_copes_with_fences_and_chatter():
    a = assist.parse_reply('Sure!\n```json\n{"found": true, "choice": "", "answer": "x = 4"}\n```')
    assert a.found and a.choice == "" and a.headline() == "x = 4"


def test_parse_only_a_long_answer():
    a = assist.parse_reply(json.dumps({"found": True, "answer": "", "detail": "It's 42. Six times seven."}))
    assert a.found and a.answer == "It's 42." and a.detail == "It's 42. Six times seven."


def test_the_prompt_always_attempts_an_answer_with_context_clues():
    assert "Always attempt an answer" in assist.SYSTEM and "Steam" in assist.SYSTEM
    assert assist.ask_text() == assist.ASK
    ask = assist.ask_text("steam", "ELDEN RING on Steam")
    assert ask.startswith(assist.ASK) and "steam" in ask and '"ELDEN RING on Steam"' in ask
    p = assist.BY_ID["gemini"]
    _url, _headers, body = assist.build_request(p, p.model, p.url, "k", JPEG, ask=ask)
    assert body["contents"][0]["parts"][1]["text"] == ask


def test_parse_nothing_found_and_plain_text():
    assert not assist.parse_reply('{"found": false, "choice": "", "answer": "", "detail": ""}').found
    assert not assist.parse_reply('{"found": "false"}').found
    assert not assist.parse_reply('{"found": true, "choice": "", "answer": ""}').found   # nothing to show
    summary = assist.parse_reply('{"found": false, "answer": "Elden Ring, $59.99", "detail": "An action RPG."}')
    assert not summary.found and summary.answer == "Elden Ring, $59.99"     # no question: still an answer
    a = assist.parse_reply("The answer is 42.\nBecause six times seven.")
    assert a.found and a.answer == "The answer is 42." and a.detail == "Because six times seven."
    with pytest.raises(assist.AssistError):
        assist.parse_reply("  ")


def test_long_answers_are_cut_for_the_notch():
    a = assist.parse_reply(json.dumps({"found": True, "answer": "word " * 60, "detail": "more " * 100}))
    assert len(a.answer) <= assist.SHORT_MAX and a.answer.endswith("…") and len(a.detail) <= assist.DETAIL_MAX
    assert len(a.detail) > 400                       # the hover card's answer is the longer, detailed one


# ------------------------------------------------------------------ settings
def test_ready_says_what_is_missing():
    assert "Google Gemini API key" in assist.ready({"assist": {"provider": "gemini"}})
    assert assist.ready({"assist": {"provider": "ollama"}}) == ""
    assert "OpenAI" in assist.ready({"assist": {"provider": "openai"}})
    assert "your Claude (Anthropic) API key" in assist.ready({"assist": {"provider": "anthropic"}})
    local = {"assist": {"provider": "openai", "urls": {"openai": "http://localhost:1234/v1"}}}
    assert assist.ready(local) == ""                          # LM Studio: no key
    assert assist.url_for(local, assist.BY_ID["openai"]) == "http://localhost:1234/v1"
    assert assist.model_for({"assist": {"models": {"groq": " my-model "}}}, assist.BY_ID["groq"]) == "my-model"


def test_saved_key_round_trips(monkeypatch):
    monkeypatch.setattr(assist.winapi, "protect", lambda text, account: "enc:" + text[::-1])
    monkeypatch.setattr(assist.winapi, "unprotect", lambda token: token[4:][::-1])
    cfg = {"assist": {"provider": "groq"}}
    assist.save_key(cfg, "groq", "  gsk_123 ")
    assert cfg["assist"]["keys"]["groq"] == "enc:321_ksg"
    assert assist.key_for(cfg, assist.BY_ID["groq"]) == "gsk_123"
    assert assist.ready(cfg) == ""
    assist.save_key(cfg, "groq", "")
    assert "groq" not in cfg["assist"]["keys"]


def test_assist_is_an_item_type_that_runs_inside_the_app():
    assert "assist" in ACTION_TYPES and ASSIST_ITEM["action"] == {"type": "assist"}
    with pytest.raises(ValueError):
        execute({"type": "assist"})


# ------------------------------------------------------------------ talking to a server
class _Server:
    """A local stand-in for an AI service: replies with `status` and `body` (or, when `body` is a function, what it
    returns for the path asked), and remembers the requests."""

    def __init__(self, status=200, body=None):
        outer = self
        self.status, self.body, self.seen, self.paths = status, body or {}, {}, []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                outer.seen = {"path": self.path, "headers": self.headers,      # any case
                              "body": json.loads(self.rfile.read(n)) if n else None}
                outer.paths.append(self.path)
                reply = outer.body(self.path) if callable(outer.body) else outer.body
                data = json.dumps(reply).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_solve_end_to_end_with_an_openai_compatible_server():
    reply = {"choices": [{"message": {"content": '{"found": true, "choice": "C", "answer": "Mitochondria", '
                                                 '"detail": "It makes the cell\'s energy."}'}}]}
    srv = _Server(200, reply)
    try:
        cfg = {"assist": {"provider": "openai", "urls": {"openai": srv.url + "/v1"}, "models": {"openai": "llava"}}}
        a = assist.solve(JPEG, cfg)
    finally:
        srv.close()
    assert (a.choice, a.answer) == ("C", "Mitochondria")
    assert srv.seen["path"] == "/v1/chat/completions" and srv.seen["body"]["model"] == "llava"


def test_solve_with_ollama():
    srv = _Server(200, {"message": {"content": '{"found": true, "choice": "", "answer": "12"}'}})
    try:
        a = assist.solve(JPEG, {"assist": {"provider": "ollama", "urls": {"ollama": srv.url}}})
    finally:
        srv.close()
    assert a.answer == "12" and srv.seen["path"] == "/api/chat" and srv.seen["body"]["model"] == "gemma3"


def test_solve_sends_the_window_title_as_a_clue():
    srv = _Server(200, {"message": {"content": '{"found": false, "answer": "Elden Ring, $59.99", '
                                               '"detail": "Elden Ring is an open-world action RPG."}'}})
    try:
        a = assist.solve(JPEG, {"assist": {"provider": "ollama", "urls": {"ollama": srv.url}}},
                         app="steam", title="ELDEN RING on Steam")
    finally:
        srv.close()
    assert '"ELDEN RING on Steam"' in srv.seen["body"]["messages"][1]["content"]
    assert a.answer == "Elden Ring, $59.99" and a.detail.startswith("Elden Ring is")


@pytest.mark.parametrize("status,body,match", [
    (401, {"error": {"message": "bad key"}}, "refused the API key"),
    (429, {"error": {"message": "quota"}}, "free limit"),
    (404, {"error": "model 'gemma3' not found"}, "ollama pull gemma3"),
    (500, {"error": {"message": "boom"}}, "boom"),
])
def test_server_errors_become_short_messages(status, body, match):
    srv = _Server(status, body)
    try:
        with pytest.raises(assist.AssistError, match=match):
            assist.solve(JPEG, {"assist": {"provider": "ollama", "urls": {"ollama": srv.url}}})
    finally:
        srv.close()


def test_solve_with_claude(monkeypatch):
    srv = _Server(200, {"stop_reason": "end_turn", "content": [
        {"type": "text", "text": '{"found": true, "choice": "A", "answer": "Paris"}'}]})
    try:
        cfg = {"assist": {"provider": "anthropic", "urls": {"anthropic": srv.url}, "models": {"anthropic": "claude-x"}}}
        with pytest.raises(assist.AssistError, match="API key"):
            assist.solve(JPEG, cfg)                           # Claude always needs a key
        monkeypatch.setattr(assist.winapi, "unprotect", lambda token: "sk-ant-" + token)
        cfg["assist"]["keys"] = {"anthropic": "tok"}
        a = assist.solve(JPEG, cfg)
    finally:
        srv.close()
    assert (a.choice, a.answer) == ("A", "Paris")
    assert srv.seen["path"] == "/v1/messages" and srv.seen["body"]["model"] == "claude-x"
    assert srv.seen["headers"]["x-api-key"] == "sk-ant-tok"


def _with_key(monkeypatch, cfg, pid):
    monkeypatch.setattr(assist.winapi, "unprotect", lambda token: token)
    cfg["assist"].setdefault("keys", {})[pid] = "KEY"
    return cfg


def test_claude_models_are_listed_page_by_page_and_only_those_that_see_images(monkeypatch):
    def reply(path):
        if "after_id" not in path:
            return {"data": [{"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5",
                              "capabilities": {"image_input": {"supported": True}}},
                             {"id": "claude-blind", "display_name": "Blind",
                              "capabilities": {"image_input": {"supported": False}}}],
                    "has_more": True, "last_id": "claude-blind"}
        return {"data": [{"id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5"}], "has_more": False}
    srv = _Server(200, reply)
    try:
        cfg = _with_key(monkeypatch, {"assist": {"provider": "anthropic", "urls": {"anthropic": srv.url}}},
                        "anthropic")
        got = assist.list_models(cfg)
    finally:
        srv.close()
    assert got == [("claude-opus-5-5", "Claude Opus 5.5"), ("claude-haiku-5-5", "Claude Haiku 5.5")]
    assert srv.paths[0].startswith("/v1/models?limit=") and "after_id=claude-blind" in srv.paths[1]
    assert srv.seen["headers"]["x-api-key"] == "KEY" and srv.seen["body"] is None


def test_openai_models_skip_speech_and_pictures_newest_first(monkeypatch):
    srv = _Server(200, {"data": [{"id": "gpt-6-luna", "created": 2}, {"id": "gpt-6-astra", "created": 3},
                                 {"id": "whisper-1", "created": 9}, {"id": "gpt-image-2.5-flare", "created": 9},
                                 {"id": "text-embedding-3-large", "created": 9}, {"id": "gpt-realtime-2", "created": 9},
                                 {"id": "gpt-4o-mini-tts", "created": 9}]})
    try:
        cfg = _with_key(monkeypatch, {"assist": {"provider": "openai", "urls": {"openai": srv.url + "/v1"}}},
                        "openai")
        got = assist.list_models(cfg)
    finally:
        srv.close()
    assert [m for m, _n in got] == ["gpt-6-astra", "gpt-6-luna"]
    assert srv.seen["path"] == "/v1/models" and srv.seen["headers"]["Authorization"] == "Bearer KEY"


def test_openrouter_models_are_filtered_by_what_they_read(monkeypatch):
    srv = _Server(200, {"data": [{"id": "a/sees", "name": "Sees", "architecture": {"input_modalities": ["text", "image"]}},
                                 {"id": "b/blind", "name": "Blind", "architecture": {"input_modalities": ["text"]}}]})
    try:
        cfg = _with_key(monkeypatch, {"assist": {"provider": "openrouter", "urls": {"openrouter": srv.url}}},
                        "openrouter")
        assert assist.list_models(cfg) == [("a/sees", "Sees")]
    finally:
        srv.close()


def test_gemini_models_that_generate_text(monkeypatch):
    srv = _Server(200, {"models": [
        {"name": "models/gemini-4-argon", "displayName": "Gemini 4 Argon", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding", "supportedGenerationMethods": ["embedContent"]}]})
    try:
        cfg = _with_key(monkeypatch, {"assist": {"provider": "gemini"}}, "gemini")
        monkeypatch.setattr(assist, "url_for", lambda c, p: srv.url + "/v1beta")
        assert assist.list_models(cfg) == [("gemini-4-argon", "Gemini 4 Argon")]
    finally:
        srv.close()
    assert srv.seen["path"].startswith("/v1beta/models?") and srv.seen["headers"]["x-goog-api-key"] == "KEY"


def test_ollama_lists_what_is_pulled():
    srv = _Server(200, {"models": [{"name": "gemma3:latest"}, {"name": "qwen2.5vl"}]})
    try:
        got = assist.list_models({"assist": {"provider": "ollama", "urls": {"ollama": srv.url}}})
    finally:
        srv.close()
    assert [m for m, _n in got] == ["gemma3:latest", "qwen2.5vl"] and srv.seen["path"] == "/api/tags"


def test_listing_errors_are_short(monkeypatch):
    srv = _Server(401, {"error": {"message": "invalid x-api-key"}})
    try:
        cfg = _with_key(monkeypatch, {"assist": {"provider": "anthropic", "urls": {"anthropic": srv.url}}},
                        "anthropic")
        with pytest.raises(assist.AssistError, match="refused the API key"):
            assist.list_models(cfg)
    finally:
        srv.close()


@pytest.mark.parametrize("pid,match", [("anthropic", "account's limit"), ("groq", "free limit")])
def test_rate_limits_read_right_for_paid_and_free(monkeypatch, pid, match):
    srv = _Server(429, {"error": {"message": "slow down"}})
    try:
        cfg = _with_key(monkeypatch, {"assist": {"provider": pid, "urls": {pid: srv.url}}}, pid)
        with pytest.raises(assist.AssistError, match=match):
            assist.solve(JPEG, cfg)
    finally:
        srv.close()


def test_ollama_not_running():
    with pytest.raises(assist.AssistError, match="Ollama isn't running"):
        assist.solve(JPEG, {"assist": {"provider": "ollama", "urls": {"ollama": "http://127.0.0.1:9"}}})


def test_missing_key_is_reported_before_any_request():
    with pytest.raises(assist.AssistError, match="API key"):
        assist.solve(JPEG, {"assist": {"provider": "groq"}})


# ------------------------------------------------------------------ the screenshot
def test_capture_returns_a_jpeg():
    try:
        data = assist.capture()
    except assist.AssistError:
        pytest.skip("no screen to grab here")
    assert data[:2] == b"\xff\xd8"
    img = QImage.fromData(data)
    assert not img.isNull() and max(img.width(), img.height()) <= assist.MAX_SIDE


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


def test_notch_shows_the_answer_then_its_card_on_hover():
    n = NotchWindow()
    _settle(n)
    rest = n.body_rect().width()
    n.show_answer("B", "Paris", "Paris has been the capital of France since 987.", 20)
    _settle(n)
    assert n.answer_headline() == "B · Paris"
    assert n.body_rect().width() >= rest and n.body_rect().height() > 30
    _paint(n)
    n.hovered = True
    n._retarget()
    _settle(n)
    assert n._card_kind() == "answer"
    assert n.body_rect().width() > 400
    _paint(n)
    assert {"answer_copy", "answer_close"} <= set(n._buttons)
    n.mousePressEvent(_Press(n._buttons["answer_copy"].center()))
    assert QApplication.clipboard().text() == "Paris has been the capital of France since 987."   # the long one
    opened = []
    n.clicked.connect(lambda: opened.append(1))
    n.mousePressEvent(_Press(QPointF(n.body_rect().center().x(), 20)))   # a click puts it away, no rotary
    n.mouseReleaseEvent(_Press(QPointF(n.body_rect().center().x(), 20)))
    assert n.answer is None and opened == []


def test_long_choice_labels_and_plain_answers_paint():
    n = NotchWindow()
    n.show_answer("A, C", "Both are prime", "", 10)
    _settle(n)
    assert n._badge_extra() > 0
    _paint(n)
    n.show_answer("", "Add a closing bracket on line 12", "Line 12 opens a ( that never closes.", 10)
    n.hovered = True
    n._retarget()
    _settle(n)
    _paint(n)
    assert n.answer_headline() == "Add a closing bracket on line 12"


def test_answer_waits_while_hovered_then_expires():
    n = NotchWindow()
    n.show_answer("D", "42", "", 5)
    n.hovered = True
    n._answer_expired()
    assert n.answer is not None                    # still being read
    n.hovered = False
    n._answer_expired()
    assert n.answer is None


def test_notch_menu_offers_screen_assist():
    n = NotchWindow()
    got = []
    n.assist.connect(lambda: got.append(1))
    n.assist.emit()
    assert got == [1]


def test_shortcuts_avoid_altgr_on_windows_and_exist_on_every_system():
    from rotary import MAC, keys, posix
    assert DEFAULT_CONFIG["assist"]["hotkey"] == ("ctrl+alt+a" if MAC else "win+alt+a")
    assert assist.settings({})["hotkey"] == DEFAULT_CONFIG["assist"]["hotkey"]
    for combo in ("win+alt+a", "ctrl+alt+a", "ctrl+shift+a"):     # the ones the list used to offer still work
        _mods, vk = keys.parse(combo)
        assert posix._vk_name(vk) == "a"               # the Carbon and X11 grabs know the key
