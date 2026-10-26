"""Screen assist: an AI looks at the screen, finds the question or problem on it and answers on the notch.

It works with free AI services: Google Gemini (a free API key from Google AI Studio), Groq and OpenRouter
(free tiers), and Ollama (free and private, it runs on this computer). Paid accounts work too: Claude with an
Anthropic API key, ChatGPT with an OpenAI API key (or any other OpenAI-compatible server), and Gemini's paid
models on a billed Google key. list_models() asks the service which models that key can use, so the settings can
offer them. The screenshot goes only to the service you chose, and only when you ask.

The AI always attempts an answer: with no question on screen it goes by context clues (a search, selected text,
the window title) or sums up the page. The answer comes back as JSON: whether there was a question, the option
to pick for multiple choice ("B"), a short answer for the notch and a longer, detailed one for its hover card
(which is also what Copy copies).

While the AI works on an answer the edges of the screen glow, Siri-style, in a purple-blue gradient (or one of the
other looks, your own colours, or not at all), and a note you add ("I'm in Chemistry 101", "only the second
question") goes along with the screenshot to say what you're after.
"""
from __future__ import annotations

import base64
import functools
import ipaddress
import json
import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import MAC, winapi

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    style: str          # gemini | openai | ollama | anthropic: which API it speaks
    url: str            # the API's base address
    model: str          # the default: a model that can see images (a free one, unless the service is paid)
    key_url: str        # where to get a key ('' = none needed)
    note: str
    paid: bool = False  # needs an account with billing; there's no free tier


PROVIDERS = [
    Provider("gemini", "Google Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta",
             "gemini-flash-latest", "https://aistudio.google.com/apikey",
             "Free. Get a key from Google AI Studio with your Google account; no card needed. Turn on billing "
             "for that key to pick the Pro models."),
    Provider("groq", "Groq", "openai", "https://api.groq.com/openai/v1",
             "meta-llama/llama-4-scout-17b-16e-instruct", "https://console.groq.com/keys",
             "Free and very fast. Get a key from the Groq console."),
    Provider("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
             "google/gemma-3-27b-it:free", "https://openrouter.ai/keys",
             "Free models end in :free. Get a key from OpenRouter."),
    Provider("ollama", "Ollama (on this computer)", "ollama", "http://localhost:11434", "gemma3",
             "", "Free and private: nothing leaves this computer. Install Ollama from ollama.com, then run "
                 "\"ollama pull gemma3\" (or another model that can see images, like qwen2.5vl)."),
    Provider("anthropic", "Claude (Anthropic)", "anthropic", "https://api.anthropic.com", "claude-opus-5-5",
             "https://platform.claude.com/settings/keys",
             "Paid per use. Create an API key on the Claude Platform; a Claude Pro or Max plan doesn't include "
             "API use, so add credit there first.", paid=True),
    Provider("openai", "ChatGPT / OpenAI-compatible", "openai", "https://api.openai.com/v1", "gpt-6-astra",
             "https://platform.openai.com/api-keys",
             "Paid per use. Create an API key on the OpenAI Platform; a ChatGPT Plus or Pro plan doesn't include "
             "API use. Change the address to use LM Studio or any other OpenAI-compatible server.", paid=True),
]
BY_ID = {p.id: p for p in PROVIDERS}

# The screen's glow while the AI answers: id → (name, colours around the edge).
GLOWS = {
    "siri": ("Siri", ("#3D7BFF", "#7A5CFF", "#BF5AF2", "#5AC8FA")),
    "aurora": ("Aurora", ("#30D158", "#64D2FF", "#0A84FF", "#5E5CE6")),
    "sunset": ("Sunset", ("#FF9F0A", "#FF375F", "#BF5AF2", "#FF6482")),
}
GLOW_COLORS = list(GLOWS["siri"][1][:3])     # where "Custom" starts
NOTE_MAX = 500           # characters of the note that go along with the screenshot

DEFAULTS = {"provider": "gemini", "models": {}, "urls": {}, "keys": {}, "seconds": 20,
            "hotkey": "ctrl+alt+a" if MAC else "win+alt+a",
            "glow": "siri", "glow_colors": GLOW_COLORS, "note": "", "ask_note": False}
TIMEOUT = 90.0
MAX_REPLY = 32_000_000   # bytes: no AI answer (or list of models) comes anywhere near this
ANTHROPIC_VERSION = "2026-10-21"
# Claude models that accept "fallbacks": "default": when one declines a screenshot, the API answers with another.
ANTHROPIC_FALLBACK = ("claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")
MAX_SIDE = 1600          # screenshots are scaled down to this: plenty to read text, quick to upload

