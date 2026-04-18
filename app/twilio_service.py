"""Twilio service — initiates outbound calls and generates TwiML."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect
from app.config import settings


class CallStatus(str, Enum):
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CallRecord:
    call_id: str
    to_number: str
    instructions: str
    client_id: str = "default"
    system_prompt: str | None = None
    status: CallStatus = CallStatus.QUEUED
    twilio_sid: str | None = None
    transcript: list[dict] = field(default_factory=list)
    summary: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    recording_sid: str | None = None
    recording_url: str | None = None
    recording_duration: int | None = None


# In-memory call store (fine for MVP)
call_store: dict[str, CallRecord] = {}


def initiate_call(to_number: str, instructions: str, client_id: str = "default", system_prompt: str | None = None) -> CallRecord:
    """Place an outbound call via Twilio and return the call record."""
    call_id = str(uuid.uuid4())[:8]
    record = CallRecord(call_id=call_id, to_number=to_number, instructions=instructions, client_id=client_id, system_prompt=system_prompt)

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

    # Twilio will fetch TwiML from our /twiml endpoint when the call connects
    twiml_url = f"{settings.server_base_url}/twiml/{call_id}"
    status_url = f"{settings.server_base_url}/call-status/{call_id}"

    amd_callback_url = f"{settings.server_base_url}/amd-callback/{call_id}"

    call_kwargs = dict(
        to=to_number,
        from_=settings.twilio_phone_number,
        url=twiml_url,
        status_callback=status_url,
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        status_callback_method="POST",
        machine_detection="DetectMessageEnd",
        async_amd=True,
        async_amd_status_callback=amd_callback_url,
        async_amd_status_callback_method="POST",
    )

    if settings.enable_recording:
        call_kwargs.update(
            record=True,
            recording_channels="dual",
            recording_status_callback=f"{settings.server_base_url}/recording-status/{call_id}",
            recording_status_callback_method="POST",
            recording_status_callback_event=["completed"],
        )

    twilio_call = client.calls.create(**call_kwargs)

    record.twilio_sid = twilio_call.sid
    record.status = CallStatus.QUEUED
    call_store[call_id] = record

    return record


def build_twiml_for_stream(call_id: str) -> str:
    """Generate TwiML that connects the call to our WebSocket media stream."""
    response = VoiceResponse()
    connect = Connect()
    stream_url = f"wss://{settings.server_base_url.replace('https://', '').replace('http://', '')}/media-stream/{call_id}"
    connect.stream(url=stream_url)
    response.append(connect)

    return str(response)
