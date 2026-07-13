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
from pathlib import Path

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

# Force a single language so the model never drifts to another one (e.g. when
# Whisper mis-detects accented English). Override via env if you want another.
LANG_NAME = os.getenv("PRESENTER_LANGUAGE", "English").strip()
LANG_CODE = os.getenv("PRESENTER_LANGUAGE_CODE", "en").strip()

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
    '{{"title": "<deck title>", "slides": [{{"title": "<slide title>", '
    '"bullets": ["<point>", "<point>", "<point>"], "note": "<one spoken sentence>"}}]}}\n\n'
    "Rules: exactly {n} slides; write everything in {lang}; each slide has a short title, 2-4 concise bullets, and a one-sentence "
    "spoken 'note' the presenter would say. If the topic is unsafe, hateful, or inappropriate, instead "
    'return a single-slide deck politely declining (title "Can\'t present this").'
)


async def _ask_model_for_deck(topic: str, strict: bool = False) -> str:
    """Open a short-lived TEXT-only Azure connection and return the raw deck text."""
    prompt = GEN_PROMPT.format(topic=topic, n=MAX_SLIDES, lang=LANG_NAME)
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
    return {"title": (str(deck.get("title", "")).strip() or "Presentation"), "slides": clean}


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
    return {"title": (str(deck.get("title", "")).strip() or "Presentation"), "slides": clean}


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
    lines = []
    for i, s in enumerate(deck["slides"], 1):
        note = f" ({s['note']})" if s["note"] else ""
        lines.append(f"{i}. {s['title']} — " + "; ".join(s["bullets"]) + note)
    return (
        f"You are an AI presenter for a {len(deck['slides'])}-slide deck titled "
        f"\"{deck['title']}\".\n\nHere is the full deck:\n\n" + "\n".join(lines) + "\n\n"
        "Rules:\n"
        f"- ALWAYS speak and reply in {LANG_NAME}, no matter what language the question is in.\n"
        "- When the user asks about a topic, FIRST call `go_to_slide` with the best-matching slide "
        "number, THEN answer in 1-3 short, spoken-style sentences.\n"
        "- Use `next_slide` / `previous_slide` when the user says 'next' or 'go back'.\n"
        "- Stay on this deck's topic. Keep answers concise."
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
        "input_audio_transcription": {"model": "whisper-1", "language": LANG_CODE},
        "instructions": build_instructions(deck),
        "tools": TOOLS,
        "tool_choice": "auto",
        # Token/cost control: cap every spoken response so answers + narration
        # stay short (audio output tokens are the expensive part).
        "max_response_output_tokens": 400,
    }


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="ML Voice Agent")


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
                     "text": f"Present slide {i} of the deck now. Give 2-4 short, engaging spoken "
                             f"sentences in {LANG_NAME} about it. Do not call any tools and do not say "
                             "the slide number."}]},
        })
        await to_azure({"type": "response.create"})
        print(f"[present] narrating slide {i}/{state['total']}")

    async def apply_deck(deck: dict, source: str):
        """Configure the presentation session with a deck (from generate OR upload
        OR a reconnect) and hand it to the browser to render."""
        state["total"] = len(deck["slides"])
        state["slide"] = 1
        state["present_active"] = False
        state["present_paused"] = False
        await to_azure({"type": "session.update", "session": make_session(deck)})
        state["ready"] = True
        await browser.send_json({"type": "deck", "title": deck["title"], "slides": deck["slides"]})
        await browser.send_json({"type": "status", "msg": "ready — ask a question or click Present"})
        print(f"[{source}] deck ready: {deck['title']!r} ({state['total']} slides)")

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
                deck = _normalize_uploaded({"title": data.get("title", "Presentation"), "slides": slides})
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
                err = event.get("error", event)
                print(f"[azure error] {err}")
                await browser.send_json({"type": "status", "msg": f"error: {err.get('message', err)}"})

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
