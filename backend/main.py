"""
ML Voice Agent — backend relay (Phases 4–9).

The backend is a RELAY between the browser and Azure gpt-realtime:

    Browser ──generate(topic)─► server ──(text)──► Azure     -> returns a slide deck (JSON)
    Browser ──user_text/audio─► server ──────────► Azure     -> answer + tool calls
    Browser ◄──deck/slide/audio/answer── server ◄─ events ─── Azure

Phase 9 adds runtime deck generation: the user types ANY topic, we ask the model
for a 5–6 slide deck as JSON, configure the presentation session with that deck,
and everything downstream (Q&A, voice, slide tools) runs against it.

Run from the project root:
    .venv\\Scripts\\python.exe -u backend\\main.py
Then open http://127.0.0.1:8000
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

# Log in UTF-8 so non-ASCII topics/transcripts (any language) never crash a
# print() — the Windows console defaults to cp1252.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import uvicorn
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    from pptx_extract import extract_deck
except ImportError:  # when run as `uvicorn backend.main:app`
    from backend.pptx_extract import extract_deck

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
load_dotenv(ROOT / ".env")

ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-realtime").strip()
API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview").strip()
VOICE = os.getenv("REALTIME_VOICE", "alloy").strip()

# The app matches the user's language (STT auto-detects; the model replies in
# the same language). This is only the FALLBACK when the language is unclear.
DEFAULT_LANGUAGE = os.getenv("PRESENTER_LANGUAGE", "English").strip()

# Lower temperature = more deterministic, less drift/hallucination (Realtime min ~0.6).
TEMPERATURE = float(os.getenv("REALTIME_TEMPERATURE", "0.6"))

MAX_SLIDES = 6

TOOLS = [
    {
        "type": "function",
        "name": "go_to_slide",
        "description": "Switch the on-screen slide deck to a specific slide number.",
        "parameters": {
            "type": "object",
            "properties": {"slide_number": {"type": "integer", "description": "Slide to show (1-based)."}},
            "required": ["slide_number"],
        },
    },
    {
        "type": "function",
        "name": "next_slide",
        "description": "Advance to the next slide.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "previous_slide",
        "description": "Go back to the previous slide.",
        "parameters": {"type": "object", "properties": {}},
    },
]

# Used only if generation fails badly, so the app never dead-ends.
FALLBACK_DECK = {
    "title": "Could not generate this deck",
    "slides": [
        {
            "title": "Generation failed",
            "bullets": [
                "The AI couldn't produce a valid deck for that topic",
                "Try a clearer or more specific topic",
                "Then press Generate again",
            ],
            "note": "Sorry — let's try a different topic.",
        }
    ],
    "language": "English",
}


def azure_url() -> str:
    host = ENDPOINT.replace("https://", "").replace("http://", "").rstrip("/")
    return f"wss://{host}/openai/realtime?api-version={API_VERSION}&deployment={DEPLOYMENT}"


# ---------------------------------------------------------------------------
# Deck generation (Phase 9)
# ---------------------------------------------------------------------------
GEN_PROMPT = (
    "You are a slide-deck generator. Create a concise, accurate {n}-slide presentation "
    "on the topic: \"{topic}\".\n\n"
    "Return ONLY valid JSON (no markdown, no code fences, no commentary) with EXACTLY this shape:\n"
    '{{"language": "<language you wrote in, e.g. English or Hindi>", "title": "<deck title>", '
    '"slides": [{{"title": "<slide title>", '
    '"bullets": ["<point>", "<point>", "<point>"], "note": "<one spoken sentence>"}}]}}\n\n'
    "Rules: exactly {n} slides; write everything in the SAME language as the topic and set \"language\" to "
    "that language's English name; each slide has a short title, 2-4 concise bullets, and a one-sentence "
    "spoken 'note' the presenter would say. If the topic is unsafe, hateful, or inappropriate, instead "
    'return a single-slide deck politely declining (title "Can\'t present this").'
)


async def _ask_model_for_deck(topic: str, strict: bool = False) -> str:
    """Open a short-lived TEXT-only Azure connection and return the raw deck text."""
    prompt = GEN_PROMPT.format(topic=topic, n=MAX_SLIDES)
    if strict:
        prompt += "\n\nIMPORTANT: Output ONLY raw JSON. Start with '{' and end with '}'. No code fences."

    async with websockets.connect(
        azure_url(), additional_headers={"api-key": API_KEY}, max_size=None
    ) as ws:
        await ws.recv()  # session.created
        await ws.send(json.dumps({"type": "session.update", "session": {"modalities": ["text"]}}))
        await ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": prompt}]},
        }))
        await ws.send(json.dumps({"type": "response.create"}))

        text = ""
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=45))
            t = ev.get("type", "")
            if t in ("response.text.delta", "response.output_text.delta"):
                text += ev.get("delta", "")
            elif t == "response.done":
                break
            elif t == "error":
                print(f"[generate] azure error: {ev.get('error', ev)}")
                break
        return text


def _parse_deck(raw: str):
    """Best-effort JSON extraction: strip fences, take the outermost braces, parse."""
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("slides"), list) or not data["slides"]:
        return None
    return data


# Detect a deck's language from its text by Unicode script (covers non-Latin
# scripts reliably; Latin-script text falls back to the default language).
_SCRIPT_RANGES = [
    ("Hindi", 0x0900, 0x097F),
    ("Bengali", 0x0980, 0x09FF),
    ("Tamil", 0x0B80, 0x0BFF),
    ("Telugu", 0x0C00, 0x0C7F),
    ("Arabic", 0x0600, 0x06FF),
    ("Hebrew", 0x0590, 0x05FF),
    ("Russian", 0x0400, 0x04FF),
    ("Greek", 0x0370, 0x03FF),
    ("Thai", 0x0E00, 0x0E7F),
    ("Japanese", 0x3040, 0x30FF),
    ("Korean", 0xAC00, 0xD7AF),
    ("Chinese", 0x4E00, 0x9FFF),
]


def detect_language(text: str) -> str:
    for ch in text:
        cp = ord(ch)
        for name, lo, hi in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                return name
    return DEFAULT_LANGUAGE


def _deck_language(deck: dict, slides: list) -> str:
    """Prefer an explicit 'language', else detect from the slide text."""
    stated = str(deck.get("language", "")).strip()
    if stated:
        return stated
    sample = " ".join(
        f"{s.get('title', '')} {' '.join(s.get('bullets', []))}" for s in slides
    )
    return detect_language(sample)


def _normalize(deck: dict) -> dict:
    clean = []
    for s in deck.get("slides", [])[:MAX_SLIDES]:
        if not isinstance(s, dict):
            continue
        title = (str(s.get("title", "")).strip() or "Slide")
        bullets = [str(b).strip() for b in (s.get("bullets") or []) if str(b).strip()][:5]
        if not bullets:
            bullets = ["(no details provided)"]
        note = str(s.get("note", "")).strip()
        clean.append({"title": title, "bullets": bullets, "note": note})
    if not clean:
        return dict(FALLBACK_DECK)
    return {
        "title": (str(deck.get("title", "")).strip() or "Presentation"),
        "slides": clean,
        "language": _deck_language(deck, clean),
    }


def _normalize_uploaded(deck: dict, max_slides: int = 12) -> dict:
    """Normalize a deck from upload or reconnect (allows more slides than a
    generated deck; same shape/guarantees as _normalize)."""
    clean = []
    for s in deck.get("slides", [])[:max_slides]:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title", "")).strip() or "Slide"
        bullets = [str(b).strip() for b in (s.get("bullets") or []) if str(b).strip()][:6]
        if not bullets:
            bullets = ["(no details)"]
        note = str(s.get("note", "")).strip()
        clean.append({"title": title[:140], "bullets": bullets, "note": note[:300]})
    if not clean:
        return dict(FALLBACK_DECK)
    return {
        "title": (str(deck.get("title", "")).strip() or "Presentation"),
        "slides": clean,
        "language": _deck_language(deck, clean),
    }


async def generate_deck(topic: str) -> dict:
    """Generate + parse a deck, with one strict retry, else fallback."""
    for strict in (False, True):
        try:
            raw = await _ask_model_for_deck(topic, strict=strict)
        except Exception as e:
            print(f"[generate] attempt failed: {type(e).__name__}: {e}")
            continue
        deck = _parse_deck(raw)
        if deck:
            return _normalize(deck)
        print(f"[generate] unparseable response (strict={strict}), retrying…")
    return dict(FALLBACK_DECK)


def build_instructions(deck: dict) -> str:
    lang = deck.get("language") or DEFAULT_LANGUAGE
    lines = []
    for i, s in enumerate(deck["slides"], 1):
        note = f" ({s['note']})" if s["note"] else ""
        lines.append(f"{i}. {s['title']} — " + "; ".join(s["bullets"]) + note)
    return (
        f"You are an AI presenter for a {len(deck['slides'])}-slide deck titled "
        f"\"{deck['title']}\".\n\nHere is the full deck:\n\n" + "\n".join(lines) + "\n\n"
        "Rules:\n"
        f"- This deck's language is {lang}. When PRESENTING/narrating slides, ALWAYS use {lang} and "
        f"NEVER switch languages between slides.\n"
        f"- When the USER asks a question, reply in the same language the user used (default {lang}).\n"
        "- Answer ONLY using the slides above. If the answer is not in the deck, say you don't have that "
        "detail — never invent facts, numbers, or names.\n"
        "- If asked something unrelated to this deck, briefly say it's outside this presentation.\n"
        "- When the user asks about a topic, FIRST call `go_to_slide` with the best-matching slide "
        "number, THEN answer in 1-3 short, spoken-style sentences.\n"
        "- Use `next_slide` / `previous_slide` when the user says 'next' or 'go back'.\n"
        "- Keep answers concise."
    )


def make_session(deck: dict) -> dict:
    """The audio presentation session config, built from a generated deck."""
    return {
        "modalities": ["audio", "text"],
        "voice": VOICE,
        "output_audio_format": "pcm16",
        "input_audio_format": "pcm16",
        "turn_detection": {
            "type": "server_vad",
            "threshold": 0.5,
            "prefix_padding_ms": 300,
            "silence_duration_ms": 500,
            "create_response": True,
            "interrupt_response": True,
        },
        # No fixed language -> Whisper detects it, so voice works in any language.
        "input_audio_transcription": {"model": "whisper-1"},
        "temperature": TEMPERATURE,
        "instructions": build_instructions(deck),
        "tools": TOOLS,
        "tool_choice": "auto",
        # Token/cost control: cap every spoken response so answers + narration
        # stay short (audio output tokens are the expensive part).
        "max_response_output_tokens": 400,
    }


def friendly_error(err: dict):
    """Map an Azure error event to a (user-facing message, level) pair.
    level: 'retry' (transient), 'reconnect', or 'info'."""
    text = f"{err.get('code') or err.get('type') or ''} {err.get('message', '')}".lower()
    if any(k in text for k in ("rate", "429", "quota", "throttl")):
        return ("The AI service is busy right now — please try again in a moment.", "retry")
    if "content" in text and "filter" in text or "safety" in text:
        return ("I can't respond to that one — try a different question.", "info")
    if any(k in text for k in ("context_length", "too long", "maximum context", "token")):
        return ("That got too long — try a shorter question.", "info")
    if "session" in text and any(k in text for k in ("expired", "timeout", "duration", "closed")):
        return ("Session expired — reconnecting…", "reconnect")
    if any(k in text for k in ("auth", "api key", "401", "403", "unauthorized")):
        return ("There's an authentication problem with the AI service.", "info")
    return (err.get("message") or "Something went wrong — please try again.", "info")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="ML Voice Agent")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB cap for .pptx uploads


@app.websocket("/ws")
async def ws_endpoint(browser: WebSocket):
    await browser.accept()
    print("[browser] connected")

    try:
        azure = await websockets.connect(
            azure_url(), additional_headers={"api-key": API_KEY}, max_size=None
        )
        first = json.loads(await azure.recv())
        print(f"[azure] {first.get('type')}")
    except Exception as e:
        print(f"[azure] connection failed: {type(e).__name__}: {e}")
        await browser.send_json({"type": "status", "msg": f"Azure FAILED: {type(e).__name__}"})
        return

    state = {
        "slide": 1,
        "total": MAX_SLIDES,
        "ready": False,
        "lang": DEFAULT_LANGUAGE,   # the deck's pinned narration language
        "response_active": False,   # is a model response currently generating?
        "present_active": False,    # is Present Mode running?
        "present_index": 1,         # slide currently being narrated
        "present_paused": False,    # manually paused?
    }
    mic_frames = [0]
    azure_lock = asyncio.Lock()

    async def to_azure(event: dict):
        async with azure_lock:
            await azure.send(json.dumps(event))

    await browser.send_json({"type": "status", "msg": "enter a topic to begin"})

    async def apply_tool(name: str, args: dict) -> dict:
        if name == "go_to_slide":
            n = int(args.get("slide_number", state["slide"]))
        elif name == "next_slide":
            n = state["slide"] + 1
        elif name == "previous_slide":
            n = state["slide"] - 1
        else:
            return {"ok": False, "error": f"unknown tool {name}"}
        n = max(1, min(state["total"], n))
        state["slide"] = n
        await browser.send_json({"type": "slide", "n": n})
        print(f"[tool] {name}{args or ''} -> slide {n}")
        return {"ok": True, "slide": n}

    async def narrate(i: int):
        """Present Mode: show slide i and ask the model to speak about it."""
        if not state["present_active"]:
            return
        i = max(1, min(state["total"], i))
        state["present_index"] = i
        state["slide"] = i
        await browser.send_json({"type": "slide", "n": i})
        await browser.send_json({"type": "presenting", "index": i, "total": state["total"]})
        await to_azure({
            "type": "conversation.item.create",
            "item": {"type": "message", "role": "user", "content": [{"type": "input_text",
                     "text": f"Present slide {i} of the deck now, speaking ONLY in {state['lang']} "
                             "(do not switch languages). Give 2-4 short, engaging spoken sentences about "
                             "it. Do not call any tools and do not say the slide number."}]},
        })
        await to_azure({"type": "response.create"})
        print(f"[present] narrating slide {i}/{state['total']}")

    async def apply_deck(deck: dict, source: str):
        """Configure the presentation session with a deck (from generate OR upload
        OR a reconnect) and hand it to the browser to render."""
        state["total"] = len(deck["slides"])
        state["slide"] = 1
        state["lang"] = deck.get("language") or DEFAULT_LANGUAGE
        state["present_active"] = False
        state["present_paused"] = False
        await to_azure({"type": "session.update", "session": make_session(deck)})
        state["ready"] = True
        await browser.send_json({
            "type": "deck", "title": deck["title"],
            "slides": deck["slides"], "language": state["lang"],
        })
        await browser.send_json({"type": "status", "msg": "ready — ask a question or click Present"})
        print(f"[{source}] deck ready: {deck['title']!r} ({state['total']} slides, lang={state['lang']})")

    async def browser_pump():
        while True:
            data = json.loads(await browser.receive_text())
            kind = data.get("type")

            if kind == "generate":
                topic = (data.get("topic") or "").strip()
                if len(topic) < 2:
                    await browser.send_json({"type": "generation_error", "msg": "Please enter a topic."})
                    continue
                print(f"[generate] topic: {topic!r}")
                await browser.send_json({"type": "status", "msg": f"generating a deck on “{topic}”…"})
                await apply_deck(await generate_deck(topic), "generate")

            elif kind == "use_deck":
                # An uploaded .pptx (parsed via /upload) or a reconnect re-applying
                # the browser's cached deck.
                slides = data.get("slides") or []
                if not slides:
                    await browser.send_json({"type": "generation_error", "msg": "That deck had no slides."})
                    continue
                deck = _normalize_uploaded({
                    "title": data.get("title", "Presentation"),
                    "slides": slides,
                    "language": data.get("language", ""),
                })
                await apply_deck(deck, "use_deck")

            elif kind == "user_text":
                if not state["ready"]:
                    continue
                text = (data.get("text") or "").strip()
                if not text:
                    continue
                print(f"[browser -> azure] (text) {text}")
                await to_azure({
                    "type": "conversation.item.create",
                    "item": {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": text}]},
                })
                await to_azure({"type": "response.create"})

            elif kind == "audio_in":
                if not state["ready"]:
                    continue
                audio_b64 = data.get("audio", "")
                mic_frames[0] += 1
                if mic_frames[0] == 1 or mic_frames[0] % 40 == 0:
                    print(f"[mic -> azure] frame #{mic_frames[0]} ({len(audio_b64)} b64 chars)")
                await to_azure({"type": "input_audio_buffer.append", "audio": audio_b64})

            # ---- Present Mode (Phase 10) ----
            elif kind == "present_start":
                if not state["ready"] or state["present_active"]:
                    continue
                state["present_active"] = True
                state["present_paused"] = False
                print("[present] start")
                await narrate(1)

            elif kind == "present_pause":
                if not state["present_active"]:
                    continue
                state["present_paused"] = True
                if state["response_active"]:
                    await to_azure({"type": "response.cancel"})  # stop generating (saves tokens)
                print("[present] paused")

            elif kind == "present_resume":
                if not state["present_active"]:
                    continue
                state["present_paused"] = False
                print("[present] resume")
                await narrate(state["present_index"])  # re-narrate current slide (no skip)

            elif kind == "present_stop":
                if not state["present_active"]:
                    continue
                state["present_active"] = False
                state["present_paused"] = False
                if state["response_active"]:
                    await to_azure({"type": "response.cancel"})
                print("[present] stop")
                await browser.send_json({"type": "present_done"})

            elif kind == "playback_idle":
                # Browser finished playing the current segment -> advance the pitch.
                if not state["present_active"] or state["present_paused"]:
                    continue
                if state["present_index"] < state["total"]:
                    await narrate(state["present_index"] + 1)
                else:
                    state["present_active"] = False
                    print("[present] complete")
                    await browser.send_json({"type": "present_done"})
                    await browser.send_json({"type": "status", "msg": "presentation complete — ask anything"})

    async def azure_pump():
        pending = []
        async for raw in azure:
            event = json.loads(raw)
            t = event.get("type", "")

            if t == "response.created":
                state["response_active"] = True

            elif t == "input_audio_buffer.speech_started":
                print("[azure] speech_started — VAD hears the user")
                await browser.send_json({"type": "user_speaking"})

            elif t == "input_audio_buffer.speech_stopped":
                print("[azure] speech_stopped — end of turn")
                await browser.send_json({"type": "user_stopped"})

            elif t == "conversation.item.input_audio_transcription.completed":
                transcript = event.get("transcript", "")
                print(f"[azure] you said: {transcript!r}")
                await browser.send_json({"type": "user_transcript", "text": transcript})

            elif t == "conversation.item.input_audio_transcription.failed":
                print(f"[azure] transcription FAILED: {event.get('error', event)}")
                await browser.send_json({
                    "type": "error", "level": "info",
                    "msg": "Didn't catch that — please try again.",
                })

            elif t == "response.audio.delta":
                await browser.send_json({"type": "audio_delta", "audio": event.get("delta", "")})

            elif t in ("response.audio_transcript.delta", "response.text.delta", "response.output_text.delta"):
                await browser.send_json({"type": "answer_delta", "delta": event.get("delta", "")})

            elif t == "response.function_call_arguments.done":
                name = event.get("name", "")
                try:
                    args = json.loads(event.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await apply_tool(name, args)
                pending.append({"call_id": event.get("call_id"), "output": result})

            elif t == "response.done":
                state["response_active"] = False
                if pending:
                    for call in pending:
                        await to_azure({
                            "type": "conversation.item.create",
                            "item": {"type": "function_call_output",
                                     "call_id": call["call_id"], "output": json.dumps(call["output"])},
                        })
                    pending = []
                    await to_azure({"type": "response.create"})
                else:
                    await browser.send_json({"type": "answer_done"})

            elif t == "error":
                err = event.get("error", event) or {}
                msg, level = friendly_error(err)
                print(f"[azure error] code={err.get('code')} type={err.get('type')} -> {msg!r}")
                # Recover: clear in-flight state so the app isn't stuck.
                state["response_active"] = False
                pending.clear()
                if state["present_active"]:
                    state["present_active"] = False
                    await browser.send_json({"type": "present_done"})
                await browser.send_json({"type": "error", "level": level, "msg": msg})

    try:
        await asyncio.gather(browser_pump(), azure_pump())
    except WebSocketDisconnect:
        print("[browser] disconnected")
    except websockets.ConnectionClosed:
        print("[azure] connection closed")
    finally:
        await azure.close()


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """Parse an uploaded .pptx into our deck structure. The browser then sends the
    result back over the WebSocket as `use_deck` to configure the session."""
    if not (file.filename or "").lower().endswith(".pptx"):
        return JSONResponse({"error": "Please upload a .pptx file."}, status_code=400)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse({"error": "That file is too large (max 20 MB)."}, status_code=400)
    try:
        deck = extract_deck(data)
    except Exception as e:
        print(f"[upload] parse failed: {type(e).__name__}: {e}")
        return JSONResponse({"error": f"Couldn't read that .pptx ({type(e).__name__})."}, status_code=400)
    if not deck["slides"]:
        return JSONResponse({"error": "No text slides found in that file."}, status_code=400)
    print(f"[upload] {file.filename!r} -> {len(deck['slides'])} slides (truncated={deck['truncated']})")
    return deck


# Serve the frontend at "/". Mounted LAST so it doesn't shadow /ws.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")   # set HOST=0.0.0.0 in containers
    port = int(os.getenv("PORT", "8000"))   # most PaaS hosts inject PORT
    print(f"Serving {FRONTEND_DIR}  ->  http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
