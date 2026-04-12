# 📞 CallPilot

> AI-powered outbound phone call assistant — tell it what to do, give it a number, and it handles the entire conversation with RAG-enhanced context.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg)](https://fastapi.tiangolo.com)
[![Twilio](https://img.shields.io/badge/Twilio-Realtime%20Voice-red.svg)](https://www.twilio.com)
[![OpenAI](https://img.shields.io/badge/OpenAI-Realtime%20API-412991.svg)](https://platform.openai.com)
[![RAG](https://img.shields.io/badge/RAG-ChromaDB-orange.svg)]()

## 🎬 Demo

*Imagine watching the live transcript scroll in real-time as the AI calls your dentist, provides your insurance details from a PDF, and books your appointment — all while you sip coffee.*

> Add a GIF/screenshot of the web UI showing a live call transcript here.

## 🔥 Why This Exists

Scheduling appointments, following up on orders, making reservations — phone calls eat up hours of our week on tasks that follow predictable patterns. **CallPilot** is your personal phone assistant that makes outbound calls on your behalf, equipped with knowledge from your personal documents (insurance cards, medical records, addresses) via RAG. It introduces itself, handles the conversation naturally, and saves a full transcript when done.

## ✨ Features

- 📞 **AI-powered outbound calls** — provide a phone number + instructions, and CallPilot handles the rest
- 🧠 **RAG (Retrieval-Augmented Generation)** — drop PDFs, TXT, or DOCX files in `context/` and the AI references them during calls
- 🎙️ **Real-time voice conversation** — bidirectional audio streaming via Twilio ↔ OpenAI Realtime API
- 📝 **Live transcript UI** — watch the conversation happen in real-time in your browser
- 💾 **Automatic transcript saving** — every call saved with timestamp, call ID, and phone number
- 🤖 **Natural persona** — AI introduces itself as your assistant, with graceful fallbacks when unsure
- 📄 **Multi-format document support** — PDF, TXT, DOCX, and Markdown files for RAG context
- 🔍 **Semantic search** — ChromaDB vector store with OpenAI embeddings for relevant context retrieval
- 🌐 **Beautiful dark-themed web UI** — clean interface to initiate calls and monitor progress

## 🏗️ Architecture

```
┌──────────────┐         ┌──────────────┐         ┌─────────────────────┐
│   Web UI     │────────▶│   FastAPI     │────────▶│   Twilio            │
│  (Browser)   │   POST  │   Server     │  REST   │   Outbound Call     │
└──────┬───────┘  /call  └──────┬───────┘         └──────────┬──────────┘
       │                        │                             │
       │  Live Transcript       │  WebSocket                  │ Media Stream
       │◀───────────────────────│◀────────────────────────────┘
       │                        │
       │                 ┌──────▼───────┐         ┌─────────────────────┐
       │                 │ Media Stream │◀───────▶│  OpenAI Realtime    │
       │                 │   Bridge     │  WS     │  API (Voice)        │
       │                 └──────┬───────┘         └─────────────────────┘
       │                        │
       │                 ┌──────▼───────┐         ┌─────────────────────┐
       │                 │   Context    │◀────────│   ChromaDB          │
       │                 │   Builder    │  query  │   Vector Store      │
       │                 └──────────────┘         └──────────┬──────────┘
       │                                                      │
       │                                          ┌───────────▼─────────┐
       │                                          │  context/ folder    │
       │                                          │  PDFs, TXT, DOCX    │
       │                                          └─────────────────────┘
```

### RAG Pipeline

```
Startup:  context/*.pdf|txt|docx → Parse → Chunk (500 chars) → Embed → ChromaDB
Call Time: Instructions → Embed query → Top 5 chunks → Inject into system prompt
```

### Audio Bridge

```
Twilio (g711_ulaw 8kHz) ↔ WebSocket ↔ FastAPI ↔ WebSocket ↔ OpenAI Realtime API
```

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+**
- **[Twilio account](https://www.twilio.com/try-twilio)** (trial gives free credits)
- **[OpenAI API key](https://platform.openai.com/api-keys)** (needs Realtime API access)
- **[ngrok](https://ngrok.com/download)** (for exposing localhost to Twilio)

### Installation

```bash
git clone https://github.com/kennedyraju55/callpilot.git
cd callpilot
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Twilio + OpenAI credentials
```

### Add Your Documents (Optional)

```bash
# Drop personal docs the AI can reference during calls
context/
├── raju-profile.txt       # Name, phone, address (always included)
├── insurance-card.pdf     # "What's your policy number?"
├── medical-notes.txt      # Medical history for appointments
└── any-other-doc.docx     # Supports PDF, TXT, DOCX, MD
```

### Run

```bash
# Terminal 1: Start ngrok tunnel
ngrok http 8000

# Terminal 2: Start the server
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** → Enter a phone number + instructions → Hit "Make the Call" 🚀

## 📁 Project Structure

```
callpilot/
├── app/
│   ├── main.py              # FastAPI routes, WebSocket, doc indexing on startup
│   ├── config.py            # Pydantic environment settings
│   ├── twilio_service.py    # Twilio outbound call + TwiML generation
│   ├── media_stream.py      # WebSocket bridge: Twilio ↔ OpenAI + transcript saving
│   ├── doc_processor.py     # Document parser, chunker, embedder → ChromaDB
│   └── context_builder.py   # Queries ChromaDB for relevant chunks at call time
├── context/                 # Drop your docs here (PDF, TXT, DOCX, MD)
│   └── raju-profile.txt     # Base profile (name, phone, address)
├── vectorstore/             # ChromaDB persistent storage (auto-generated)
├── transcripts/             # Saved call transcripts (auto-generated)
├── static/
│   └── index.html           # Web UI (dark theme)
├── test_bridge.py           # Integration test for Twilio → OpenAI flow
├── requirements.txt
├── .env.example
└── README.md
```

## 🔌 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `POST` | `/call` | Initiate a call (`{to_number, instructions}`) |
| `GET` | `/call/{id}` | Get call status + transcript |
| `POST` | `/twiml/{id}` | Twilio webhook (auto-called) |
| `WS` | `/media-stream/{id}` | Twilio media stream (auto-connected) |

## 💡 Example Use Cases

- 🦷 *"Book a dentist appointment for Tuesday at 3pm"* → AI provides name/DOB/insurance from docs
- 🍽️ *"Make a dinner reservation for 2 at 7pm tonight under the name Guthikonda"*
- 📦 *"Call about order #12345, ask for delivery status and estimated arrival"*
- 🏥 *"Schedule a follow-up appointment. I'm available Monday or Wednesday afternoon"*

## 💰 Cost Per Call (~2 min)

| Component | Estimated Cost |
|-----------|---------------|
| Twilio | ~$0.03 |
| OpenAI Realtime (gpt-4o) | ~$0.10–0.20 |
| OpenAI Embeddings (one-time) | ~$0.001 |
| **Total** | **~$0.13–0.23** |

## 🤝 Contributing

Contributions welcome! Ideas for Phase 2:
- Live RAG re-querying mid-call
- Web UI document upload
- Call recording (audio files)
- Auto-generated call summaries
- Multi-user support

Please open an issue or submit a PR.

## 📄 License

MIT License — see [LICENSE](LICENSE)

## 👨‍💻 Author

**Nrk Raju Guthikonda**
- 🏢 Senior Software Engineer at Microsoft (Copilot Search Infrastructure)
- 🔗 [GitHub](https://github.com/kennedyraju55) | [LinkedIn](https://www.linkedin.com/in/nrk-raju-guthikonda-504066a8/)
- 🚀 Building 116+ open-source AI tools for real-world impact

---

<p align="center">
  <b>⭐ If CallPilot saves you from phone hold music, give it a star!</b>
</p>
