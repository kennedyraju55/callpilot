"""WebSocket bridge: Twilio Media Stream ↔ AI Voice Provider (OpenAI or Gemini).

Architecture:
  1. Twilio connects WebSocket, sends "start" event with streamSid
  2. Provider (OpenAI or Gemini) is selected via AI_PROVIDER in .env
  3. Bidirectional audio: Twilio ↔ Provider
  4. Provider yields normalized events (audio, transcripts, interruptions)

Switch providers by setting AI_PROVIDER=openai or AI_PROVIDER=gemini in .env.
"""

import json
import asyncio
import traceback
from pathlib import Path
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect
from app.config import settings
from app.twilio_service import call_store, CallStatus
from app.context_builder import retrieve_context
from app.providers import get_provider

TRANSCRIPTS_DIR = Path("transcripts")
SYSTEM_PROMPT_FILE = Path("context/system-prompt.txt")

DEFAULT_SYSTEM_PROMPT = """\
You are CallPilot, a professional AI phone assistant.
You are calling on behalf of your client, {client_name}.
You are speaking to a real person on the other end of a phone line.

YOUR TASK:
{instructions}

{rag_context}

GUIDELINES:
- Follow the user's instructions EXACTLY. Do not add, invent, or assume any details beyond what the instructions say.
- Be polite, natural, and conversational.
- Stay focused on the task. Don't ramble.
- If asked to hold, say "Sure, I'll wait" and stay silent.
- When the task is done, politely end: "Thank you, that's all I needed!"
- If you don't know the answer to a question and it's not in your documents, say: "I don't have that information right now, but I'll let {client_name} know and get back to you."
- If the person is confused about talking to an AI, briefly explain you're an AI assistant calling on {client_name}'s behalf.
"""


def _log(call_id: str, msg: str):
    print(f"[VoiceApp][{call_id}] {msg}")


def build_system_prompt(instructions: str, rag_context: str = "", custom_prompt: str | None = None) -> str:
    name = settings.client_name

    if custom_prompt:
        template = custom_prompt
    elif SYSTEM_PROMPT_FILE.exists():
        template = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    else:
        template = DEFAULT_SYSTEM_PROMPT

    rag_section = ""
    if rag_context:
        rag_section = (
            f"REFERENCE INFORMATION FROM {name.upper()}'S DOCUMENTS:\n"
            f"{rag_context}\n\n"
            "Use the above information to answer any questions during the call. "
            f"If the information isn't in your documents, say you'll need to "
            f"check with {name} and get back to them."
        )

    return template.format(
        client_name=name,
        instructions=instructions,
        rag_context=rag_section,
    )


def _save_transcript(call_id: str, record):
    if not record.transcript:
        _log(call_id, "No transcript to save")
        return

    TRANSCRIPTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    clean_number = record.to_number.replace("+", "").replace(" ", "")
    filename = f"{timestamp}_{call_id}_{clean_number}.txt"
    filepath = TRANSCRIPTS_DIR / filename

    lines = [
        f"CallPilot — Conversation Transcript",
        f"{'=' * 40}",
        f"Call ID:      {call_id}",
        f"To:           {record.to_number}",
        f"Provider:     {settings.ai_provider.upper()}",
        f"Date:         {record.created_at}",
        f"Instructions: {record.instructions}",
        f"{'=' * 40}",
        "",
    ]
    for entry in record.transcript:
        role = "🤖 AI" if entry["role"] == "assistant" else "👤 Caller"
        lines.append(f"{role}: {entry['text']}")
        lines.append("")

    lines.append(f"{'=' * 40}")
    lines.append(f"Total entries: {len(record.transcript)}")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    _log(call_id, f"Transcript saved → {filepath}")


