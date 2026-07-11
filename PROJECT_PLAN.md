# ML Voice Agent — Project Plan

An AI voice application that presents 5–6 slides, automatically changes slides based on the
user's spoken question, speaks its answers, and can be interrupted mid-sentence.

The **Basics of Machine Learning** deck (Phases 1–8) is the seed prototype and proves every
required feature. **Phases 9–11 extend it** so the user can enter **any topic**, have the AI
**generate the deck at runtime**, and have it **auto-presented** by voice — with interruption.

> **Architecture update (Phase 1 done):** the "brain" is now **Azure OpenAI `gpt-realtime`**
> (the Realtime speech-to-speech API) instead of Anthropic Claude. `gpt-realtime` listens,
> reasons, calls tools, **and** speaks — all over a single websocket — and handles
> interruption (barge-in) natively. This removes most of the hand-wired STT→LLM→TTS plumbing.

## The Stack

| Component | Choice | Role |
|---|---|---|
| Ears + Brain + Mouth | **Azure OpenAI `gpt-realtime`** (Realtime API) | Transcribes your voice, reasons, decides which slide (tool call), and speaks the answer — all in one model |
| Slide control | **Realtime tool calling** | The model calls `go_to_slide(n)` while it answers |
| Interruption (barge-in) | **Realtime server-side VAD** | Built into the Realtime API — talk over it and it stops |
| Backend | **Python + FastAPI + WebSockets** | Relays audio between the browser and Azure; forwards slide events |
| Frontend | **HTML/CSS/JS** | Shows slides, captures mic, plays the model's audio |
| Deepgram (optional) | **Deepgram nova-3** | *Optional* extra STT for an on-screen transcript. Not required — `gpt-realtime` transcribes on its own. |
| Cartesia | **removed** | No longer needed — `gpt-realtime` generates the speech |

## The Big Picture

```
🎤 Mic ─► Browser ─(audio)─► FastAPI ─(audio)─► Azure gpt-realtime ─(audio)─► FastAPI ─► Browser ─► 🔊 Speaker
                                                        │
                                                        ├─ transcribes your speech (STT built in)
                                                        ├─ reasons about the answer
                                                        ├─ speaks the answer (TTS built in)
                                                        └─ tool call: go_to_slide(4) ─► FastAPI ─► frontend changes the slide
```

Two hard requirements, two mechanisms:
- **Auto slide change** = Realtime **tool calling**. The model calls `go_to_slide(n)` while it answers.
- **Interruption (barge-in)** = Realtime **server-side VAD**. When the mic hears you while the model
  talks, the Realtime API fires a `speech_started` / cancellation event; we stop playback and it listens.

---

## Phase 0 — Mindset (read once)

You are building an **agent**: a loop where a model decides actions (which slide, what to say) using
**tools** you define. The single most important concept to master is **tool calling** — here it is a
`gpt-realtime` *function*. Everything else (audio streaming, WebSockets) is plumbing around it.
Build and test the plumbing one piece at a time — never wire all of it up at once.

The Realtime API talks in **JSON events over a websocket** in both directions. You *send* events like
`session.update`, `conversation.item.create`, `response.create`, and audio frames; you *receive* events
like `response.audio.delta`, `response.audio_transcript.delta`, `response.function_call_arguments.done`,
and `input_audio_buffer.speech_started`. Learning these event names IS learning this project.

---

## Phase 1 — Setup & Prerequisites ✅ (done)
**Goal:** a working Python environment and the API keys.

- [x] 1.1 Install **Python 3.11+** — `python --version` (this box: 3.14.3)
- [x] 1.2 Install **VS Code**
- [x] 1.3 Create the project virtual environment (`.venv`)
- [x] 1.4 Get an **Azure OpenAI** resource with a **`gpt-realtime`** deployment
- [x] 1.5 Paste the real **`AZURE_OPENAI_API_KEY`** into `.env` (endpoint/deployment/version already filled)
- [x] 1.6 (optional) **Deepgram API key** — only if you want a second transcript source later
- [x] 1.7 `.env` created from `.env.example`
- [x] 1.8 `pip install -r requirements.txt`

**Done when:** the Phase 2 connection test (below) prints a reply from `gpt-realtime`.

