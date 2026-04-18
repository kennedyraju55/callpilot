"""Google Gemini Live API voice provider.

Audio conversion:
  Twilio → Gemini: g711_ulaw 8kHz  → PCM16 16kHz
  Gemini → Twilio: PCM16 24kHz     → g711_ulaw 8kHz

Yields the same normalized events as OpenAIProvider.
"""

import json
import asyncio
import base64
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed
from app.config import settings

GEMINI_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
    "?key={api_key}"
)

# audioop is in stdlib for Python ≤ 3.12; audioop-lts backports it for 3.13+
try:
    import audioop
except ImportError:
    try:
        import audioop_lts as audioop  # pip install audioop-lts
    except ImportError:
        audioop = None


def _check_audioop():
    if audioop is None:
        raise RuntimeError(
            "audioop not available. Run: pip install audioop-lts"
        )


def _mulaw_to_pcm16_16k(mulaw_b64: str) -> str:
    """base64 g711_ulaw 8kHz → base64 PCM16 16kHz."""
    _check_audioop()
    raw = base64.b64decode(mulaw_b64)
    pcm_8k = audioop.ulaw2lin(raw, 2)
    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
    return base64.b64encode(pcm_16k).decode()


def _pcm16_24k_to_mulaw(pcm_b64: str) -> str:
    """base64 PCM16 24kHz → base64 g711_ulaw 8kHz."""
    _check_audioop()
    raw = base64.b64decode(pcm_b64)
    pcm_8k, _ = audioop.ratecv(raw, 2, 1, 24000, 8000, None)
    mulaw = audioop.lin2ulaw(pcm_8k, 2)
    return base64.b64encode(mulaw).decode()


class GeminiProvider:
    def __init__(self, call_id: str, system_prompt: str):
        self.call_id = call_id
        self.system_prompt = system_prompt
        self._ws = None

    async def connect(self):
        _check_audioop()
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set in .env")
        url = GEMINI_URL.format(api_key=settings.gemini_api_key)
        self._ws = await ws_connect(url)

    async def configure_session(self):
        setup_msg = {
            "setup": {
                "model": settings.gemini_realtime_model,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": settings.gemini_realtime_voice
                            }
                        }
                    },
                },
                "systemInstruction": {
                    "parts": [{"text": self.system_prompt}]
                },
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
            }
        }
        await self._ws.send(json.dumps(setup_msg))

        # Wait for setupComplete
        for _ in range(10):
            try:
                resp = await asyncio.wait_for(self._ws.recv(), timeout=10)
                data = json.loads(resp)
                if "setupComplete" in data:
                    print(f"[Gemini][{self.call_id}] setup complete")
                    break
            except asyncio.TimeoutError:
                break

        # Trigger greeting via a user text turn
        await self._ws.send(json.dumps({
            "clientContent": {
                "turns": [{
                    "role": "user",
                    "parts": [{"text": "Someone just picked up the phone. Say hi naturally."}],
                }],
                "turnComplete": True,
            }
        }))

    async def send_audio(self, audio_b64: str):
        if not self._ws:
            return
        pcm_b64 = _mulaw_to_pcm16_16k(audio_b64)
        await self._ws.send(json.dumps({
            "realtimeInput": {
                "audio": {
                    "data": pcm_b64,
                    "mimeType": "audio/pcm;rate=16000",
                }
            }
        }))

    async def cancel_response(self):
        # Gemini VAD handles interruption automatically.
        # Sending a new turn signals the model to stop current response.
        pass

    async def events(self):
        try:
            async for raw in self._ws:
                data = json.loads(raw)
                sc = data.get("serverContent", {})
                if not sc:
                    continue

                # AI audio chunks
                model_turn = sc.get("modelTurn", {})
                for part in model_turn.get("parts", []):
                    inline = part.get("inlineData", {})
                    mime = inline.get("mimeType", "")
                    if mime.startswith("audio/pcm") and inline.get("data"):
                        mulaw_b64 = _pcm16_24k_to_mulaw(inline["data"])
                        yield {"type": "audio", "data": mulaw_b64}

                # Caller interrupted the AI
                if sc.get("interrupted"):
                    yield {"type": "speech_started", "ai_speaking": True, "response_id": None}

                # AI finished its turn
                if sc.get("turnComplete"):
                    yield {"type": "response_done"}

                # AI transcript (assembled progressively)
                out_trans = sc.get("outputTranscription", {})
                if out_trans.get("text"):
                    yield {"type": "transcript_ai", "text": out_trans["text"]}

                # Caller transcript
                in_trans = sc.get("inputTranscription", {})
                if in_trans.get("text"):
                    yield {"type": "transcript_caller", "text": in_trans["text"]}

        except ConnectionClosed as e:
            print(f"[Gemini][{self.call_id}] closed: code={e.code}")
        except Exception as e:
            print(f"[Gemini][{self.call_id}] error: {e}")

    async def close(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
