# CallPilot — Copilot Instructions

> This file tells GitHub Copilot (and any future LLM) everything about this project.

## What Is CallPilot?

CallPilot is an **AI-powered outbound phone call assistant**. You give it a phone number and instructions (e.g., "Book a dentist appointment for Tuesday at 3pm"), and it places a real phone call, speaks with the person who answers, and follows your instructions autonomously.

**Owner:** Nrk Raju Guthikonda (`kennedyraju55` on GitHub)

## Architecture Overview

```
User (Web UI)
  │
  ▼
FastAPI Server (app/main.py)
  │
  ├── POST /call → Twilio REST API (outbound call)
  │                    │
  │                    ▼
  │               Callee's Phone
  │                    │
  │                    ▼ (Twilio fetches TwiML)
  ├── POST /twiml/{id} → returns <Connect><Stream> TwiML
  │                           │
  │                           ▼ (WebSocket)
  ├── WS /media-stream/{id} ←→ OpenAI Realtime API
  │       (app/media_stream.py)
  │       Bidirectional audio: Twilio mulaw ↔ OpenAI g711_ulaw
  │
  ├── RAG Pipeline
  │     context/ folder → doc_processor.py → ChromaDB → context_builder.py
  │     (documents are chunked, embedded, and retrieved per-call)
  │
  ├── GET/POST /system-prompt → Editable system prompt
  ├── GET /spending → Live cost tracker
  └── POST /amd-callback/{id} → Voicemail detection (auto-hangup)
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Telephony | Twilio Programmable Voice, Media Streams (WebSocket) |
| AI Voice | OpenAI Realtime API (`gpt-4o-realtime-preview`) |
| RAG | ChromaDB (embedded), OpenAI `text-embedding-3-small` |
| Frontend | Vanilla HTML/CSS/JS (dark theme, single page) |
| Tunnel | ngrok (exposes localhost to Twilio) |

## File Structure

```
voice app/
├── app/
│   ├── main.py              # FastAPI app, all routes, lifespan startup
│   ├── config.py            # Pydantic Settings (env vars)
│   ├── twilio_service.py    # Twilio call initiation, TwiML, AMD, call store
│   ├── media_stream.py      # WebSocket bridge: Twilio ↔ OpenAI Realtime API
│   ├── doc_processor.py     # RAG: parse docs → chunk → embed → ChromaDB
│   └── context_builder.py   # RAG: query ChromaDB → return relevant chunks
├── context/
│   ├── system-prompt.txt    # Editable system prompt template (NOT indexed by RAG)
│   └── raju-profile.txt     # Personal info indexed into RAG
├── static/
│   └── index.html           # Web UI (call form, transcript, system prompt editor, spending)
├── transcripts/             # Auto-saved call transcripts (timestamped .txt files)
├── vectorstore/             # ChromaDB persistent storage (auto-generated)
├── check_spending.py        # CLI spending tracker script
├── test_bridge.py           # Test file
├── requirements.txt         # Python dependencies
├── .env                     # Environment variables (secrets — NOT committed)
├── .env.example             # Template for .env
└── README.md                # Project documentation
```

## Key Files — What Each Does

### `app/main.py`
- FastAPI app with lifespan (indexes docs on startup)
- Routes: `/` (UI), `/call` (POST), `/call/{id}` (GET status), `/twiml/{id}`, `/call-status/{id}`, `/amd-callback/{id}`, `/system-prompt` (GET/POST), `/spending` (GET), `/test-call`, `/media-stream/{id}` (WebSocket)
- Replaces `{{CLIENT_NAME}}` in HTML template

### `app/media_stream.py`
- **The core of the app** — bridges Twilio and OpenAI bidirectionally
- `handle_media_stream()`: accepts Twilio WebSocket, connects to OpenAI, runs two async tasks (twilio→openai, openai→twilio)
- `build_system_prompt()`: merges template + instructions + RAG context
- `_save_transcript()`: saves conversation to `transcripts/` folder
- `_configure_openai_session()`: sets voice, VAD, audio format, triggers AI greeting
- Prompt priority: per-call custom prompt > `context/system-prompt.txt` > hardcoded `DEFAULT_SYSTEM_PROMPT`

### `app/twilio_service.py`
- `initiate_call()`: creates Twilio outbound call with AMD (voicemail detection)
- `build_twiml_for_stream()`: generates TwiML to connect call to our WebSocket
- `CallRecord` dataclass: stores call state, transcript, system_prompt
- `call_store`: in-memory dict of active calls

### `app/config.py`
- Pydantic Settings loaded from `.env`
- Keys: `twilio_account_sid`, `twilio_auth_token`, `twilio_phone_number`, `openai_api_key`, `server_base_url`, `openai_realtime_model`, `openai_realtime_voice`, `client_name`

### `app/doc_processor.py`
- Scans `context/` folder for `.txt`, `.md`, `.pdf`, `.docx` files
- Skips `system-prompt.txt` (config, not content)
- Chunks text (500 chars, 50 overlap) → embeds with `text-embedding-3-small` → stores in ChromaDB collection `callpilot_docs`
- Re-indexes from scratch on every server startup

### `app/context_builder.py`
- `retrieve_context(query)`: embeds the query, searches ChromaDB top-5, returns formatted text
- Called once per call with the call instructions as query

### `context/system-prompt.txt`
- Template with placeholders: `{client_name}`, `{instructions}`, `{rag_context}`
- Editable via web UI (`/system-prompt` endpoints)
- Uses Python `str.format()` — escape literal braces as `{{` `}}`

### `static/index.html`
- Single-page dark-themed UI
- Sections: Call Form, System Prompt Editor (collapsible), Spending Dashboard (collapsible), Call Status + Live Transcript
- Polls `/call/{id}` every 2s during active calls

## Audio Pipeline Details

- **Twilio** sends/receives **g711_ulaw** (mulaw 8kHz) via WebSocket Media Streams
- **OpenAI Realtime API** natively supports `g711_ulaw` — **no audio conversion needed**
- Audio input: 1 token per 100ms → 600 tokens/min
- Audio output: 1 token per 50ms → 1,200 tokens/min
- Server VAD (Voice Activity Detection) is enabled — OpenAI detects when caller stops speaking

## RAG Pipeline

1. **Startup**: `index_documents()` scans `context/`, parses files, chunks, embeds, stores in ChromaDB
2. **Per-call**: `retrieve_context(instructions)` queries ChromaDB for relevant chunks
3. **Injection**: Relevant chunks are injected into the system prompt under `{rag_context}`
4. Documents in `context/` are auto-indexed; `system-prompt.txt` is excluded

## Voicemail Detection

- Uses Twilio's **Async AMD** (`machine_detection="DetectMessageEnd"`, `async_amd=True`)
- If AMD detects machine/voicemail/fax → auto-hangs up via `/amd-callback/{id}`
- Saves money by not running OpenAI on voicemail greetings

## Spending Tracking

- `/spending` endpoint returns live Twilio costs + estimated OpenAI costs
- Twilio costs from `usage.records.this_month` API
- OpenAI costs estimated from total call duration × token rates
- Also available as CLI: `python check_spending.py`

## Environment Variables (.env)

```
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...
OPENAI_API_KEY=sk-...
SERVER_BASE_URL=https://your-ngrok-url.ngrok-free.dev
OPENAI_REALTIME_MODEL=gpt-4o-realtime-preview
OPENAI_REALTIME_VOICE=alloy
CLIENT_NAME=Raju
```

## Running the App

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start ngrok tunnel
ngrok http 8000

# 3. Update .env with ngrok URL (SERVER_BASE_URL)

# 4. Start server
uvicorn app.main:app --reload --port 8000

# 5. Open http://localhost:8000
```

