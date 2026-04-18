"""CallPilot — AI phone assistant."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Request, Form
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import settings
from app.twilio_service import initiate_call, build_twiml_for_stream, call_store
from app.media_stream import handle_media_stream
from app.doc_processor import index_documents


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: index documents from context/ folder
    print("[CallPilot] Starting up...")
    count = index_documents()
    if count:
        print(f"[CallPilot] ✓ {count} document chunks ready for RAG")
    yield
    print("[CallPilot] Shutting down.")


app = FastAPI(title="CallPilot", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# --- Request/Response models ---

class CallRequest(BaseModel):
    to_number: str
    instructions: str
    system_prompt: str | None = None


class CallResponse(BaseModel):
    call_id: str
    status: str
    twilio_sid: str | None = None


# --- API Endpoints ---

@app.get("/", response_class=HTMLResponse)
async def home():
    with open("static/index.html", encoding="utf-8") as f:
        html = f.read().replace("{{CLIENT_NAME}}", settings.client_name)
        return html


@app.post("/call", response_model=CallResponse)
async def start_call(req: CallRequest):
    """Initiate an outbound AI-powered phone call."""
    record = initiate_call(
        to_number=req.to_number,
        instructions=req.instructions,
        system_prompt=req.system_prompt,
    )
    return CallResponse(
        call_id=record.call_id,
        status=record.status.value,
        twilio_sid=record.twilio_sid,
    )


@app.get("/call/{call_id}")
async def get_call(call_id: str):
    """Get the current status and transcript of a call."""
    record = call_store.get(call_id)
    if not record:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Call not found")
    return {
        "call_id": record.call_id,
        "to_number": record.to_number,
        "status": record.status.value if hasattr(record.status, 'value') else record.status,
        "instructions": record.instructions,
        "transcript": record.transcript,
        "summary": record.summary,
        "created_at": record.created_at,
        "recording_url": f"/recordings/{record.call_id}.mp3" if record.recording_sid else None,
        "recording_duration": record.recording_duration,
    }


@app.post("/twiml/{call_id}", response_class=Response)
async def twiml_webhook(call_id: str):
    """Twilio fetches this TwiML when the outbound call connects."""
    twiml = build_twiml_for_stream(call_id)
    return Response(content=twiml, media_type="application/xml")


@app.post("/call-status/{call_id}")
async def call_status_webhook(call_id: str, request: Request):
    """Twilio posts call status updates here."""
    form = await request.form()
    status = form.get("CallStatus", "unknown")
    record = call_store.get(call_id)
    if record:
        record.status = status
        print(f"[{call_id}] Twilio status update: {status}")
    return {"ok": True}


@app.post("/amd-callback/{call_id}")
async def amd_callback(call_id: str, request: Request):
    """Twilio Answering Machine Detection callback. Hang up if voicemail."""
    from twilio.rest import Client
    form = await request.form()
    answered_by = form.get("AnsweredBy", "unknown")
    print(f"[{call_id}] AMD result: {answered_by}")

    if answered_by in ("machine_start", "machine_end_beep", "machine_end_silence", "machine_end_other", "fax"):
        record = call_store.get(call_id)
        if record and record.twilio_sid:
            client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
            client.calls(record.twilio_sid).update(status="completed")
            print(f"[{call_id}] ☎️ Voicemail detected — call disconnected to save costs")
    return {"ok": True}


@app.post("/recording-status/{call_id}")
async def recording_status_webhook(call_id: str, request: Request):
    """Twilio posts here when call recording is ready. Download MP3 locally."""
    import httpx
    from pathlib import Path

    form = await request.form()
    recording_sid = form.get("RecordingSid")
    recording_url = form.get("RecordingUrl")  # base URL, append .mp3 to download
    duration = form.get("RecordingDuration")
    status = form.get("RecordingStatus", "")

    print(f"[{call_id}] Recording status: {status} sid={recording_sid} dur={duration}s")

    record = call_store.get(call_id)
    if record:
        record.recording_sid = recording_sid
        record.recording_url = recording_url
        record.recording_duration = int(duration) if duration else None

    if status == "completed" and recording_url:
        recordings_dir = Path("recordings")
        recordings_dir.mkdir(exist_ok=True)
        dest = recordings_dir / f"{call_id}.mp3"
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                r = await http.get(
                    f"{recording_url}.mp3",
                    auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                )
                r.raise_for_status()
                dest.write_bytes(r.content)
            print(f"[{call_id}] ✓ Recording saved to {dest} ({len(r.content)} bytes)")
        except Exception as e:
            print(f"[{call_id}] ✗ Failed to download recording: {e}")

    return {"ok": True}


@app.get("/recordings/{filename}")
async def get_recording(filename: str):
    """Serve a saved call recording MP3."""
    from pathlib import Path
    from fastapi.responses import FileResponse
    path = Path("recordings") / filename
    if not path.exists() or not path.is_file():
        return Response(status_code=404, content="Recording not found")
    return FileResponse(path, media_type="audio/mpeg", filename=filename)


@app.post("/test-call")
async def create_test_call(req: CallRequest):
    """Create a call record without dialing Twilio — for local testing."""
    import uuid
    from app.twilio_service import CallRecord, call_store as store
    call_id = "test-" + str(uuid.uuid4())[:4]
    record = CallRecord(call_id=call_id, to_number=req.to_number, instructions=req.instructions)
    store[call_id] = record
    return {"call_id": call_id, "status": "ready", "ws_url": f"/media-stream/{call_id}"}


@app.get("/system-prompt")
async def get_system_prompt():
    """Return the current system prompt template."""
    from pathlib import Path
    prompt_file = Path("context/system-prompt.txt")
    if prompt_file.exists():
        return {"prompt": prompt_file.read_text(encoding="utf-8")}
    return {"prompt": ""}


@app.post("/system-prompt")
async def save_system_prompt(request: Request):
    """Save an updated system prompt template."""
    from pathlib import Path
    data = await request.json()
    prompt = data.get("prompt", "")
    prompt_file = Path("context/system-prompt.txt")
    prompt_file.write_text(prompt, encoding="utf-8")
    return {"ok": True}


@app.get("/spending")
async def get_spending():
    """Return current spending breakdown for Twilio + estimated OpenAI."""
    from twilio.rest import Client as TwilioClient

    client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

    # Twilio costs
    records = client.usage.records.this_month.list()
    twilio_items = []
    twilio_total = 0.0
    labels = {
        "phonenumbers-local": "Phone Number",
        "calls-outbound": "Outbound Calls",
        "calls-media-stream-minutes": "Media Streams",
        "calls-text-to-speech": "Text-to-Speech",
    }
    for r in records:
        price = float(r.price or 0)
        if price > 0 and r.category in labels:
            twilio_items.append({"label": labels[r.category], "count": r.count, "cost": round(price, 4)})
    if "totalprice" in {r.category for r in records}:
        for r in records:
            if r.category == "totalprice":
                twilio_total = round(float(r.price or 0), 4)
    else:
        twilio_total = round(sum(i["cost"] for i in twilio_items), 4)

    # Call stats
    calls = client.calls.list(limit=100)
    total_seconds = sum(int(c.duration or 0) for c in calls)
    total_calls = len(calls)

    # OpenAI estimate
    total_min = total_seconds / 60.0
    input_tokens = int((total_min * 0.5) * 600)
    output_tokens = int((total_min * 0.5) * 1200)
    input_cost = round((input_tokens / 1_000_000) * 100, 4)
    output_cost = round((output_tokens / 1_000_000) * 200, 4)
    openai_total = round(input_cost + output_cost + 0.01, 4)

    return {
        "twilio": {"items": twilio_items, "total": twilio_total},
        "openai": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_cost": input_cost,
            "output_cost": output_cost,
            "total": openai_total,
        },
        "calls": total_calls,
        "talk_minutes": round(total_min, 1),
        "grand_total": round(twilio_total + openai_total, 2),
    }


@app.websocket("/media-stream/{call_id}")
async def media_stream_endpoint(websocket: WebSocket, call_id: str):
    """WebSocket endpoint for Twilio Media Streams → OpenAI Realtime API bridge."""
    await handle_media_stream(websocket, call_id)
