# Rehu.ai — Client Deployment Guide

**Version:** 2.0  
**Platform:** SuperHuman / Rehu.ai  
**Repository:** `Blugig/Rehu.Ai`  
**Last updated:** May 2026  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Deployment Models](#3-deployment-models)
4. [Prerequisites](#4-prerequisites)
5. [Environment Variables](#5-environment-variables)
6. [Deployment Option A — Railway (Recommended)](#6-deployment-option-a--railway-recommended)
7. [Deployment Option B — Docker / Client Cloud](#7-deployment-option-b--docker--client-cloud)
8. [Deployment Option C — Local POC](#8-deployment-option-c--local-poc)
9. [Client Configuration (Admin Panel)](#9-client-configuration-admin-panel)
10. [Website Embed & Integration](#10-website-embed--integration)
11. [Persona Reference](#11-persona-reference)
12. [Sandbox vs Production](#12-sandbox-vs-production)
13. [Production Go-Live Checklist](#13-production-go-live-checklist)
14. [Security & Compliance](#14-security--compliance)
15. [Monitoring & Troubleshooting](#15-monitoring--troubleshooting)
16. [Cost Estimate](#16-cost-estimate)
17. [Recommended Rollout Timeline](#17-recommended-rollout-timeline)
18. [Known Limitations & Roadmap](#18-known-limitations--roadmap)
19. [Appendix — API Endpoints](#19-appendix--api-endpoints)

---

## 1. Executive Summary

Rehu.ai is a **Human Digital Workforce Platform** that deploys photorealistic AI personas on a client's website or intranet. Each persona speaks in real time (voice + video), powered by product knowledge uploaded via an Admin panel.

**Deployment model today:** **One hosted instance per client.**

- One deployment = one company (one set of API keys, one knowledge base)
- Multiple **personas** inside that instance (sales, HR, support, healthcare) = different AI experts for the **same** client
- **Not** multi-tenant SaaS yet (no shared platform serving 50 isolated clients from one server)

**What the client adds:** Typically one `<script>` tag or a link on their website.  
**What you manage:** Hosting, API keys, personas, knowledge, and go-live configuration.

---

## 2. Architecture Overview

### 2.1 High-level flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     CLIENT WEBSITE                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Embed Widget (superhuman-widget.js)                      │   │
│  │  → Floating "Talk to our AI Expert" button                │   │
│  │  → Opens iframe → https://rehu.client.com/call            │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTPS
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              REHU BACKEND (FastAPI on Railway/Docker)            │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ /admin      │  │ /call        │  │ /api/sessions          │  │
│  │ Configure   │  │ Live call UI │  │ Create LiveAvatar sess │  │
│  │ personas +  │  │ (LiveKit)    │  │ + RAG knowledge        │  │
│  │ knowledge   │  │              │  │                        │  │
│  └─────────────┘  └──────────────┘  └────────────────────────┘  │
│                              │                                   │
│                    Knowledge Store (./data/chromadb/)            │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        LiveAvatar       OpenAI        LiveKit
        (avatar video)   (LLM)         (WebRTC stream)
```

### 2.2 Session lifecycle (what happens on "Start Conversation")

| Step | Component | Action |
|------|-----------|--------|
| 1 | Browser (`index.html`) | Reads `?persona=...` from URL, calls `POST /api/sessions` |
| 2 | Backend (`main.py`) | Loads persona config, runs RAG query against knowledge base |
| 3 | Backend | Builds system prompt (personality + company + knowledge) |
| 4 | LiveAvatar (`liveavatar.py`) | Creates "context" with prompt + opening line |
| 5 | LiveAvatar | Creates session token (avatar ID + voice) |
| 6 | LiveAvatar | Starts session → returns LiveKit URL + client token |
| 7 | Browser | Connects to LiveKit room, attaches video + audio tracks |
| 8 | Visitor | Speaks via microphone; avatar responds with lip-synced video |

### 2.3 What differs per persona

| Layer | Source file | Per-persona customization |
|-------|-------------|---------------------------|
| Face / video | `persona_experience.py` | LiveAvatar avatar ID mapping |
| Role / personality | `persona_templates.py` | Template (HR, sales, support, etc.) |
| Company branding | Admin panel | Company name, tone, opening line |
| Product knowledge | Admin → Knowledge Base | PDFs, URLs, text per `persona_id` |
| Voice | LiveAvatar / ElevenLabs | Voice ID (production) |

### 2.4 Technology stack (current)

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11, FastAPI, Uvicorn |
| Avatar streaming | LiveAvatar API (HeyGen LiveAvatar) |
| Real-time media | LiveKit (WebRTC) |
| LLM | OpenAI GPT-4o mini |
| Knowledge (RAG) | BM25 + JSON persistence (`knowledge.py`) |
| Voice fallback | ElevenLabs + Deepgram (if LiveAvatar unavailable) |
| Frontend | Static HTML/JS/CSS served by FastAPI |
| Embed | `frontend/sdk/superhuman-widget.js` |
| Hosting | Railway, Docker, Azure, AWS, VPS |

> **Note:** `README.md` and `.env.example` in the repo still reference the older HeyGen/Daily/Pipecat stack. The live codebase uses **LiveAvatar + LiveKit**.

---

## 3. Deployment Models

### Model A — You host for the client (recommended)

You deploy Rehu on Railway or cloud. Client embeds your URL on their public website.

**Best for:** SaaS agencies, MSPs, quick client go-live, most commercial deployments.

### Model B — Client infrastructure (on-prem / private cloud)

Same Docker image runs inside the client's Azure, AWS, or on-prem environment.

**Best for:** Banks, healthcare, government — strict data residency or network isolation.

**Client must provide:**

- Container host (Docker Compose, Kubernetes, Azure App Service)
- Outbound HTTPS to `api.liveavatar.com`, OpenAI, LiveKit
- Persistent volume for `./data/chromadb`
- API keys (theirs or yours, per contract)

### Model C — Internal / intranet only

Host on VPN or internal DNS. No public embed — direct link inside SharePoint, HR portal, etc.

**Example URL:** `https://rehu.internal.acme.com/call?persona=hr-interviewer`

---

## 4. Prerequisites

### 4.1 Accounts & API keys

| Service | Required | Sign up |
|---------|----------|---------|
| LiveAvatar | **Yes** (for video avatar) | https://app.liveavatar.com/settings/api |
| OpenAI | **Yes** (for conversation) | https://platform.openai.com/api-keys |
| ElevenLabs | Optional (voice-only fallback) | https://elevenlabs.io |
| Deepgram | Optional (voice-only fallback) | https://deepgram.com |
| Railway / cloud host | **Yes** (for production) | https://railway.app |

### 4.2 Technical requirements

- Python 3.11+ (local dev) or Docker
- GitHub repo access (`Blugig/Rehu.Ai`)
- Client website: ability to add a script tag before `</body>` (or IT approval for embed)
- Browser: Chrome, Edge, Safari (modern); microphone permission required for visitors

### 4.3 Network requirements (client environment)

**Outbound HTTPS required from Rehu server:**

- `api.liveavatar.com`
- `api.openai.com`
- LiveKit signaling/media endpoints (returned per session)
- Optional: ElevenLabs, Deepgram

**Inbound HTTPS required:**

- Public access to your Rehu URL (or internal-only for Model C)
- Port 443 (or host-assigned `PORT` on Railway)

---

## 5. Environment Variables

Create `.env` in `backend/` (or set in Railway Variables tab).

### 5.1 Required for production

```env
# LiveAvatar — photorealistic avatar streaming
LIVEAVATAR_API_KEY=your_liveavatar_api_key
LIVEAVATAR_USE_SANDBOX=false

# OpenAI — conversation intelligence
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

### 5.2 Optional / advanced

```env
# LiveAvatar overrides
LIVEAVATAR_API_BASE=https://api.liveavatar.com
LIVEAVATAR_AVATAR_ID=          # Leave blank for persona-mapped avatars
LIVEAVATAR_VOICE_ID=           # Leave blank for avatar default voice

# App security
SECRET_KEY=generate-a-random-32-char-string
DEBUG=false

# CORS — restrict to client domain in production
CORS_ORIGINS=["https://www.acme.com","https://acme.com"]

# Voice-only fallback (if LiveAvatar session fails)
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
DEEPGRAM_API_KEY=

# Knowledge base storage
CHROMA_PERSIST_DIR=./data/chromadb
```

### 5.3 Sandbox vs production flag

| Value | Behavior |
|-------|----------|
| `LIVEAVATAR_USE_SANDBOX=true` | Free Wayne avatar, ~1 min sessions, all personas show same face |
| `LIVEAVATAR_USE_SANDBOX=false` | Paid plan, unique face per persona, full session length |

---

## 6. Deployment Option A — Railway (Recommended)

### Step 1 — Push code to GitHub

```bash
git add .
git commit -m "Prepare for client deployment"
git push origin main
```

Repository: `https://github.com/Blugig/Rehu.Ai`

### Step 2 — Create Railway project

1. Go to https://railway.app
2. **New Project** → **Deploy from GitHub**
3. Select `Blugig/Rehu.Ai`
4. Railway auto-detects `railway.json`:

```json
{
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "backend/Dockerfile"
  },
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1",
    "healthcheckPath": "/health"
  }
}
```

### Step 3 — Set environment variables

Railway dashboard → **Variables** → add all keys from Section 5.

Minimum:

- `LIVEAVATAR_API_KEY`
- `OPENAI_API_KEY`
- `LIVEAVATAR_USE_SANDBOX=true` (POC) or `false` (production)

### Step 4 — Add persistent volume (knowledge base)

Mount a volume at `/app/data/chromadb` so uploaded client knowledge survives redeploys.

Railway → Service → **Volumes** → Add volume → mount path: `/app/data/chromadb`

### Step 5 — Verify deployment

```bash
curl https://your-app.up.railway.app/health
```

Expected response includes `"status": "healthy"`, `"liveavatar_key_set": true`.

### Step 6 — Custom domain (optional)

Railway → **Settings** → **Domains** → Add custom domain  
Client DNS: CNAME `rehu.acme.com` → Railway-assigned hostname

### Step 7 — Smoke test URLs

| URL | Purpose |
|-----|---------|
| `https://your-url/` | Marketing homepage |
| `https://your-url/call` | Default call (Maya / sales) |
| `https://your-url/call?persona=hr-interviewer` | HR persona |
| `https://your-url/admin` | Admin panel |
| `https://your-url/docs` | Swagger API docs |

---

## 7. Deployment Option B — Docker / Client Cloud

### Step 1 — Configure environment

```bash
cd SuperHuman-Platform
cp .env.example .env
# Edit .env with client API keys
```

### Step 2 — Build and run

```bash
docker-compose up --build -d
```

### Step 3 — Persistent storage

Ensure volume mount for knowledge:

```yaml
volumes:
  - chromadb_data:/app/data/chromadb
```

Knowledge files are stored as: `./data/chromadb/{persona_id}.json`

### Step 4 — Reverse proxy (production)

Place Nginx or Azure Application Gateway in front:

- TLS termination (HTTPS)
- Optional: IP allowlist for `/admin`
- Optional: basic auth on `/admin`

Example Nginx location block:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

WebSocket support is required for voice-only fallback mode (`/ws/voice/{session_id}`).

---

## 8. Deployment Option C — Local POC

For demos before cloud deployment:

```powershell
cd SuperHuman-Platform\backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
# Create .env with LIVEAVATAR_API_KEY and OPENAI_API_KEY
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

| Local URL | Purpose |
|-----------|---------|
| http://localhost:8000/ | Homepage |
| http://localhost:8000/call | Call UI |
| http://localhost:8000/admin | Admin |

---

## 9. Client Configuration (Admin Panel)

Open: `https://your-deployed-url/admin`

### 9.1 Dashboard

Overview of personas, knowledge stats, and quick links to embed code.

### 9.2 Personas

**Create or clone a persona:**

1. Go to **Personas**
2. Clone from solution template (HR, sales, support, healthcare, product demo)
3. Set:
   - **Company name** (e.g. "Acme Corp")
   - **Persona name** (e.g. "Alex")
   - **Tone** (professional, friendly, etc.)
   - **Avatar** (production LiveAvatar ID — auto-mapped from template)

**Available solution templates:**

| Template slug | Persona ID | Use case |
|---------------|------------|----------|
| Sales | `default` | Maya — sales & conversion |
| HR | `hr-interviewer` | Alex — recruiting & HR |
| Healthcare | `healthcare-guide` | Elena — patient guidance |
| Support | `support-agent` | Riley — customer support |
| Product demo | `product-demo` | Casey — product walkthrough |

### 9.3 Knowledge Base

Train the persona on client-specific content:

| Method | How |
|--------|-----|
| Upload file | PDF, TXT, DOCX via Admin |
| Paste text | Product specs, FAQs, policies |
| URL | Admin fetches and indexes page content |

Knowledge is stored per `persona_id` at `./data/chromadb/{persona_id}.json`.

**Best practices:**

- Upload pricing sheets, product catalogs, HR handbooks, support FAQs
- One persona per domain (HR knowledge separate from sales knowledge)
- Test retrieval via Admin → Query Knowledge before go-live

### 9.4 Embed Widget

Admin → **Embed Widget**:

1. Select persona
2. Set button position (bottom-right, bottom-left)
3. Set accent color (client brand hex)
4. Set button label (e.g. "Talk to HR", "Chat with Maya")
5. Copy generated script tag

---

## 10. Website Embed & Integration

### 10.1 Standard embed (floating widget)

Paste before `</body>` on the client's website:

```html
<!-- Rehu.ai AI Persona Widget -->
<script src="https://rehu.acme.com/sdk/superhuman-widget.js"
  data-persona="hr-interviewer"
  data-position="bottom-right"
  data-color="#2E86AB"
  data-label="Talk to HR"
  data-api="https://rehu.acme.com">
</script>
```

**Widget attributes:**

| Attribute | Required | Description |
|-----------|----------|-------------|
| `data-api` | Yes | Your deployed Rehu base URL |
| `data-persona` | Yes | Persona ID (e.g. `hr-interviewer`) |
| `data-position` | No | `bottom-right` (default) or `bottom-left` |
| `data-color` | No | Hex brand color (default `#2E86AB`) |
| `data-label` | No | Button text |
| `data-auto-open` | No | `true` to auto-open after 2 seconds |
| `data-greeting` | No | Custom greeting message |

**JavaScript API (optional):**

```javascript
window.SuperHuman.open();   // Open widget programmatically
window.SuperHuman.close();  // Close widget
window.SuperHuman.toggle(); // Toggle open/close
```

### 10.2 Direct link (no widget)

```html
<a href="https://rehu.acme.com/call?persona=support-agent"
   class="btn-primary">
  Chat with our AI Support
</a>
```

### 10.3 Full-page experience

Send visitors directly to:

```
https://rehu.acme.com/call?persona=default
```

Use for email campaigns, QR codes, or dedicated landing pages.

### 10.4 Intranet / SharePoint

Link from internal portal — no public embed required:

```
https://rehu.internal.acme.com/call?persona=hr-interviewer
```

### 10.5 Known widget issue (fix before go-live)

The widget currently loads the iframe to `/?persona=...` but the call UI is at `/call`.

**Workaround until fixed:** Use direct links (`/call?persona=...`) instead of the embed widget, or patch `frontend/sdk/superhuman-widget.js` line 162:

```javascript
// Change from:
iframe.src = `${CONFIG.apiBase}/?${params.toString()}`;
// To:
iframe.src = `${CONFIG.apiBase}/call?${params.toString()}`;
```

---

## 11. Persona Reference

### Call URLs by use case

| Use case | URL |
|----------|-----|
| Sales (Maya) | `/call` or `/call?persona=default` |
| HR (Alex) | `/call?persona=hr-interviewer` |
| Healthcare (Elena) | `/call?persona=healthcare-guide` |
| Support (Riley) | `/call?persona=support-agent` |
| Product demo (Casey) | `/call?persona=product-demo` |

### Production avatar mapping

Mapped in `backend/app/persona_experience.py`:

| Persona | LiveAvatar avatar |
|---------|-------------------|
| Maya (sales) | Katya in Black Suit |
| Alex (HR) | Silas HR |
| Elena (healthcare) | Ann Doctor Standing |
| Riley (support) | Silas Customer Support |
| Casey (product demo) | Bryan Tech Expert |
| Sandbox (all) | Wayne (free tier only) |

---

## 12. Sandbox vs Production

| | Sandbox |
|---|-------|
| **Sandbox** | `LIVEAVATAR_USE_SANDBOX=true` — Free Wayne avatar, ~1 min sessions, all personas show same face |
| **Production** | `LIVEAVATAR_USE_SANDBOX=false` — Paid plan, unique face per persona, full session length |

**Recommendation:**

- **Week 1–2:** Sandbox for knowledge training and stakeholder demos
- **Go-live:** Flip to production, verify each persona's face on `/call`

---

## 13. Production Go-Live Checklist

### Infrastructure

- [ ] Rehu deployed and `/health` returns healthy
- [ ] Persistent volume mounted for `./data/chromadb`
- [ ] Custom domain configured (e.g. `rehu.acme.com`)
- [ ] HTTPS enabled (TLS certificate valid)

### API keys & mode

- [ ] `LIVEAVATAR_API_KEY` set and valid
- [ ] `OPENAI_API_KEY` set and valid
- [ ] `LIVEAVATAR_USE_SANDBOX=false`
- [ ] `SECRET_KEY` changed from default
- [ ] `CORS_ORIGINS` set to client domain(s)

### Content & personas

- [ ] Persona cloned/configured with client company name
- [ ] Knowledge base uploaded (PDFs, FAQs, product docs)
- [ ] Knowledge query tested in Admin
- [ ] Each persona call URL tested end-to-end (video + audio + mic)

### Client website integration

- [ ] Embed script pasted on client site (or direct link live)
- [ ] Widget/button appears correctly on desktop and mobile
- [ ] Microphone permission prompt works in Chrome/Safari/Edge
- [ ] Widget-style iframe URL points to `/call` (not `/`)

### Security

- [ ] `/admin` protected (VPN, basic auth, or IP allowlist)
- [ ] `.env` not committed to git
- [ ] API keys stored in host secrets manager (Railway Variables, Azure Key Vault)

### Client handoff

- [ ] Client IT notified of embed script location
- [ ] Support runbook shared (Section 15)
- [ ] Billing/usage expectations documented (Section 16)

---

## 14. Security & Compliance

### Current state

| Control | Status |
|---------|--------|
| Admin authentication | **Not built** — must protect externally |
| API key storage | Environment variables (host-managed) |
| CORS | Configurable via `CORS_ORIGINS` |
| Knowledge data | Stored on server disk (JSON files) |
| Visitor PII | Optional `visitor_name` / `visitor_email` on session create |
| Multi-tenant isolation | **Not applicable** — one instance per client |

### Recommendations for client environments

1. **Protect `/admin`** — Nginx basic auth, Azure AD, or VPN-only access
2. **Restrict CORS** — Never use `["*"]` in production
3. **Use client-owned API keys** — Where contract requires client billing/control
4. **Data residency** — Deploy in client's region (EU Azure, etc.) for GDPR
5. **Review LiveAvatar / OpenAI DPAs** — Required for healthcare/finance clients

---

## 15. Monitoring & Troubleshooting

### Health check

```bash
GET /health
```

Response fields:

- `active_sessions` — current live sessions
- `sandbox_mode` — true/false
- `liveavatar_key_set` — API key configured

### Common issues

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Dark screen, no video | Sandbox avatar not supported | Use Wayne in sandbox; disable sandbox for production avatars |
| Avatar not speaking | Audio track not attached | Restart backend; check browser autoplay policy |
| Wrong face on call | Sandbox mode | Set `LIVEAVATAR_USE_SANDBOX=false` |
| Persona shows generic answers | Empty knowledge base | Upload docs in Admin |
| Widget opens homepage not call | Widget URL bug | Use `/call?persona=...` or patch widget JS |
| CORS error on client site | Wrong `CORS_ORIGINS` | Add client domain to env |
| Knowledge lost after redeploy | No persistent volume | Mount `/app/data/chromadb` volume |
| Persona settings reset | In-memory config | Re-configure in Admin after restart |

### Logs

Railway → **Deployments** → **View Logs**  
Look for LiveAvatar session creation errors and RAG query output.

---

## 16. Cost Estimate

**Per client instance (monthly, approximate):**

| Service | Purpose | Cost |
|---------|---------|------|
| LiveAvatar | Avatar streaming (production) | $99 – $399+ |
| OpenAI GPT-4o mini | Conversation | $20 – $80 |
| Railway / Azure hosting | Backend + frontend | $5 – $50 |
| ElevenLabs / Deepgram | Voice fallback (optional) | $0 – $40 |
| **Total** | | **~$125 – $570/mo** |

**Revenue example:** Client pays $2,000/mo → ~80%+ gross margin after infra costs.

---

## 17. Recommended Rollout Timeline

### Week 1 — POC

- Deploy sandbox instance (`LIVEAVATAR_USE_SANDBOX=true`)
- Clone persona template for client
- Upload initial FAQ / product docs
- Demo `/call` to client stakeholders

### Week 2 — Pilot

- Embed on client **staging** website
- Gather feedback on knowledge gaps, tone, UX
- Iterate knowledge base uploads

### Week 3 — Go-live

- Set `LIVEAVATAR_USE_SANDBOX=false`
- Configure custom domain + CORS
- Protect `/admin`
- Embed on **production** website
- Monitor `/health` and first live sessions

### Week 4+ — Scale

- Add additional personas (HR + sales + support)
- Separate knowledge bases per persona
- New clients = new deployed instances (or future multi-tenant build)

---

## 18. Known Limitations & Roadmap

### Current limitations (do not block single-client POC)

| # | Issue | Impact | Planned fix |
|---|-------|--------|-------------|
| 1 | Widget iframe loads `/` not `/call` | Embed shows homepage | Patch `superhuman-widget.js` |
| 2 | README / `.env.example` outdated | Confusing setup | Update docs for LiveAvatar |
| 3 | Persona config not persisted | Admin edits lost on restart | Persist to disk |
| 4 | No Admin login | Security risk | Basic auth / SSO |
| 5 | No multi-tenancy | One instance per client | Future SaaS architecture |

### Future enhancements (not required for first client)

- Multi-tenant platform (many clients, one deployment)
- Client self-service portal
- Analytics dashboard (session count, conversion)
- CRM integrations (HubSpot, Salesforce)
- White-label branding per client

---

## 19. Appendix — API Endpoints

Base URL: `https://your-rehu-url.com`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | System health |
| `/api/personas` | GET | List personas |
| `/api/personas` | POST | Create persona |
| `/api/personas/{id}` | GET/PUT/DELETE | Manage persona |
| `/api/personas/{id}/experience` | GET | Preview image + connect messages |
| `/api/sessions` | POST | Start avatar session |
| `/api/sessions/{id}` | DELETE | End session |
| `/api/knowledge/add` | POST | Add text/URL knowledge |
| `/api/knowledge/upload` | POST | Upload file |
| `/api/knowledge/query` | POST | Test RAG retrieval |
| `/api/knowledge/stats/{persona_id}` | GET | Knowledge chunk count |
| `/api/solutions` | GET | List solution templates |

Full interactive docs: `https://your-url/docs` (Swagger UI)

### Example — Start session

```bash
curl -X POST https://rehu.acme.com/api/sessions \
  -H "Content-Type: application/json" \
  -d '{"persona_id": "hr-interviewer", "visitor_name": "Jane"}'
```

Response includes `livekit_url`, `livekit_client_token`, `opening_text`, `mode`.

---

## Quick Reference — Acme Corp Example

```
1. Deploy  →  https://rehu-acme.up.railway.app
2. Admin     →  Clone hr-interviewer, company = "Acme Corp"
3. Knowledge →  Upload employee handbook + benefits PDF
4. Embed     →  Paste script on acme.com/careers
5. Go-live   →  LIVEAVATAR_USE_SANDBOX=false, CNAME rehu.acme.com
```

---

**Document owner:** SSPM Consultants / Rehu.ai  
**Support:** Configure via `/admin` · API docs at `/docs` · Health at `/health`
