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