async def _wait_for_twilio_start(websocket: WebSocket, call_id: str) -> str | None:
    _log(call_id, "Waiting for Twilio 'start' event...")
    try:
        for _ in range(10):
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            data = json.loads(raw)
            event = data.get("event")
            _log(call_id, f"Twilio event: {event}")
            if event == "start":
                sid = data["start"]["streamSid"]
                _log(call_id, f"Got streamSid: {sid}")
                return sid
            elif event in ("connected", "media"):
                continue
    except Exception as e:
        _log(call_id, f"Error waiting for start: {e}")
    return None


async def handle_media_stream(websocket: WebSocket, call_id: str):
    """Main handler: bridges Twilio ↔ AI provider bidirectionally."""
    await websocket.accept()
    _log(call_id, "WebSocket accepted")

    record = call_store.get(call_id)
    if not record:
        _log(call_id, "ERROR: Unknown call ID")
        await websocket.close(code=1008, reason="Unknown call ID")
        return

    record.status = CallStatus.IN_PROGRESS
    provider = None

    try:
        stream_sid = await _wait_for_twilio_start(websocket, call_id)
        if not stream_sid:
            _log(call_id, "Never got streamSid — aborting")
            return

        # Build system prompt with RAG context
        _log(call_id, "Retrieving document context...")
        rag_context = retrieve_context(record.instructions)
        if rag_context:
            _log(call_id, f"Found relevant context ({len(rag_context)} chars)")
        system_prompt = build_system_prompt(record.instructions, rag_context, record.system_prompt)

        # Connect to the selected AI provider
        provider = get_provider(call_id, system_prompt)
        _log(call_id, f"Connecting to {settings.ai_provider.upper()} provider...")
        await provider.connect()
        await provider.configure_session()
        _log(call_id, f"✓ {settings.ai_provider.upper()} ready")

        async def twilio_to_ai():
            """Forward caller audio: Twilio → AI provider."""
            try:
                while True:
                    raw = await websocket.receive_text()
                    data = json.loads(raw)
                    if data.get("event") == "media":
                        await provider.send_audio(data["media"]["payload"])
                    elif data.get("event") == "stop":
                        _log(call_id, "Twilio stream stopped")
                        break
            except WebSocketDisconnect:
                _log(call_id, "Twilio disconnected")
            except Exception as e:
                _log(call_id, f"twilio→ai error: {e}")

        async def ai_to_twilio():
            """Forward AI audio and handle events: AI provider → Twilio."""
            async for event in provider.events():
                etype = event["type"]

                if etype == "audio":
                    await websocket.send_json({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": event["data"]},
                    })

                elif etype == "speech_started" and event.get("ai_speaking"):
                    _log(call_id, "Caller interrupted — stopping AI audio")
                    await provider.cancel_response()
                    await websocket.send_json({"event": "clear", "streamSid": stream_sid})

                elif etype == "transcript_ai":
                    record.transcript.append({"role": "assistant", "text": event["text"]})
                    _log(call_id, f"AI: {event['text']}")

                elif etype == "transcript_caller":
                    record.transcript.append({"role": "caller", "text": event["text"]})
                    _log(call_id, f"Caller: {event['text']}")

                elif etype == "response_done":
                    _log(call_id, "AI response complete, listening...")

                elif etype == "error":
                    _log(call_id, f"Provider ERROR: {event['message']}")

        twilio_task = asyncio.create_task(twilio_to_ai())
        ai_task = asyncio.create_task(ai_to_twilio())

        done, pending = await asyncio.wait(
            [twilio_task, ai_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for task in done:
            exc = task.exception()
            if exc:
                _log(call_id, f"Task exception: {exc}")
                traceback.print_exception(type(exc), exc, exc.__traceback__)

    except Exception as e:
        _log(call_id, f"FATAL: {e}")
        traceback.print_exc()
    finally:
        if provider:
            await provider.close()
        record.status = CallStatus.COMPLETED
        _save_transcript(call_id, record)
        _log(call_id, f"Call ended. {len(record.transcript)} transcript entries")