## Cost Estimates

| Item | Cost |
|------|------|
| Twilio phone number | $1.15/month |
| Outbound call | ~$0.02/min |
| OpenAI Realtime audio input | $100/1M tokens (~$0.06/min) |
| OpenAI Realtime audio output | $200/1M tokens (~$0.24/min) |
| OpenAI embeddings | $0.02/1M tokens (negligible) |
| **Typical 2-min call** | **~$0.60–$0.80** |

## Known Gotchas

1. **ngrok URL changes every restart** — must update `SERVER_BASE_URL` in `.env`
2. **Twilio trial accounts** can only call verified phone numbers
3. **AI may read system prompt aloud** if trigger message is instruction-like — use natural triggers like "Someone just picked up the phone. Say hi naturally."
4. **`system-prompt.txt` uses `str.format()`** — any literal `{` or `}` in the prompt must be doubled (`{{`, `}}`)
5. **Call store is in-memory** — call records are lost on server restart (transcripts persist as files)
6. **ChromaDB re-indexes on every startup** — fast for small doc sets, may need optimization for large ones

## Future / Phase 2 Ideas

- **Live RAG**: Mid-call document lookup (function calling during conversation)
- **Image/OCR support**: Index photos (insurance cards, etc.) in `context/` folder
- **Call recording**: Save audio files alongside transcripts
- **Persistent call store**: SQLite or similar instead of in-memory dict
- **Multi-user support**: Multiple client profiles
- **Inbound calls**: Handle incoming calls, not just outbound
- **gpt-4o-mini-realtime**: Cheaper model option (~3x less cost)