---

## Phase 2 — Prove the Realtime connection (one standalone script)  ⭐ do this next
**Goal:** confirm your Azure `gpt-realtime` deployment works — text in, text + tool-call out — before
touching any audio, browser, or server code. Lives in `scripts/`.

- [x] 2.1 `scripts/realtime_test.py` — open the websocket, send a typed question, print the streamed
      **text** answer. (We use `modalities: ["text"]` first — no audio yet — to isolate the connection.)
- [x] 2.2 In the same script, register a `go_to_slide` tool and ask a question that should trigger it;
      confirm a `response.function_call_arguments.done` event arrives with `{"slide_number": ...}`.
      ✅ Verified 2026-07-11: model called `go_to_slide(1)` and `go_to_slide(4)` correctly + gave a text answer.

**Done when:** the script prints a text answer AND shows the model calling `go_to_slide`.
**You'll learn:** the Realtime websocket handshake, the core JSON events, and tool calling — the heart
of the agent. Everything after this is plumbing.

---

## Phase 3 — The slide deck (frontend)
**Goal:** a viewable 6-slide deck on ML with next/prev.

- [x] 3.1 Write slide content as structured data (`frontend/slides.js`):
      1. What is Machine Learning?
      2. Types of ML (supervised / unsupervised / reinforcement)
      3. How a model learns (training data → model)
      4. Common algorithms (regression, trees, neural nets)
      5. Real-world use cases
      6. Limitations & ethics
- [x] 3.2 `frontend/index.html` + `style.css` — render the current slide
- [x] 3.3 `next` / `prev` buttons + keyboard arrows (+ clickable progress dots)

**Done when:** you can click through all 6 slides in a browser.

---

## Phase 4 — Backend skeleton (FastAPI + WebSocket)
**Goal:** a server the frontend can talk to in real time, that also holds the Azure connection.

- [x] 4.1 `backend/main.py` — FastAPI app that serves the frontend
- [x] 4.2 A `/ws` WebSocket endpoint (browser ↔ backend) that echoes messages (test the pipe)
- [x] 4.3 On a browser connection, the backend opens its **own** websocket to Azure `gpt-realtime`
      and logs `session.created`
- [x] 4.4 Frontend connects to `/ws` on load and logs messages (shows a live status badge)

**Done when:** the browser and server exchange messages, and the server logs a live Azure session.
**✅ Verified 2026-07-11:** `http://127.0.0.1:8000` serves the deck; `/ws` echoes; backend reports
`Azure gpt-realtime reachable (session.created)`. Run with `.venv\Scripts\python.exe backend\main.py`.
**You'll learn:** the backend is a **relay** — browser audio in one side, Azure on the other, slide
events split back out to the browser.

---

## Phase 5 — The presenter: tool calling → slides (typed first)  ⭐ core phase
**Goal:** type a question, `gpt-realtime` answers (text) AND changes the slide automatically.

- [x] 5.1 System instructions (`session.update`): give the model the full text of all 6 slides +
      "you are presenting these; call `go_to_slide` before answering about a slide's topic"
- [x] 5.2 Define tools in the session: `go_to_slide(slide_number)`, `next_slide()`, `previous_slide()`
- [x] 5.3 Backend: forward the typed question as a `conversation.item.create` + `response.create`
- [x] 5.4 When a `function_call` for a slide arrives, send a `{"type":"slide","n":4}` event to the browser
      (and return a `function_call_output` to the model so it keeps talking)
- [x] 5.5 Frontend: on that event, switch to that slide
- [x] 5.6 Stream the model's answer **text** back and show it on screen

**Done when:** you type "what algorithms are there?" and slide 4 appears + an answer shows.
**This is the heart of the app — get it rock solid before adding audio.**
**✅ Verified 2026-07-11:** "what algorithms?" → slide 4 + answer; "ethics" → slide 6 + answer;
"next slide" on slide 6 → model knew it was the last. Backend tracks current slide for next/previous.

---

## Phase 6 — Give it a voice (audio OUT)
**Goal:** the model **speaks** its answers.

