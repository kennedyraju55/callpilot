# 📞 CallPilot

An AI-powered phone call assistant that makes calls on behalf of **Raju (Nrk Raju Guthikonda)**. Tell it what to do, give it a phone number, and it handles the entire conversation — with access to your personal documents for context.

**Stack:** Python 3.11+ · FastAPI · Twilio · OpenAI Realtime API · ChromaDB (RAG)

---

## How It Works

```
You → [Web UI] → [FastAPI] → [Twilio Outbound Call]
                                      ↕
                              [Media Stream WebSocket]
                                      ↕
                    [context/ docs] → [RAG Context Builder]
                                      ↕
                            [OpenAI Realtime API (Voice)]
                                      ↕
                              [transcripts/ saved]
```

1. **Startup:** Server indexes all documents in `context/` folder into a ChromaDB vector store
2. **You provide** a phone number and instructions (e.g., *"Book a dentist appointment for Tuesday at 3pm"*)
3. The app calls the number via **Twilio**
4. When connected, Twilio streams live audio to our **WebSocket** server
5. The server retrieves **relevant document chunks** (RAG) based on your instructions
6. It bridges audio between Twilio ↔ **OpenAI Realtime API**, with RAG context injected into the system prompt
7. The AI introduces itself: *"Hi, I'm calling on behalf of Raju"*
8. If the AI doesn't know an answer, it says: *"I'll let Raju know and get back to you"*
9. **Live transcript** shown in the UI, and **saved to `transcripts/`** after the call ends

---

## Key Features