SYSTEM = """You look at a screenshot of someone's computer screen and help with what's on it right now: a quiz or \
homework question, a multiple-choice question, a maths or logic problem, a puzzle, an error message, a bug in \
code, a form that needs filling in, something they searched for, or simply the page they're reading.

Reply with one JSON object and nothing else:
{"found": true, "choice": "", "answer": "", "detail": ""}

- found: true when the screen asks a question or shows a problem to solve; false when you're answering from \
context clues or summing up the screen instead. Either way, still fill in answer and detail.
- choice: for multiple choice, the label of the correct option exactly as it appears ("B", "3", "iv"); \
several labels separated by commas when more than one is correct. Otherwise "".
- answer: the short answer for a small notch, as short as possible (60 characters at most): "x = 4", "Paris", \
"Add a closing bracket on line 12", "Open-world action RPG, 92% positive". For multiple choice, the text of \
the chosen option.
- detail: the longer, more detailed answer people read when they hover the notch and copy to paste elsewhere: \
3 to 6 sentences (700 characters at most), plain text, no markdown. It must make sense on its own, so restate \
the answer (and the option) first, then explain why, show the key steps of the working, or give the next steps \
and useful facts.

Always attempt an answer; never reply that there's nothing to answer. When nothing on the screen asks a \
question outright, use the context clues: what's typed into a search box or the address bar (a Google search \
is a question: answer it), selected or highlighted text, an error or dialog, the page's heading, the app and \
window title. When there's no question at all, sum up what's on screen and what's useful to know about it: on \
a Steam store page, what the game is, its price and its reviews; on an article, its main points; in code, what \
it does and anything wrong with it.

If there are several questions, answer the one that is selected, highlighted or most prominent. Work the problem \
out carefully before you answer.

The person may add a note to the screenshot. It comes from them, so go by it: it says which question they mean, \
what they want to know, or gives context (their course, their level, the language to answer in). It's only a \
note: still answer what's on the screen."""

ASK = "What is the question or problem on this screen (or, if there's none, what's on it), and what's the answer?"
SHORT_MAX = 80           # characters: the notch's one-line answer
DETAIL_MAX = 800         # characters: the hover card's longer answer, and what Copy copies


def ask_text(app: str = "", title: str = "", note: str = "") -> str:
    """The question sent with the screenshot, with the app in front and its window title as context clues, and
    the person's own note, if they added one."""
    clues = []
    if app:
        clues.append(f"the app in front is {_short(app, 60)}")
    if title:
        clues.append(f'its window title is "{_short(title, 200)}"')
    text = f"{ASK} ({'; '.join(clues)}.)" if clues else ASK
    note = _short(note, NOTE_MAX)
    return f"{text}\n\nMy note: {note}" if note else text


def glow_colors(cfg: dict | None) -> list[str]:
    """The colours around the screen's edge while the AI answers; [] when the glow is off."""
    a = settings(cfg)
    look = str(a.get("glow") or "off")
    if look in GLOWS:
        return list(GLOWS[look][1])
    if look == "custom":
        out = [str(c) for c in a.get("glow_colors") or [] if re.fullmatch(r"#[0-9a-fA-F]{6}", str(c))]
        return out or list(GLOW_COLORS)
    return []


def note_for(cfg: dict | None, extra: str = "") -> str:
    """The note that goes with the screenshot: the one kept on the Screen assist page, then this time's."""
    parts = [" ".join(str(t or "").split()) for t in (settings(cfg).get("note"), extra)]
    return " ".join(p for p in parts if p)


class AssistError(Exception):
    """Something the notch can show as it is."""


@dataclass
class Answer:
    found: bool
    choice: str = ""
    answer: str = ""
    detail: str = ""

    def headline(self) -> str:
        """One line for the notch or the status pill: "B · Paris", or just the answer."""
        if self.choice and self.answer:
            return f"{self.choice} · {self.answer}"
        return self.choice or self.answer

    def spoken(self) -> str:
        if self.choice:
            return f"Answer {self.choice}. {self.answer}".strip()
        return self.answer


# --------------------------------------------------------------------------- settings
def settings(cfg: dict | None) -> dict:
    return {**DEFAULTS, **((cfg or {}).get("assist") or {})}


def provider(cfg: dict | None) -> Provider:
    return BY_ID.get(settings(cfg)["provider"], PROVIDERS[0])


def model_for(cfg: dict | None, p: Provider) -> str:
    return str(settings(cfg)["models"].get(p.id) or p.model).strip()


