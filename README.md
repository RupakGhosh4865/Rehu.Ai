# SuperHuman AI Persona Platform

A production-ready platform for creating AI personas that look, speak, and behave like real humans — with real-time voice, photorealistic avatar, and deep product knowledge.

**Stack:** Python · FastAPI · Pipecat · Daily.co · HeyGen · ElevenLabs · Deepgram · OpenAI · ChromaDB

---

## Quick Start (Local)

### 1. Prerequisites
- Python 3.11+
- Docker (optional, for containerised run)
- API accounts: OpenAI, ElevenLabs, HeyGen, Daily.co, Deepgram

### 2. Clone & configure
```bash
cd backend
cp ../.env.example ../.env
# Edit .env and fill in your API keys
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Run locally
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` — the conversation UI loads automatically.
Open `http://localhost:8000/admin` — the admin panel.

### 5. Or run with Docker
```bash
docker-compose up --build
```

---

## Deploy to Railway.app (5 minutes, free tier available)

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo → Railway auto-detects the `railway.json` config
4. In Railway dashboard → Variables tab, add all keys from `.env.example`
5. Railway builds & deploys — get your public URL (e.g. `https://superhuman.up.railway.app`)

**That's it.** Your platform is live.

---

## API Reference

All endpoints documented at `/docs` (Swagger UI) when running locally.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | System health check |
| `/api/personas` | GET/POST | List / create personas |
| `/api/personas/{id}` | GET/PUT/DELETE | Manage individual persona |
| `/api/sessions` | POST | Start a new avatar session |
| `/api/sessions/{id}` | DELETE | End a session |
| `/api/knowledge/add` | POST | Add text to knowledge base |
| `/api/knowledge/upload` | POST | Upload file to knowledge base |
| `/api/knowledge/query` | POST | Test knowledge retrieval |
| `/api/avatar/speak` | POST | Make avatar speak manually |
| `/api/avatar/interrupt` | POST | Interrupt avatar speech |

---

## Embed on Your Website

Add this single script tag before `</body>`:

```html
<script src="https://your-app.up.railway.app/sdk/superhuman-widget.js"
  data-persona="default"
  data-position="bottom-right"
  data-color="#2E86AB"
  data-label="Talk to our AI Expert"
  data-api="https://your-app.up.railway.app">
</script>
```

Or use the Admin Panel (`/admin`) → Embed Widget to generate the code automatically.

---

## Architecture

```
Browser (visitor)
  │
  ├── Daily.co WebRTC ──────────→ Pipecat Pipeline (Railway)
  │       (voice audio)              │
  │                                  ├── Deepgram STT → text
  │                                  ├── RAG lookup (ChromaDB)
  │                                  ├── OpenAI GPT-4o mini → response
  │                                  └── ElevenLabs TTS → audio back
  │
  └── HeyGen WebRTC ─────────────→ HeyGen Streaming Avatar
          (avatar video)               (lip-synced to TTS audio)
```

---

## Environment Variables

See `.env.example` for all required and optional variables.

**Required:**
- `OPENAI_API_KEY` — [platform.openai.com](https://platform.openai.com)
- `ELEVENLABS_API_KEY` — [elevenlabs.io](https://elevenlabs.io)
- `HEYGEN_API_KEY` — [app.heygen.com](https://app.heygen.com)
- `DAILY_API_KEY` — [daily.co](https://daily.co)
- `DEEPGRAM_API_KEY` — [deepgram.com](https://deepgram.com)

---

## Cost Estimate (per customer/month)

| Service | Cost |
|---|---|
| HeyGen Streaming | $99–$399 |
| ElevenLabs | $22 |
| Deepgram | $15–40 |
| OpenAI GPT-4o mini | $20–80 |
| Daily.co | $0–50 |
| Railway hosting | $5–20 |
| **Total** | **~$160–$610** |

Revenue at $2,000/month = **80%+ gross margin**.

---

Built by SSPM Consultants | SuperHuman AI Persona Platform
