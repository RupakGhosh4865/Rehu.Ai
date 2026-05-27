# Savant.ai

Deploy a **Savant Superhuman** — a human-grade representative that talks, listens, and knows your product. On your website, inside your product, and in your meetings.

**Stack:** Python · FastAPI · LiveAvatar · ElevenLabs · Deepgram · OpenAI · ChromaDB · Playwright

---

## Quick Start (Local)

### 1. Prerequisites
- Python 3.11+
- Docker (optional, for containerised run)
- API accounts: OpenAI, ElevenLabs, LiveAvatar, Daily.co, Deepgram

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

Open `http://localhost:8000` — the marketing homepage loads automatically.
Open `http://localhost:8000/call` — the live conversation UI.
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
5. Railway builds & deploys — get your public URL (e.g. `https://savant.up.railway.app`)

**That's it.** Your platform is live.

---

## API Reference

All endpoints documented at `/docs` (Swagger UI) when running locally.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | System health check |
| `/api/leads` | POST | Public inbound lead capture (homepage forms) |
| `/api/personas` | GET/POST | List / create personas |
| `/api/personas/{id}` | GET/PUT/DELETE | Manage individual persona |
| `/api/sessions` | POST | Start a new avatar session |
| `/api/sessions/{id}` | DELETE | End a session |
| `/api/knowledge/add` | POST | Add text to knowledge base |
| `/api/knowledge/upload` | POST | Upload file to knowledge base |
| `/api/knowledge/query` | POST | Test knowledge retrieval |
| `/api/avatar/speak` | POST | Make avatar speak manually |
| `/api/avatar/interrupt` | POST | Interrupt avatar speech |
| `/api/ride-along` | POST/GET/DELETE | Meeting-bot orchestration (Zoom/Meet/Teams) |

---

## Embed on Your Website

Add this single script tag before `</body>`:

```html
<script src="https://your-app.up.railway.app/sdk/superhuman-widget.js"
  data-persona="default"
  data-position="bottom-right"
  data-color="#0EA5A4"
  data-label="Talk to our Expert"
  data-api="https://your-app.up.railway.app">
</script>
```

Or use the Admin Panel (`/admin`) → Embed Widget to generate the code automatically.

**Programmatic control:**

```javascript
window.Savant.open();              // Open widget
window.Savant.close();             // Close widget
window.Savant.toggle();            // Toggle open/close
window.Savant.openWithGreeting(s); // Open with a custom greeting
```

(`window.SuperHuman` is kept as a backward-compat alias.)

---

## Architecture

```
Browser (visitor)
  │
  ├── Daily.co WebRTC ──────────→ Voice Pipeline (Railway)
  │       (voice audio)              │
  │                                  ├── Deepgram STT → text
  │                                  ├── RAG lookup (ChromaDB)
  │                                  ├── OpenAI GPT-4o mini → response
  │                                  └── ElevenLabs TTS → audio back
  │
  └── LiveAvatar WebRTC ─────────→ Streaming Avatar
          (avatar video)               (lip-synced to TTS audio)
```

---

## Environment Variables

See `.env.example` for all required and optional variables.

**Required:**
- `OPENAI_API_KEY` — [platform.openai.com](https://platform.openai.com)
- `ELEVENLABS_API_KEY` — [elevenlabs.io](https://elevenlabs.io)
- `LIVEAVATAR_API_KEY` — [app.liveavatar.com](https://app.liveavatar.com)
- `DAILY_API_KEY` — [daily.co](https://daily.co)
- `DEEPGRAM_API_KEY` — [deepgram.com](https://deepgram.com)

---

## Cost Estimate (per customer/month)

| Service | Cost |
|---|---|
| LiveAvatar Streaming | $99–$399 |
| ElevenLabs | $22 |
| Deepgram | $15–40 |
| OpenAI GPT-4o mini | $20–80 |
| Daily.co | $0–50 |
| Railway hosting | $5–20 |
| **Total** | **~$160–$610** |

Revenue at $2,000/month = **80%+ gross margin**.

---

Built by SSPM Consultants | Savant.ai