- **RAG (Retrieval-Augmented Generation):** Drop PDFs, TXT, or DOCX files in `context/` — the AI can reference them during calls (e.g., insurance info, addresses, personal details)
- **Base profile:** `context/raju-profile.txt` contains Raju's name, phone, and address — always available to the AI
- **Transcript saving:** Every call transcript is saved to `transcripts/` with timestamp, call ID, and phone number
- **Live transcript UI:** Watch the conversation happen in real-time in the browser
- **Graceful fallback:** When the AI doesn't have an answer, it promises to follow up rather than making things up

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **Twilio account** — [Sign up free](https://www.twilio.com/try-twilio) (trial gives you credits)
- **OpenAI API key** — [Get one here](https://platform.openai.com/api-keys) (needs Realtime API access)
- **ngrok** — [Download](https://ngrok.com/download) (for exposing localhost to Twilio)

### 1. Install Dependencies

```bash
cd "voice app"
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your actual credentials
```

Fill in:
- `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` — from [Twilio Console](https://console.twilio.com)
- `TWILIO_PHONE_NUMBER` — your Twilio phone number (buy one in Console → Phone Numbers)
- `OPENAI_API_KEY` — your OpenAI API key
- `SERVER_BASE_URL` — your ngrok URL (see step 3)

### 3. Add Your Documents (Optional)

Drop any personal documents into the `context/` folder:

```
context/
├── raju-profile.txt       # Base info (name, phone, address) — included by default
├── insurance-card.pdf     # Example: AI can answer "What's your policy number?"
├── medical-notes.txt      # Example: AI can relay your medical history
└── any-other-doc.docx     # Supports PDF, TXT, DOCX, MD
```

Documents are **automatically indexed at server startup** using ChromaDB embeddings.

### 4. Start ngrok

In a separate terminal:

```bash
ngrok http 8000
```

Copy the `https://xxxxx.ngrok-free.app` URL into your `.env` file as `SERVER_BASE_URL`.

### 5. Run the Server

```bash
uvicorn app.main:app --reload --port 8000
```

You should see:
```
[CallPilot] Starting up...
[CallPilot] Found 1 document(s) in context/
[CallPilot] ✓ Indexed 3 chunks from 1 file(s).
[CallPilot] ✓ 3 document chunks ready for RAG
```

### 6. Open the UI

Go to **http://localhost:8000** — enter a phone number and instructions, hit "Make the Call"!

> ⚠️ **Twilio Trial Limitation:** On a trial account, you can only call [verified phone numbers](https://www.twilio.com/docs/usage/tutorials/how-to-use-your-free-trial-account#verify-your-personal-phone-number). Add your number in Twilio Console → Verified Caller IDs.

---

## Project Structure

```
voice app/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI routes, WebSocket endpoint, startup doc indexing
│   ├── config.py            # Environment settings (Pydantic)
│   ├── twilio_service.py    # Twilio outbound call + TwiML generation
│   ├── media_stream.py      # WebSocket bridge: Twilio ↔ OpenAI + RAG context + transcript saving
│   ├── doc_processor.py     # Document parser, chunker, embedder → ChromaDB indexer
│   └── context_builder.py   # Queries ChromaDB for relevant chunks at call time
├── context/                 # Drop your docs here (PDF, TXT, DOCX, MD)
│   └── raju-profile.txt     # Base profile: name, phone, address
├── vectorstore/             # ChromaDB persistent storage (auto-generated)
├── transcripts/             # Saved call transcripts (auto-generated per call)
├── static/
│   └── index.html           # Web UI (dark theme)
├── test_bridge.py           # Integration test: simulates Twilio → OpenAI flow
├── requirements.txt
├── .env.example
└── README.md
```

---

## Architecture Deep Dive

### RAG Pipeline (Phase 1 — Pre-call)

```
context/ folder
    ↓  (startup)
[doc_processor.py]
    → Parse files (PyPDF2, python-docx, or plain read)
    → Chunk text (~500 chars with 50 char overlap)
    → Generate embeddings (OpenAI text-embedding-3-small)
    → Store in ChromaDB (persistent, in vectorstore/)
    ↓  (at call time)
[context_builder.py]
    → Embed the user's instructions as a query
    → Similarity search → top 5 relevant chunks
    → Format and inject into the system prompt
    ↓
[media_stream.py]
    → System prompt = persona + task + RAG context + guidelines
    → Sent to OpenAI Realtime API session.update
```

### Audio Bridge

```
Twilio (mulaw 8kHz g711_ulaw)
    ↕  WebSocket (bidirectional)
FastAPI media_stream handler
    ↕  WebSocket (bidirectional)
OpenAI Realtime API (natively supports g711_ulaw — no conversion needed)
```

Two async tasks run concurrently:
- **twilio_to_openai:** Forwards caller audio → OpenAI
- **openai_to_twilio:** Forwards AI audio → Twilio, captures transcripts

### Transcript Saving

After each call, a transcript file is saved to `transcripts/`:
```
transcripts/2026-04-12_15-30-00_abc123_15551234567.txt
```

Contains: call metadata, full conversation with role labels (🤖 AI / 👤 Caller), entry count.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `POST` | `/call` | Initiate a call (`{to_number, instructions}`) |
| `GET` | `/call/{id}` | Get call status + transcript |
| `POST` | `/twiml/{id}` | Twilio webhook (auto-called by Twilio) |
| `POST` | `/call-status/{id}` | Twilio status callback |
| `WS` | `/media-stream/{id}` | Twilio media stream (auto-connected) |
| `POST` | `/test-call` | Create a test call record without dialing Twilio |

---

## AI Persona

The AI introduces itself as Raju's assistant:
- Greeting: *"Hi, I'm calling on behalf of Raju."*
- Has access to docs in `context/` (personal info, insurance, etc.)
- Fallback: *"I don't have that information right now, but I'll let Raju know and get back to you."*

---

## Example Use Cases

- 🦷 *"Book a dentist appointment for Tuesday at 3pm."* → AI provides name/DOB/insurance from docs
- 🍽️ *"Make a dinner reservation for 2 at 7pm tonight under the name Guthikonda."*
- 📦 *"Call about order #12345, ask for delivery status and estimated arrival."*
- 🏥 *"Schedule a follow-up appointment. I'm available Monday or Wednesday afternoon."*

---

## Cost Per Call (Estimated)

| Component | Rate | ~2 min call |
|-----------|------|-------------|
| Twilio | ~$0.014/min (US) | ~$0.03 |
| OpenAI Realtime (gpt-4o-realtime-preview) | ~$0.05–0.10/min | ~$0.10–0.20 |
| OpenAI Embeddings (one-time indexing) | $0.02/1M tokens | ~$0.001 |
| **Total** | | **~$0.13–0.23** |

Twilio trial includes free credits. Embedding cost is negligible (one-time at startup).

---

## Dependencies

```
fastapi[standard]       # Web framework + WebSocket support
uvicorn[standard]       # ASGI server
twilio                  # Outbound calls + TwiML
openai                  # Realtime API + embeddings
websockets              # OpenAI WebSocket client
python-dotenv           # .env file loading
pydantic-settings       # Typed config from env vars
chromadb                # Vector store for RAG
PyPDF2                  # PDF text extraction
python-docx             # DOCX text extraction
```

---

## Future Ideas (Phase 2)

- **Live RAG:** Re-query vector store mid-call when new questions arise
- **Web UI upload:** Upload per-call docs through the browser instead of only `context/`
- **Call recording:** Save audio files alongside transcripts
- **Summary generation:** Auto-generate a call summary after completion
- **Multi-user:** Support multiple clients, not just Raju

---

## License

MIT — Built for learning and experimentation.
