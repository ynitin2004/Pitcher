# Corrections & Fixes — Plan

Follow-up fixes after the first live run on Render. Tackled in phases, one concern at a time.

| # | Issue reported | Root cause | Status |
|---|---|---|---|
| C1 | AI **speaks/answers in another language** (Present Mode + voice Q&A) | Whisper auto-detects the wrong language from accented/EN-IN speech; the model instructions never force a reply language | ✅ |
| C2 | **UI not responsive** — doesn't adapt to phone/small screens | Layout rows (deck header, ask bar, topic form) don't wrap/stack; only slide padding had a breakpoint | ✅ |
| C3 | Verify both fixes end-to-end and redeploy | — | ✅ |

> **Also fixed (found while testing):** the topic and deck screens were **showing at the same time** —
> our `display:flex` was overriding the `hidden` attribute. Fixed with `[hidden] { display:none !important }`.

---

## Correction C1 — Force English everywhere  🗣️
**Goal:** it always understands and replies in **English**, regardless of accent or input language.

- [x] C1.1 Add a configurable presenter language (default **English** / `en`) via env
      (`PRESENTER_LANGUAGE`, `PRESENTER_LANGUAGE_CODE`).
- [x] C1.2 **STT:** set `input_audio_transcription.language = "en"` so Whisper stops auto-detecting.
- [x] C1.3 **Answers:** add an explicit rule to the presenter instructions — *"Always reply in English,
      no matter what language the question is in."*
- [x] C1.4 **Present Mode:** narration prompt says *"…in English."*
- [x] C1.5 **Generation:** the deck-generator prompt says *"Write everything in English."*

**Done when:** a spoken or typed question in any accent gets an **English** spoken answer, and Present
Mode narrates in English.

---

## Correction C2 — Responsive / mobile UI  📱
**Goal:** usable and tidy on a phone (narrow, portrait), not just desktop.

- [x] C2.1 Deck header (`.deck-head`) stacks vertically on small screens; present controls wrap.
- [x] C2.2 Ask bar (`.ask`) — input takes its own full-width row; mic + Ask sit below.
- [x] C2.3 Topic form stacks (input above a full-width button); input `min-width` no longer overflows.
- [x] C2.4 Smaller hero, headings, and paddings under ~640px; body padding reduced; nothing overflows
      horizontally.
- [x] C2.5 Keep the desktop layout unchanged above the breakpoint.

**Done when:** at ~390px wide (typical phone) every control is reachable, nothing overflows sideways,
and text stays readable.

---

## Correction C3 — Verify & redeploy  ✅
- [x] C3.1 Local: regenerate a deck, ask a question (typed) → English answer.
- [x] C3.2 Local: Present Mode narrates in English.
- [x] C3.3 Check the layout at a phone width (DevTools device toolbar).
- [x] C3.4 Commit + push → Render auto-redeploys; hard-refresh and re-check.

**Done when:** the Render URL answers in English and looks right on a phone.

---

# Corrections — Phase 2

Reversing the over-correction (forced English) and hardening the model + errors.

## C4 — Match the user's language (multilingual) ✅
- [x] C4.1 Removed forced English; Whisper **auto-detects** the language again.
- [x] C4.2 Answers: *"Reply in the SAME language the user speaks/types."* (fallback = `PRESENTER_LANGUAGE`).
- [x] C4.3 Generation: *"write everything in the same language as the topic."*
- [x] C4.4 Present Mode narrates in the deck's language.
- **✅ Verified:** Hindi topic → Hindi deck + Hindi answer; English topic → English.

## C5 — Reduce hallucination ✅
- [x] C5.1 **Grounding rule:** answer only from the slides; if it's not there, say so — never invent.
- [x] C5.2 **Out-of-scope rule:** unrelated questions get a brief "that's outside this deck."
- [x] C5.3 **Lower `temperature` = 0.6** (env `REALTIME_TEMPERATURE`) for less drift.
- **✅ Verified:** "Tesla stock price?" on a Photosynthesis deck → politely refused.

## C6 — Structured error handling ✅
- [x] C6.1 `friendly_error()` classifies Azure error codes → clear messages + a level
      (rate-limit → "busy, try again"; content-filter → "can't answer that"; auth; token/length; session).
- [x] C6.2 On error, backend **recovers** (clears in-flight state, stops a hung Present) instead of stalling.
- [x] C6.3 Whisper transcription failure → "Didn't catch that — please try again."
- [x] C6.4 Upload **size limit** (20 MB) + friendlier messages.
- [x] C6.5 Frontend shows errors in a red toast (`#deck-msg`) / topic message, and re-enables input.

## C7 — Robustness bug found while testing ✅
- [x] C7.1 **UTF-8 logging:** a non-ASCII topic (e.g. Hindi) crashed `print()` on the Windows cp1252
      console and killed the WebSocket handler. Fixed by forcing `sys.stdout` to UTF-8 at startup.

**Done when:** the app replies in the user's language, stays grounded to the deck, and surfaces friendly
errors without crashing — all verified locally.
