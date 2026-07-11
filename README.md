# AI Voice Presenter

An AI voice application that **presents slides and answers questions by voice**. Give it a
**topic** and it generates a 5–6 slide deck on the spot — or **upload your own `.pptx`**. Then it
can **auto-present** the whole deck out loud, **change slides automatically** from your questions,
and be **interrupted** mid-sentence.

Built on **Azure OpenAI `gpt-realtime`** — one Realtime (speech-to-speech) model that listens,
reasons, calls tools (to drive the slides), and speaks, with native interruption.

## Features

- 🎛️ **Two ways to get a deck** — type any **topic** (AI generates it) or **upload a `.pptx`**.
- 🗣️ **Talk or type** your questions — it jumps to the right slide and answers by voice.
- ▶️ **Present Mode** — the AI pitches the whole deck itself, slide by slide, and you can cut in.
- ✋ **Interruption (barge-in)** — start talking and it stops immediately and listens.
- 🔌 **Resilient** — auto-reconnects and re-applies your deck if the Realtime session drops.
- 🪶 **Lightweight** — FastAPI + vanilla JS. No framework, no build step.

## Architecture

```
Browser (mic + slides + audio)
   │  WebSocket /ws  (JSON: generate / use_deck / user_text / audio_in / present_*)
   ▼
FastAPI relay (backend/main.py)
   │  WebSocket  (Azure Realtime protocol)
   ▼
Azure OpenAI gpt-realtime  — STT + reasoning + tool calls + TTS + VAD/interruption
```
- **Auto slide change** = the model calls the `go_to_slide` / `next_slide` / `previous_slide` tools.
- **Interruption** = the Realtime API's server-side VAD (`interrupt_response`) + the browser flushing
  its buffered audio.
- **Deck generation** = a short text-mode call to the model returns the deck as JSON.
- **Upload** = `python-pptx` extracts text + speaker notes from your `.pptx` into the same structure.

## Run locally

Requires **Python 3.11+** and an **Azure OpenAI** resource with a **`gpt-realtime`** deployment.

```powershell
# 1. Virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1          # (macOS/Linux: source .venv/bin/activate)

# 2. Dependencies
pip install -r requirements.txt

# 3. Secrets — copy the template and paste your key
copy .env.example .env              # (macOS/Linux: cp .env.example .env)
#    then edit .env and set AZURE_OPENAI_API_KEY

# 4. Start
python -u backend\main.py
```

Open **http://127.0.0.1:8000**, enter a topic (or upload a `.pptx`), and start asking.

### `.env`

```
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
AZURE_OPENAI_API_KEY=<your key>
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-realtime
AZURE_OPENAI_API_VERSION=2025-04-01-preview
REALTIME_VOICE=alloy               # optional: alloy | echo | shimmer | verse
```

## Using it

- **Type a topic** → *Generate & Present* → a fresh deck appears.
- **Upload a `.pptx`** → *Upload a .pptx* → your deck appears (text + notes are read; images aren't).
- **Ask** by typing, or click **🎤 Talk** and speak (allow the mic).
- **▶ Present** → the AI narrates the whole deck; use **⏸ Pause / ⏹ Stop**.
- 🎧 **Use headphones for voice** — on open speakers the mic can hear the AI and self-interrupt.
  Or tick **"Mute mic while the AI speaks."**

## Cost / token tips

- Responses are capped (`max_response_output_tokens`) to keep audio short.
- **Turn the mic off when you're not asking** — an open mic streams billable audio every ~85 ms.
- Concise decks = fewer context tokens per turn. Generation runs in cheaper **text mode**.
- Optional: drop `whisper-1` transcription, or generate decks with a cheaper chat model.

## Deploy

See the **Deployment** section below — the short version: any host with **WebSocket support** and
**HTTPS** (the mic requires a secure context off localhost). A `Dockerfile` is included.

## Project layout

```
ml-voice-agent/
├── backend/
│   ├── main.py          # FastAPI relay: /ws, /upload, deck generation, Present Mode
│   └── pptx_extract.py  # .pptx -> deck (python-pptx)
├── frontend/
│   ├── index.html       # topic screen + deck view
│   ├── app.js           # controller (WS, deck, mic, present, upload, reconnect)
│   ├── audio.js         # PCM16 playback (Web Audio)
│   ├── mic.js           # mic capture -> 24 kHz PCM16 (AudioWorklet)
│   ├── mic-worklet.js   # capture processor
│   └── style.css
├── requirements.txt
├── Dockerfile
├── .env.example
└── PROJECT_PLAN.md      # the phased build log
```

---

# Deployment guide

**Two hard requirements for any host:**
1. **WebSockets** — the app is WS-based end to end.
2. **HTTPS** — browsers only allow microphone access (`getUserMedia`) on a **secure context**.
   `http://` works on `localhost` only; a deployed URL **must** be `https://` or the mic is blocked.

Also: set your **`.env` values as the host's environment variables** (never commit `.env`), and let the
host set `PORT` (the app already reads `HOST`/`PORT`).

### Option A — Azure Container Apps  *(recommended: same cloud as your model = low latency)*
1. `az login`
2. Build & push the image (or let ACA build from source):
   ```bash
   az containerapp up \
     --name ai-voice-presenter --resource-group <rg> \
     --location eastus2 --source . --ingress external --target-port 8000
   ```
3. In the Container App → **Settings → Secrets/Environment variables**, add
   `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT_NAME`,
   `AZURE_OPENAI_API_VERSION`, and set `HOST=0.0.0.0`.
   ACA gives you an `https://…` URL with WebSockets by default. ✅

### Option B — Azure App Service (Web App for Containers or Python)
- Deploy the container (or the repo). In **Configuration → Application settings** add the same env vars.
- **Enable "Web sockets: On"** in Configuration → General settings. App Service terminates TLS for you (HTTPS). ✅

### Option C — Render / Railway / Fly.io  *(fastest to a public URL)*
- New **Web Service** from the repo (they detect the `Dockerfile`).
- Add the env vars in the dashboard. WebSockets + HTTPS are on by default.
- Start command is the image `CMD` (`python -u backend/main.py`); they inject `PORT`. ✅

### Option D — A plain VM (Azure VM / EC2 / Droplet)
- Run the container, then put **nginx** in front with a real domain + **Let's Encrypt** TLS, and
  proxy WebSockets:
  ```nginx
  location / {
      proxy_pass http://127.0.0.1:8000;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
      proxy_set_header Host $host;
  }
  ```

### Build & run the container locally first
```bash
docker build -t ai-voice-presenter .
docker run -p 8000:8000 --env-file .env ai-voice-presenter
# http://localhost:8000  (localhost is a secure context, so the mic works here)
```

### Production notes
- **CORS/origins:** the app serves its own frontend, so no cross-origin setup is needed.
- **Realtime session limits:** long sessions can drop; the frontend auto-reconnects and re-applies the
  deck, but expect an occasional blip on very long talks.
- **Secrets:** rotate the Azure key if it was ever committed; keep it only in host env vars.
- **Scaling:** each browser holds one WS to the backend and one backend↔Azure WS. Scale out horizontally;
  no shared state between connections.