- [x] 6.1 Switch the session to `modalities: ["audio","text"]` and pick a voice (`VOICE`, default `alloy`)
- [x] 6.2 Backend forwards `response.audio.delta` (base64 PCM16) frames to the browser
- [x] 6.3 Frontend decodes and plays the PCM audio (Web Audio API — `frontend/audio.js`, gapless scheduling)
- [x] 6.4 Show `response.audio_transcript.delta` as live captions

**Done when:** typed questions produce a **spoken** answer + the slide change.
**You'll learn:** streaming 24 kHz PCM16 audio to the browser and playing it gaplessly.
**✅ Verified 2026-07-11:** "what is machine learning?" → slide 1 + ~9.8s of streamed speech + matching caption.

---

## Phase 7 — Give it ears (audio IN)
**Goal:** talk to it instead of typing.

- [x] 7.1 Capture microphone audio in the browser (getUserMedia → 24 kHz PCM16) — `frontend/mic.js`
- [x] 7.2 Stream mic frames to the backend over `/ws` (`audio_in` messages)
- [x] 7.3 Backend forwards them as `input_audio_buffer.append` to Azure; server VAD auto-responds
- [x] 7.4 `gpt-realtime` transcribes + answers — the full voice loop closes (whisper-1 shows "You said …")
- [ ] 7.5 (optional) also send the mic audio to **Deepgram** for a second on-screen transcript

**Done when:** you *speak* a question and get a spoken answer + slide change.
**Verified server-side 2026-07-11:** VAD/transcription session config accepted; `audio_in` relay path live.
**⚠️ Needs your live mic test in the browser** (click 🎤 Talk, allow mic, speak a question).

---

## Phase 8 — Interruption (barge-in)
**Goal:** you can cut the AI off mid-sentence and it listens.

- [x] 8.1 Enable server-side VAD in `session.update` (`turn_detection: {type: "server_vad"}`)
- [x] 8.2 On an `input_audio_buffer.speech_started` event, **stop** browser audio playback immediately
- [x] 8.3 Abort the in-flight answer — `interrupt_response: true` makes Azure auto-cancel its response
- [x] 8.4 Flush the browser's audio queue (`pcmPlayer.stop()`) and go back to listening
- [ ] 8.5 Test: interrupt mid-answer and ask a new question  ⬅ needs your live mic test

**Done when:** you can talk over the AI and it stops and responds to the new question.
**Note:** the Realtime API does the VAD + turn detection for you — this is why we pivoted to it.
**🎧 Use headphones for the test** — on open speakers the mic can hear the AI and self-interrupt.
A "mute mic while AI speaks" fallback is a Phase 9 option if headphones aren't available.

---

## Phase 9 — Dynamic topic → AI-generated deck (+ topic screen & hero image)  ⭐ new ask
**Goal:** the user types **any topic**, and the AI generates a fresh 5–6 slide deck on it at
runtime, then that deck drives everything (Q&A, voice, present mode).

- [x] 9.1 **Topic screen** (`frontend`): a landing view with an **aesthetic animated AI hero image**
      (self-contained inline SVG — glowing "AI orb" with orbiting rings/particles, theme-aware), a topic
      text box, and a "Generate & Present" button. Hidden once a deck is loaded. (`index.html`, `app.js`)
- [x] 9.2 Backend `generate` WS message: sends the topic to `gpt-realtime` (text mode, ephemeral socket)
      with a strict instruction to return **JSON**: `{ "title", "slides":[{title,bullets[],note}] }`