def url_for(cfg: dict | None, p: Provider) -> str:
    return str(settings(cfg)["urls"].get(p.id) or p.url).strip().rstrip("/")


def key_for(cfg: dict | None, p: Provider) -> str:
    token = settings(cfg)["keys"].get(p.id, "")
    if not token:
        return ""
    try:
        return winapi.unprotect(token)
    except Exception:
        log.info("couldn't read the saved %s key", p.name, exc_info=True)
        return ""


def save_key(cfg: dict, provider_id: str, key: str) -> None:
    """Keep an API key the way the OBS password is kept: encrypted for this user (Windows), in the
    Keychain (macOS) or the keyring (Linux, when there is one)."""
    keys = cfg.setdefault("assist", {}).setdefault("keys", {})
    key = key.strip()
    if key:
        keys[provider_id] = winapi.protect(key, f"assist-{provider_id}")
    else:
        keys.pop(provider_id, None)


def ready(cfg: dict | None) -> str:
    """'' when screen assist can run; otherwise what's missing, in words."""
    p = provider(cfg)
    if not p.key_url or key_for(cfg, p):
        return ""
    if p.id == "openai" and url_for(cfg, p) != p.url:
        return ""                                   # LM Studio and other local servers need no key
    return f"Add {'your' if p.paid else 'a free'} {p.name} API key on the Screen assist page"


