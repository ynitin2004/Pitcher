"""
Phase 2 — Prove the Azure OpenAI `gpt-realtime` connection works.

This is a TEXT-ONLY test (no audio yet) so we can isolate one thing: can we
open the Realtime websocket, ask a question, get a streamed text answer, and
watch the model call a `go_to_slide` tool? Everything after Phase 2 builds on this.

The Realtime API is just JSON events over a websocket. We:
  1. connect               -> server sends `session.created`
  2. session.update        -> tell it to use text, give instructions + tools
  3. conversation.item.create + response.create  -> ask a question
  4. read streamed events  -> `response.text.delta`, function-call events, `response.done`

Run:  python scripts\realtime_test.py
(Make sure AZURE_OPENAI_API_KEY is filled in .env first.)
"""

import asyncio
import json
import os
from pathlib import Path

import websockets
from dotenv import load_dotenv

# Load .env from the project root (one level up from scripts/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-realtime").strip()
API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview").strip()

# The slide topics the presenter knows about (used in the instructions).
SLIDES = [
    "1. What is Machine Learning?",
    "2. Types of ML (supervised / unsupervised / reinforcement)",
    "3. How a model learns (training data -> model)",
    "4. Common algorithms (regression, trees, neural nets)",
    "5. Real-world use cases",
    "6. Limitations & ethics",
]

INSTRUCTIONS = (
    "You are presenting a 6-slide deck on the Basics of Machine Learning.\n"
    "The slides are:\n" + "\n".join(SLIDES) + "\n\n"
    "When the user asks about a topic, FIRST call the `go_to_slide` tool with the "
    "matching slide number, THEN answer in one or two short sentences."
)

# One tool: the model calls this to change the slide on screen.
TOOLS = [
    {
        "type": "function",
        "name": "go_to_slide",
        "description": "Switch the on-screen slide deck to the given slide number (1-6).",
        "parameters": {
            "type": "object",
            "properties": {
                "slide_number": {
                    "type": "integer",
                    "description": "Which slide to show, 1 through 6.",
                }
            },
            "required": ["slide_number"],
        },
    }
]


def build_url() -> str:
    host = ENDPOINT.replace("https://", "").replace("http://", "").rstrip("/")
    return (
        f"wss://{host}/openai/realtime"
        f"?api-version={API_VERSION}&deployment={DEPLOYMENT}"
    )


def preflight() -> bool:
    ok = True
    if not ENDPOINT:
        print("  [x] AZURE_OPENAI_ENDPOINT is missing in .env")
        ok = False
    if not API_KEY:
        print("  [x] AZURE_OPENAI_API_KEY is empty in .env  <-- paste your real key here")
        ok = False
    return ok


async def send(ws, event: dict):
    await ws.send(json.dumps(event))


async def run_turn(ws, user_text: str):
    """Ask one question and process events until the model finishes (response.done).

    Returns the pending function call (dict) if the model asked to call a tool,
    else None. Prints the streamed text answer as it arrives.
    """
    print(f"\n>>> You: {user_text}")

    await send(ws, {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": user_text}],
        },
    })
    await send(ws, {"type": "response.create"})

    pending_call = None
    printed_prefix = False

    async for raw in ws:
        event = json.loads(raw)
        etype = event.get("type", "")

        # Streamed text answer (handle both event name variants)
        if etype in ("response.text.delta", "response.output_text.delta"):
            if not printed_prefix:
                print("<<< gpt-realtime: ", end="", flush=True)
                printed_prefix = True
            print(event.get("delta", ""), end="", flush=True)

        # The model wants to call a tool — capture name + arguments
        elif etype == "response.function_call_arguments.done":
            pending_call = {
                "call_id": event.get("call_id"),
                "name": event.get("name"),
                "arguments": event.get("arguments", "{}"),
            }
            print(f"\n[TOOL CALL] {pending_call['name']}({pending_call['arguments']})")

        # New output item announced (function_call carries the name/call_id early)
        elif etype == "response.output_item.added":
            item = event.get("item", {})
            if item.get("type") == "function_call" and pending_call is None:
                pending_call = {
                    "call_id": item.get("call_id"),
                    "name": item.get("name"),
                    "arguments": "",
                }

        elif etype == "response.done":
            if printed_prefix:
                print()  # newline after streamed text
            return pending_call

        elif etype == "error":
            print("\n[ERROR EVENT]", json.dumps(event.get("error", event), indent=2))
            return pending_call

        # Uncomment to see every event the server sends (great for learning):
        # else:
        #     print(f"[event] {etype}")


async def main():
    print("=== Phase 2: Azure gpt-realtime connection test ===")
    if not preflight():
        print("\nFix the above in .env, then re-run:  python scripts\\realtime_test.py")
        return

    url = build_url()
    print(f"Connecting to: {url}")

    try:
        async with websockets.connect(
            url, additional_headers={"api-key": API_KEY}, max_size=None
        ) as ws:
            # 1) Wait for the session to be created
            first = json.loads(await ws.recv())
            print(f"[connected] server said: {first.get('type')}")

            # 2) Configure the session: text mode, instructions, tools
            await send(ws, {
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "instructions": INSTRUCTIONS,
                    "tools": TOOLS,
                    "tool_choice": "auto",
                },
            })

            # 3) Turn A — a plain question (expect a text answer)
            await run_turn(ws, "In one short sentence, what is machine learning?")

            # 4) Turn B — a question that should trigger the go_to_slide tool
            call = await run_turn(
                ws, "Show me the slide about algorithms and tell me about neural networks."
            )

            # 5) If it called the tool, return a result and let it finish speaking
            if call and call["name"] == "go_to_slide":
                await send(ws, {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps({"ok": True}),
                    },
                })
                await send(ws, {"type": "response.create"})
                # Reuse the reader by draining this follow-up response
                printed_prefix = False
                async for raw in ws:
                    event = json.loads(raw)
                    etype = event.get("type", "")
                    if etype in ("response.text.delta", "response.output_text.delta"):
                        if not printed_prefix:
                            print("<<< gpt-realtime: ", end="", flush=True)
                            printed_prefix = True
                        print(event.get("delta", ""), end="", flush=True)
                    elif etype == "response.done":
                        if printed_prefix:
                            print()
                        break
                    elif etype == "error":
                        print("\n[ERROR EVENT]", json.dumps(event.get("error", event), indent=2))
                        break

            print("\n=== Done. If you saw a text answer AND a [TOOL CALL], Phase 2 works. ===")

    except websockets.InvalidStatus as e:
        print(f"\n[connection rejected] {e}")
        print("Common causes: wrong api-key, wrong deployment name, or an api-version")
        print("your resource doesn't support for gpt-realtime.")
    except Exception as e:
        print(f"\n[unexpected error] {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