- [x] 9.3 **Robust parsing**: strip ```` ```json ```` fences, take outermost braces, `json.loads` in
      try/except, validate schema, normalize to ≤6 slides, one strict auto-retry, else a safe fallback deck
- [x] 9.4 Deck is **dynamic everywhere**: per-connection `total`, instructions rebuilt from the deck,
      frontend renders the new slides, `go_to_slide` clamped to the deck size
- [x] 9.5 Input guards: empty/too-short topic rejected client- and server-side; model refuses unsafe topics
- [x] 9.6 Loading + error UX: "Generating…" state, `generation_error` → message + Retry, `↺ New topic` button

**Done when:** you type "The Solar System", hit generate, and get a 6-slide deck you can ask questions about.
**Edge cases handled:** bad JSON → retry/fallback; too many slides → clamp; unsafe topic → polite refusal;
slow/timeout → 45s read timeout → fallback; out-of-range slide → dynamic per-deck `total`.
**✅ Verified 2026-07-11:** generated a 6-slide "Solar System" deck; "largest planet?" → slide 4 + spoken answer.

---

## Phase 10 — Present Mode (auto-pitch the whole deck) with interrupt & resume
**Goal:** the AI **presents the deck start-to-finish on its own** — narrating each slide and advancing —
and you can interrupt to ask a question, then it resumes.

- [x] 10.1 A "Present" button + `present_*` control messages; backend tracks `present_index`
- [x] 10.2 For each slide in order: show slide `i` → instruct the model to narrate it (concise, spoken) →
      the **browser paces** advance via `playback_idle` (fires when that segment's audio finishes) → next
- [x] 10.3 **Interrupt handling:** voice barge-in (Phase 8) stops narration + cancels the pending advance;
      the question is answered; the Q&A's own `playback_idle` then continues the pitch
- [x] 10.4 Controls: Pause / Resume (re-narrates current slide, no skip) / Stop; "presenting slide i/n" indicator
- [x] 10.5 Edge cases: reaches last slide → `present_done`; jumps resync on next narration; Pause cancels
      the in-flight response (`response.cancel`) to save tokens; `go_to_slide` clamped to deck size

**Done when:** you pick a topic, click Present, and the AI pitches all slides by voice — and you can cut
in with a question and have it carry on.
**✅ Verified 2026-07-11 (protocol):** auto-advance 1→6 + present_done; pause holds; resume re-narrates then
advances; stop ends cleanly. (Voice barge-in during a pitch needs a working mic to demo.)
**Token control:** `max_response_output_tokens: 400` caps every response; narration references slides by
number (not re-sent content); Pause cancels in-flight generation.

---

## Phase 11 — Polish & demo
**Goal:** a solid, demo-able prototype.

- [x] 11.1 On-screen indicators: "listening" / "speaking" / "presenting slide i/n" / live transcript
- [x] 11.2 WebSocket **auto-reconnect** with backoff; on reconnect the browser re-sends its cached deck
      (`use_deck`) to reconfigure the session — survives the Realtime **session duration limit**
- [x] 11.3 Edge cases handled (out-of-scope questions via instructions; empty topic/deck guards)
- [x] 11.4 **Mic-mute-while-speaking** checkbox for users without headphones (avoids self-interrupt)
- [x] 11.5 **Upload a `.pptx`** as well as generate: `POST /upload` → `python-pptx` extracts text + notes
      (`backend/pptx_extract.py`) → `use_deck` configures the session (same structure as generated decks)
- [x] 11.6 Deployment-ready: env-driven `HOST`/`PORT`, `Dockerfile`, `.dockerignore`; final README + deploy guide

**Done when:** a stranger can enter a topic (or upload a deck), have it presented by voice, interrupt it,
ask questions, and watch slides change.
**✅ Verified 2026-07-11:** uploaded a 4-slide .pptx → parsed → "what were the results?" → slide 4 + spoken
answer; generation + Present Mode paths unchanged. Reconnect reuses the verified `use_deck` path.

---

## Phase 12 — Deployment (Render, and what a plain VM needs)  🚀

### First, the two things people get confused about

**1. Where does the frontend go?**
Nowhere separate. **The backend serves the frontend.** In `backend/main.py` the last line mounts the
`frontend/` folder as static files at `/`:
```python
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
```
So one web service serves *everything* from one URL: the page (`/`, `app.js`, `style.css`, …), the
WebSocket (`/ws`), and the upload endpoint (`/upload`). There is **no** separate frontend host, no CDN,
no CORS to configure.

**2. What does the Dockerfile do?**
It packages the whole app into one image Render runs:
- starts from `python:3.12-slim`,
- `pip install -r requirements.txt`,
- **copies `backend/` AND `frontend/`** into the image,
- sets `HOST=0.0.0.0` (listen on all interfaces) — Render injects `PORT` at runtime, which
  `main.py` already reads,
- runs `python -u backend/main.py`.
Render builds this image **on its servers** — you do **not** need Docker running locally.

**Two hard requirements Render satisfies automatically:** WebSockets ✅ and HTTPS ✅ (the mic needs a
secure context; it is blocked on non-`localhost` `http://`).