# --------------------------------------------------------------------------- the request
def build_request(p: Provider, model: str, base: str, key: str, image: bytes,
                  mime: str = "image/jpeg", ask: str = ASK) -> tuple[str, dict, dict]:
    """(url, headers, JSON body) for one screenshot. No network here, so it can be tested."""
    b64 = base64.b64encode(image).decode("ascii")
    headers = {"Content-Type": "application/json", "User-Agent": "RotaryUI"}
    if p.style == "gemini":
        if key:
            headers["x-goog-api-key"] = key
        body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"inline_data": {"mime_type": mime, "data": b64}},
                                                        {"text": ask}]}],
                "generationConfig": {"responseMimeType": "application/json"}}
        return f"{base}/models/{model}:generateContent", headers, body
    if p.style == "ollama":
        body = {"model": model, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": ask, "images": [b64]}]}
        return f"{base}/api/chat", headers, body
    if p.style == "anthropic":
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
        body = {"model": model, "max_tokens": 16000, "system": SYSTEM,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": ask}]}]}
        if model.startswith(ANTHROPIC_FALLBACK):
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            body["fallbacks"] = "default"
        return f"{base}/v1/messages", headers, body
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if "openrouter.ai" in base:
        headers["HTTP-Referer"] = "https://github.com/MochiBoy9/Gesture_Rotary"
        headers["X-Title"] = "RotaryUI"
    # No response_format: not every OpenAI-compatible server accepts it alongside an image. The prompt asks
    # for JSON and parse_reply() copes with a little extra around it.
    body = {"model": model,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": [
                             {"type": "text", "text": ask},
                             {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]}
    return f"{base}/chat/completions", headers, body


def reply_text(style: str, data: dict) -> str:
    """The model's words out of each API's response."""
    if style == "gemini":
        cands = data.get("candidates") or []
        if not cands:
            why = (data.get("promptFeedback") or {}).get("blockReason")
            raise AssistError(f"Gemini didn't answer ({why})" if why else "Gemini didn't answer")
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(str(pt.get("text", "")) for pt in parts if not pt.get("thought"))
    if style == "ollama":
        return str((data.get("message") or {}).get("content", ""))
    if style == "anthropic":
        if data.get("stop_reason") == "refusal":
            raise AssistError("Claude declined to answer this one")
        return "".join(str(b.get("text", "")) for b in data.get("content") or [] if b.get("type") == "text")
    choices = data.get("choices") or []
    if not choices:
        raise AssistError("The AI didn't answer")
    content = (choices[0].get("message") or {}).get("content", "")
    if isinstance(content, list):                       # some servers return content parts
        content = "".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return str(content or "")


def _clean_choice(choice: str) -> str:
    """'(b)' → 'B', 'Option C.' → 'C', 'a, c' → 'A, C'."""
    parts = []
    for part in re.split(r"[,/&]|\band\b", str(choice)):
        part = re.sub(r"^\s*(option|answer|choice)\s*", "", part.strip(), flags=re.I).strip(" ().:[]-")
        if not part:
            continue
        parts.append(part.upper() if len(part) == 1 else part)
    return ", ".join(parts)[:16]


def _short(text, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def parse_reply(text: str) -> Answer:
    """The model's reply → an Answer. Copes with code fences and words around the JSON; when there's no
    JSON at all, the reply itself is the answer."""
    text = (text or "").strip()
    if not text:
        raise AssistError("The AI sent an empty answer")
    data = None
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            data = None
    if not isinstance(data, dict):
        lines = [ln.strip() for ln in text.strip("`").splitlines() if ln.strip()]
        return Answer(True, "", _short(lines[0] if lines else text, SHORT_MAX),
                      _short(" ".join(lines[1:]), DETAIL_MAX))
    found = data.get("found", True)
    if isinstance(found, str):
        found = found.strip().lower() not in ("false", "no", "0", "")
    ans = Answer(bool(found), _clean_choice(data.get("choice") or ""), _short(data.get("answer"), SHORT_MAX),
                 _short(data.get("detail"), DETAIL_MAX))
    if not ans.answer and ans.detail:              # only the long answer: its first sentence goes on the notch
        ans.answer = _short(re.split(r"(?<=[.!?])\s", ans.detail, maxsplit=1)[0], SHORT_MAX)
    if not (ans.choice or ans.answer):
        ans.found = False                          # nothing to show at all
    return ans


@functools.lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """One context for every HTTPS call (screen assist, updates, weather): loading the CA list each time is slow."""
    try:
        import certifi                       # comes with MediaPipe; the packaged Mac app has no system CA path
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _error_message(raw: bytes) -> str:
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return raw.decode("utf-8", "replace").strip()[:160]
    if isinstance(data, list) and data:
        data = data[0]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or "")[:160]
    return str(err or "")[:160]


def is_local(url: str) -> bool:
    """An address on this computer or the local network (Ollama, LM Studio…), where plain http is normal."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith((".local", ".localhost", ".lan", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host and not host.isdigit()     # a machine on the network by its name: "gpu-box"
    return ip.is_loopback or ip.is_private or ip.is_link_local


SECRET_HEADERS = ("authorization", "x-api-key", "x-goog-api-key")


def check_address(url: str, headers: dict, p: Provider) -> None:
    """Never send an API key in the clear: over the internet it has to be https."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise AssistError(f"{p.name}'s address has to start with https://")
    has_key = any(k.lower() in SECRET_HEADERS and v for k, v in headers.items())
    if scheme == "http" and has_key and not is_local(url):
        raise AssistError(f"{p.name}'s address has to start with https:// to send your API key safely")


def post(url: str, headers: dict, body: dict | None, p: Provider, model: str = "", timeout: float = TIMEOUT) -> dict:
    """POST `body` as JSON, or GET when there's no body; the reply's JSON, or an AssistError in words."""
    check_address(url, headers, p)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            got = json.loads(r.read(MAX_REPLY).decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = _error_message(e.read(64_000) or b"")
        log.info("%s answered %s: %s", p.name, e.code, msg)
        if e.code in (401, 403):
            raise AssistError(f"{p.name} refused the API key") from None
        if e.code == 429:
            if p.paid:
                raise AssistError(f"{p.name} says you've hit your account's limit; try again in a minute") from None
            raise AssistError(f"{p.name}'s free limit is used up for now; try again in a minute") from None
        if e.code == 404:
            if not model:
                raise AssistError(f"{p.name} has no list of models at this address") from None
            if p.style == "ollama":
                raise AssistError(f"Ollama doesn't have {model} yet; run: ollama pull {model}") from None
            raise AssistError(f"{p.name} doesn't know the model {model}") from None
        raise AssistError(f"{p.name}: {msg or f'error {e.code}'}") from None
    except urllib.error.URLError as e:
        if p.style == "ollama":
            raise AssistError("Ollama isn't running; install it from ollama.com and open it") from None
        raise AssistError(f"Couldn't reach {p.name} ({getattr(e, 'reason', e)})") from None
    except TimeoutError:
        raise AssistError(f"{p.name} took too long to answer") from None
    except ValueError:
        raise AssistError(f"{p.name} sent something that isn't JSON") from None
    except OSError as e:                       # the connection dropped halfway through the answer
        raise AssistError(f"Couldn't reach {p.name} ({e})") from None
    if not isinstance(got, dict):
        raise AssistError(f"{p.name} sent an answer it couldn't read")
    return got


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def solve(image: bytes, cfg: dict | None, mime: str = "image/jpeg", app: str = "", title: str = "",
          note: str = "") -> Answer:
    """Send one screenshot and return the answer (blocking; run it off the UI thread). `app` and `title`, the app
    in front and its window title, go along as context clues; `note` is the person's own (note_for())."""
    missing = ready(cfg)
    if missing:
        raise AssistError(missing)
    p = provider(cfg)
    model = model_for(cfg, p)
    url, headers, body = build_request(p, model, url_for(cfg, p), key_for(cfg, p), image, mime,
                                       ask_text(app, title, note))
    log.info("screen assist: asking %s (%s), %d KB", p.name, model, len(image) // 1024)
    text = reply_text(p.style, post(url, headers, body, p, model))
    ans = parse_reply(text)
    log.info("screen assist: found=%s choice=%r answer=%r (%d more characters)", ans.found, ans.choice, ans.answer,
             len(ans.detail))
    return ans


# --------------------------------------------------------------------------- which models a key can use
# Ids on OpenAI-compatible lists that can't answer a chat with a screenshot (speech, pictures, embeddings…).
_NOT_CHAT = re.compile(r"embed|whisper|tts|transcri|dall-e|image|realtime|audio|moderation|guard|sora|"
                       r"babbage|davinci|search|computer-use|codex|translate|live", re.I)
_GEMINI_SKIP = re.compile(r"embed|tts|image|live|transcribe|aqa|banana|omni", re.I)


def list_models(cfg: dict | None, p: Provider | None = None, timeout: float = 20.0) -> list[tuple[str, str]]:
    """[(model id, name to show)] that this service offers the saved key, newest first where the service says.
    Blocking: run it off the UI thread."""
    p = p or provider(cfg)
    base, key = url_for(cfg, p), key_for(cfg, p)
    headers = {"User-Agent": "RotaryUI"}
    out: list[tuple[str, str]] = []
    if p.style == "anthropic":
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
        after = ""
        for _page in range(10):
            data = post(f"{base}/v1/models?limit=1000" + (f"&after_id={urllib.parse.quote(after)}" if after else ""),
                        headers, None, p, timeout=timeout)
            for m in data.get("data") or []:
                if not isinstance(m, dict) or not m.get("id"):
                    continue
                caps = m.get("capabilities") or {}
                if (caps.get("image_input") or {}).get("supported", True):
                    out.append((str(m["id"]), str(m.get("display_name") or m["id"])))
            after = str(data.get("last_id") or "")
            if not data.get("has_more") or not after:
                break
        return out
    if p.style == "gemini":
        if key:
            headers["x-goog-api-key"] = key
        token = ""
        for _page in range(10):
            data = post(f"{base}/models?pageSize=1000" + (f"&pageToken={urllib.parse.quote(token)}" if token else ""),
                        headers, None, p, timeout=timeout)
            for m in data.get("models") or []:
                if not isinstance(m, dict):
                    continue
                mid = str(m.get("name", "")).removeprefix("models/")
                if ("generateContent" in (m.get("supportedGenerationMethods") or [])
                        and mid and not _GEMINI_SKIP.search(mid)):
                    out.append((mid, str(m.get("displayName") or mid)))
            token = str(data.get("nextPageToken") or "")
            if not token:
                break
        return out
    if p.style == "ollama":
        data = post(f"{base}/api/tags", headers, None, p, timeout=timeout)
        return [(str(m["name"]), str(m["name"])) for m in data.get("models") or []
                if isinstance(m, dict) and m.get("name")]
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = post(f"{base}/models", headers, None, p, timeout=timeout)
    rows = []
    for m in data.get("data") or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "")
        if not mid:
            continue
        mods = (m.get("architecture") or {}).get("input_modalities")    # OpenRouter says what each model reads
        if mods is not None:
            if "image" not in mods:
                continue
        elif _NOT_CHAT.search(mid):
            continue
        rows.append((-_int(m.get("created")), mid, str(m.get("name") or mid)))
    return [(mid, name) for _c, mid, name in sorted(rows)]


# --------------------------------------------------------------------------- the screenshot
def capture(screen=None) -> bytes:
    """The screen under the mouse as a JPEG (call on the UI thread). The notch and the rotary never show up
    in it on Windows; on macOS it needs the Screen Recording permission (otherwise only the wallpaper)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QCursor, QGuiApplication

    screen = screen or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    if screen is None:
        raise AssistError("No screen to look at")
    pix = screen.grabWindow(0)
    if pix.isNull() or pix.width() < 8:
        raise AssistError("Couldn't capture the screen")
    img = pix.toImage()
    if max(img.width(), img.height()) > MAX_SIDE:
        img = img.scaled(MAX_SIDE, MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG", 85)
    buf.close()
    if data.isEmpty():
        raise AssistError("Couldn't capture the screen")
    return bytes(data.data())
