"""OpenAI Realtime API voice provider.

Yields normalized events:
  {"type": "audio",             "data": "<b64 g711_ulaw>"}
  {"type": "transcript_ai",    "text": "..."}
  {"type": "transcript_caller","text": "..."}
  {"type": "speech_started",   "ai_speaking": bool, "response_id": str|None}
  {"type": "response_done"}
  {"type": "error",            "message": "..."}
"""

import json
import asyncio
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed
from app.config import settings

OPENAI_URL = "wss://api.openai.com/v1/realtime?model={model}"


class OpenAIProvider:
    def __init__(self, call_id: str, system_prompt: str):
        self.call_id = call_id
        self.system_prompt = system_prompt
        self._ws = None
        self._ai_speaking = False
        self._current_response_id = None

    async def connect(self):
        url = OPENAI_URL.format(model=settings.openai_realtime_model)
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        self._ws = await ws_connect(url, additional_headers=headers)

    async def configure_session(self):
        session_config = {
            "type": "session.update",
            "session": {
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 200,
                    "silence_duration_ms": 300,
                },
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "voice": settings.openai_realtime_voice,
                "instructions": self.system_prompt,
                "modalities": ["text", "audio"],
                "temperature": 0.7,
                "input_audio_transcription": {"model": "whisper-1"},
            },
        }
        await self._ws.send(json.dumps(session_config))

        # Consume session.created + session.updated
        for _ in range(2):
            try:
                resp = await asyncio.wait_for(self._ws.recv(), timeout=10)
                data = json.loads(resp)
                print(f"[OpenAI][{self.call_id}] setup: {data['type']}")
            except asyncio.TimeoutError:
                break

        # Inject greeting trigger
        await self._ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Someone just picked up the phone. Say hi naturally."}],
            },
        }))
        await self._ws.send(json.dumps({"type": "response.create"}))

    async def send_audio(self, audio_b64: str):
        if not self._ws:
            return
        await self._ws.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": audio_b64,
        }))

    async def cancel_response(self):
        if self._ws:
            await self._ws.send(json.dumps({"type": "response.cancel"}))

    async def events(self):
        try:
            async for raw in self._ws:
                data = json.loads(raw)
                msg_type = data.get("type", "")

                if msg_type == "response.created":
                    self._current_response_id = data.get("response", {}).get("id")
                    self._ai_speaking = True

                elif msg_type == "response.audio.delta":
                    self._ai_speaking = True
                    yield {"type": "audio", "data": data["delta"]}

                elif msg_type == "response.done":
                    self._ai_speaking = False
                    self._current_response_id = None
                    yield {"type": "response_done"}

                elif msg_type == "input_audio_buffer.speech_started":
                    yield {
                        "type": "speech_started",
                        "ai_speaking": self._ai_speaking,
                        "response_id": self._current_response_id,
                    }
                    self._ai_speaking = False
                    self._current_response_id = None

                elif msg_type == "response.audio_transcript.done":
                    text = data.get("transcript", "")
                    if text:
                        yield {"type": "transcript_ai", "text": text}

                elif msg_type == "conversation.item.input_audio_transcription.completed":
                    text = data.get("transcript", "")
                    if text:
                        yield {"type": "transcript_caller", "text": text}

                elif msg_type == "error":
                    err = data.get("error", {})
                    yield {"type": "error", "message": err.get("message", str(err))}

        except ConnectionClosed as e:
            print(f"[OpenAI][{self.call_id}] closed: code={e.code}")
        except Exception as e:
            print(f"[OpenAI][{self.call_id}] error: {e}")

    async def close(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
