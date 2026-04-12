"""WebSocket bridge: Twilio Media Stream ↔ OpenAI Realtime API.

Architecture:
  1. Twilio connects WebSocket, sends "start" event with streamSid
  2. We connect to OpenAI Realtime API, configure the session
  3. We inject a greeting message and trigger response.create so the AI speaks first
  4. Bidirectional audio: Twilio audio → OpenAI, OpenAI audio → Twilio
  5. Call ends when either side disconnects

Twilio Media Streams use mulaw 8kHz (g711_ulaw).
OpenAI Realtime API natively supports g711_ulaw — no conversion needed.
"""

import json
import asyncio
import traceback
from pathlib import Path
from datetime import datetime, timezone
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed
from fastapi import WebSocket, WebSocketDisconnect
from app.config import settings
from app.twilio_service import call_store, CallStatus
from app.context_builder import retrieve_context

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model={model}"
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

    # Priority: per-call custom prompt > file > hardcoded default
    if custom_prompt:
        template = custom_prompt
    elif SYSTEM_PROMPT_FILE.exists():
        template = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    else:
        template = DEFAULT_SYSTEM_PROMPT

    # Build RAG section
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
    """Save call transcript to a file in transcripts/ folder."""
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
    """Wait for Twilio's 'start' event and return the streamSid."""
    _log(call_id, "Waiting for Twilio 'start' event...")
    try:
        # Twilio sends connected → start within the first few messages
        for _ in range(10):
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            data = json.loads(raw)
            event = data.get("event")
            _log(call_id, f"Twilio event: {event}")

            if event == "start":
                sid = data["start"]["streamSid"]
                _log(call_id, f"Got streamSid: {sid}")
                return sid
            elif event == "connected":
                continue
            elif event == "media":
                continue  # Audio arrived before start — keep waiting
    except Exception as e:
        _log(call_id, f"Error waiting for start: {e}")
    return None


async def _connect_openai(call_id: str):
    """Connect to OpenAI Realtime API and return the WebSocket."""
    url = OPENAI_REALTIME_URL.format(model=settings.openai_realtime_model)
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "OpenAI-Beta": "realtime=v1",
    }
    _log(call_id, f"Connecting to OpenAI ({settings.openai_realtime_model})...")
    ws = await ws_connect(url, additional_headers=headers)
    _log(call_id, "OpenAI connected")
    return ws


async def _configure_openai_session(openai_ws, call_id: str, instructions: str, rag_context: str = "", custom_prompt: str | None = None):
    """Configure the OpenAI session and make the AI speak first."""

    # 1. Send session configuration
    session_config = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": settings.openai_realtime_voice,
            "instructions": build_system_prompt(instructions, rag_context, custom_prompt),
            "modalities": ["text", "audio"],
            "temperature": 0.8,
            "input_audio_transcription": {"model": "whisper-1"},
        },
    }
    await openai_ws.send(json.dumps(session_config))
    _log(call_id, "Session config sent")

    # 2. Consume session.created and session.updated
    for _ in range(2):
        try:
            resp = await asyncio.wait_for(openai_ws.recv(), timeout=10)
            data = json.loads(resp)
            _log(call_id, f"OpenAI setup: {data['type']}")
        except asyncio.TimeoutError:
            _log(call_id, "Timeout waiting for OpenAI setup response")
            break

    # 3. Inject a user message so the AI has something to respond to,
    #    then trigger response.create to make the AI speak first.
    await openai_ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": "Someone just picked up the phone. Say hi naturally."
            }]
        }
    }))
    await openai_ws.send(json.dumps({"type": "response.create"}))
    _log(call_id, "Triggered AI greeting via response.create")


async def handle_media_stream(websocket: WebSocket, call_id: str):
    """Main handler: bridges Twilio ↔ OpenAI bidirectionally."""
    await websocket.accept()
    _log(call_id, "WebSocket accepted")

    record = call_store.get(call_id)
    if not record:
        _log(call_id, "ERROR: Unknown call ID")
        await websocket.close(code=1008, reason="Unknown call ID")
        return

    record.status = CallStatus.IN_PROGRESS
    openai_ws = None

    try:
        # Step 1: Wait for Twilio stream to start (gives us streamSid)
        stream_sid = await _wait_for_twilio_start(websocket, call_id)
        if not stream_sid:
            _log(call_id, "Never got streamSid — aborting")
            return

        # Step 2: Connect to OpenAI
        openai_ws = await _connect_openai(call_id)

        # Step 3: Retrieve RAG context from uploaded documents
        _log(call_id, "Retrieving document context...")
        rag_context = retrieve_context(record.instructions)
        if rag_context:
            _log(call_id, f"Found relevant context ({len(rag_context)} chars)")
        else:
            _log(call_id, "No document context available")

        # Step 4: Configure session and trigger AI greeting
        await _configure_openai_session(openai_ws, call_id, record.instructions, rag_context, record.system_prompt)

        # Step 5: Bidirectional audio bridge
        async def twilio_to_openai():
            """Forward caller audio from Twilio → OpenAI."""
            try:
                while True:
                    raw = await websocket.receive_text()
                    data = json.loads(raw)
                    event = data.get("event")

                    if event == "media":
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": data["media"]["payload"],
                        }))
                    elif event == "stop":
                        _log(call_id, "Twilio stream stopped")
                        break
            except WebSocketDisconnect:
                _log(call_id, "Twilio disconnected")
            except Exception as e:
                _log(call_id, f"twilio→openai error: {e}")

        async def openai_to_twilio():
            """Forward AI audio from OpenAI → Twilio."""
            try:
                async for raw in openai_ws:
                    data = json.loads(raw)
                    msg_type = data.get("type", "")

                    if msg_type == "response.audio.delta":
                        # Forward audio to Twilio
                        await websocket.send_json({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": data["delta"]},
                        })

                    elif msg_type == "response.audio_transcript.done":
                        text = data.get("transcript", "")
                        if text:
                            record.transcript.append({"role": "assistant", "text": text})
                            _log(call_id, f"AI: {text}")

                    elif msg_type == "conversation.item.input_audio_transcription.completed":
                        text = data.get("transcript", "")
                        if text:
                            record.transcript.append({"role": "caller", "text": text})
                            _log(call_id, f"Caller: {text}")

                    elif msg_type == "error":
                        err = data.get("error", {})
                        _log(call_id, f"OpenAI ERROR: {err.get('message', str(err))}")

                    elif msg_type == "response.done":
                        _log(call_id, "AI response complete, listening...")

            except ConnectionClosed as e:
                _log(call_id, f"OpenAI closed: code={e.code}")
            except Exception as e:
                _log(call_id, f"openai→twilio error: {e}")

        # Run both tasks — when one ends, cancel the other
        twilio_task = asyncio.create_task(twilio_to_openai())
        openai_task = asyncio.create_task(openai_to_twilio())

        done, pending = await asyncio.wait(
            [twilio_task, openai_task],
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
        if openai_ws:
            try:
                await openai_ws.close()
            except Exception:
                pass
        record.status = CallStatus.COMPLETED
        _save_transcript(call_id, record)
        _log(call_id, f"Call ended. {len(record.transcript)} transcript entries")