### Deploy to Render — every step

- [ ] 12.1 **Make it a git repo & push to GitHub** (Render deploys from a repo). From the project folder:
  ```bash
  git init
  git add .
  git commit -m "AI Voice Presenter"
  # create an EMPTY repo on github.com (no README), then:
  git remote add origin https://github.com/<you>/ai-voice-presenter.git
  git branch -M main
  git push -u origin main
  ```
  `.gitignore` already excludes `.env` and `.venv/`, so **your key is not pushed** — good.
- [ ] 12.2 **Create the service on Render.** Two ways:
  - **Blueprint (easiest):** Render dashboard → **New +** → **Blueprint** → pick your repo. Render reads
    `render.yaml`, creates the service, and prompts for the two secret env vars.
  - **Manual:** **New +** → **Web Service** → connect the repo → Render detects the **Dockerfile**
    (Language = *Docker*) → choose a plan → **Create Web Service**.
- [ ] 12.3 **Set environment variables** (dashboard → the service → **Environment**):
  `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT_NAME=gpt-realtime`,
  `AZURE_OPENAI_API_VERSION=2025-04-01-preview`, `HOST=0.0.0.0` (optional `REALTIME_VOICE`).
  Do **not** set `PORT` — Render injects it.
- [ ] 12.4 **Deploy.** Render builds the image and starts it; you get an `https://<name>.onrender.com` URL.
- [ ] 12.5 **Verify:** open the URL → enter a topic → generate → ask a question. On a device with a mic,
  click 🎤 Talk (HTTPS makes the mic work).
- [ ] 12.6 **Plan note:** the **Free** plan sleeps after ~15 min idle (first hit cold-starts ~30 s and a
  sleeping WebSocket won't stay open); **Starter** ($7/mo) stays warm — better for a live demo.

**Done when:** the Render URL presents by voice, changes slides from questions, and can be interrupted.

### What a plain VM needs (if you ever go that route)

You'd be responsible for the things Render does for you. Checklist:

- [ ] 12.7 **A VM** (e.g. Ubuntu) with a **public IP**, and its firewall/security-group opening **ports 80
      and 443**.
- [ ] 12.8 **A domain name** pointing (A record) at the VM's IP — **required for HTTPS**, which is
      **required for the mic**. (A bare IP can't easily get a trusted TLS cert.)
- [ ] 12.9 **Docker** installed (`curl -fsSL https://get.docker.com | sh`) — or Python 3.11+ + venv.
- [ ] 12.10 **Run the app:**
  ```bash
  docker build -t ai-voice-presenter .
  docker run -d --restart unless-stopped -p 8000:8000 --env-file .env ai-voice-presenter
  ```
- [ ] 12.11 **A reverse proxy that terminates TLS and forwards WebSockets.** Easiest is **Caddy**
      (automatic HTTPS). A whole `Caddyfile` is just:
  ```
  your-domain.com {
      reverse_proxy 127.0.0.1:8000
  }
  ```
  Caddy fetches a Let's Encrypt cert automatically and proxies WebSockets with no extra config.
  (With **nginx** you'd instead add a config with the `Upgrade`/`Connection` headers — see README — and run
  `certbot` for the cert.)
- [ ] 12.12 **Secrets** in `.env` on the box (or systemd env), never in git; **keep it running** via the
      Docker `--restart` policy (or a `systemd` unit).

**In one sentence:** a VM = you provide the public IP, domain, TLS cert, reverse proxy, and process
supervision yourself; Render bundles all of that into "connect repo → set env vars → deploy."

**Files added for deploy:** `Dockerfile`, `.dockerignore`, `render.yaml`.

---

## Golden rules for a first agentic project
1. **One piece at a time.** Never debug audio + model + slides together — isolate.
2. **Test the brain (Phase 2 & 5) with typing before adding audio.** Text is easy to debug; audio is not.
3. **Commit to git after each phase** so you can always go back.
4. **Keep secrets in `.env`** — never paste keys into code or share them.
5. **Learn the event names.** The whole Realtime API is JSON events over a websocket — print the ones
   you don't handle yet so you always know what the server is saying.
