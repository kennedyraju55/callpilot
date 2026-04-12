"""Integration test: Simulates Twilio connecting to our WebSocket and verifies
that OpenAI Realtime API responds with audio via our bridge.

This test:
1. Starts the FastAPI server
2. Creates a call record (bypassing Twilio outbound)
3. Connects a WebSocket to /media-stream/{call_id} (like Twilio would)
4. Sends a fake Twilio "connected" and "start" event
5. Verifies that OpenAI sends audio back (the AI greeting)
6. Reports success/failure
"""

import asyncio
import json
import uuid
import httpx
import websockets.asyncio.client as ws_client


SERVER = "http://localhost:8000"
WS_SERVER = "ws://localhost:8000"


async def test_media_stream():
    print("=" * 60)
    print("INTEGRATION TEST: Twilio ↔ OpenAI Bridge")
    print("=" * 60)

    # Step 1: Create a test call record via the API
    print("\n[1] Creating test call record via /test-call...")
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{SERVER}/test-call", json={
            "to_number": "+15551234567",
            "instructions": "You are calling a pizza place. Ask if they are open and what their hours are."
        })
        data = resp.json()
        call_id = data["call_id"]
    print(f"    Call ID: {call_id}")

    # Step 2: Connect WebSocket (simulating Twilio)
    print("\n[2] Connecting WebSocket to /media-stream/{call_id}...")
    ws_url = f"{WS_SERVER}/media-stream/{call_id}"

    try:
        async with ws_client.connect(ws_url) as ws:
            print("    WebSocket connected!")

            # Step 3: Send Twilio "connected" event
            print("\n[3] Sending Twilio 'connected' event...")
            await ws.send(json.dumps({
                "event": "connected",
                "protocol": "Call",
                "version": "1.0.0"
            }))

            # Step 4: Send Twilio "start" event with fake streamSid
            fake_stream_sid = "MZfake" + uuid.uuid4().hex[:20]
            print(f"\n[4] Sending Twilio 'start' event (streamSid={fake_stream_sid[:15]}...)...")
            await ws.send(json.dumps({
                "event": "start",
                "sequenceNumber": "1",
                "start": {
                    "accountSid": "ACfake",
                    "streamSid": fake_stream_sid,
                    "callSid": "CAfake",
                    "tracks": ["inbound"],
                    "mediaFormat": {
                        "encoding": "audio/x-mulaw",
                        "sampleRate": 8000,
                        "channels": 1
                    }
                },
                "streamSid": fake_stream_sid
            }))

            # Step 5: Wait for audio responses from OpenAI (via our bridge)
            print("\n[5] Waiting for AI audio response (up to 15 seconds)...")
            audio_chunks = 0
            transcript = None
            deadline = asyncio.get_event_loop().time() + 15

            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                    data = json.loads(raw)

                    if data.get("event") == "media":
                        audio_chunks += 1
                        if audio_chunks == 1:
                            print("    First audio chunk received from AI!")
                        elif audio_chunks % 50 == 0:
                            print(f"    Received {audio_chunks} audio chunks...")

                except asyncio.TimeoutError:
                    if audio_chunks > 0:
                        break  # Got audio, silence means the AI is done speaking
                    continue

            # Step 6: Report results
            print("\n" + "=" * 60)
            if audio_chunks > 0:
                print(f"SUCCESS! Received {audio_chunks} audio chunks from AI.")
                record = call_store[call_id]
                if record.transcript:
                    print(f"Transcript ({len(record.transcript)} entries):")
                    for entry in record.transcript:
                        print(f"  {entry['role']}: {entry['text']}")
                else:
                    print("(Transcript not yet available — audio was streaming)")
                print("=" * 60)
                print("\nThe bridge is WORKING. Ready for real calls.")
            else:
                print("FAILED: No audio received from AI.")
                print("Check server logs for errors.")
                print("=" * 60)

    except Exception as e:
        print(f"\nFAILED with error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_media_stream())
