# Savant.ai — Frontend Deployment Guide
### Complete end-to-end: Architecture · Local Dev · Production · CDN · Embedding

---

## 1. How the Frontend Works (Architecture First)

Before deploying, understand the key design decision:

> **There is no separate frontend server.**
> The FastAPI backend serves every HTML page, every CSS file, every JS file, and every
> font/icon/video directly. The frontend is a collection of plain HTML + vanilla JS files
> that live inside the `frontend/` folder. No React. No Next.js. No build step. No `npm`.

```
d:\superhuman\Rehu.Ai\
├── backend/               ← FastAPI (Python) — runs the API AND serves the frontend
│   └── app/
│       └── main.py        ← mounts frontend/ as static, serves HTML routes
└── frontend/              ← all UI lives here, served by the backend
    ├── homepage.html      ← / (marketing landing + Train+Talk studio)
    ├── index.html         ← /call (live avatar conversation screen)
    ├── admin.html         ← /admin (admin dashboard)
    ├── login.html         ← /login
    ├── signup.html        ← /signup
    ├── onboarding.html    ← /onboarding
    ├── pricing.html       ← /pricing
    ├── ride-along.html    ← /ride-along
    ├── solution.html      ← /solutions/{slug}
    ├── assets/
    │   ├── savant-home.css       ← homepage styles
    │   ├── savant-light.css      ← call screen styles
    │   ├── styles.css            ← auth pages styles
    │   ├── call-experience.js    ← persona preview + connect overlay logic
    │   ├── persona-live.js       ← LiveKit/LiveAvatar live call logic
    │   ├── aiza.mp4              ← hero avatar video
    │   └── aiza-hero.mp4         ← hero section background video
    └── sdk/
        └── superhuman-widget.js  ← embeddable widget for customer websites
```

### How FastAPI serves the frontend

In [backend/app/main.py](backend/app/main.py):

```python
# Assets mounted as /static (CSS, JS, images, fonts, videos)
app.mount("/static", StaticFiles(directory="frontend/assets"), name="assets")

# SDK widget mounted at /sdk
app.mount("/sdk", StaticFiles(directory="frontend/sdk"), name="sdk")

# HTML pages served by route handlers
@app.get("/")        → serves frontend/homepage.html
@app.get("/call")    → serves frontend/index.html
@app.get("/admin")   → serves frontend/admin.html  (auth required)
@app.get("/login")   → serves frontend/login.html
@app.get("/signup")  → serves frontend/signup.html
@app.get("/pricing") → serves frontend/pricing.html
```

**Consequence:** deploying the frontend = deploying the backend. They are one unit.

---

## 2. Frontend Pages — What Each Does

| URL | File | Who sees it | Purpose |
|-----|------|-------------|---------|
| `/` | `homepage.html` | Public visitors | Marketing + Train+Talk studio |
| `/call` | `index.html` | Visitors starting a call | Live avatar conversation |
| `/call?persona=hr-interviewer` | `index.html` | Widget embed / direct link | Persona-specific call |
| `/admin` | `admin.html` | Authenticated admin | Manage personas, knowledge, leads |
| `/login` | `login.html` | Tenant users | Sign in → JWT → admin |
| `/signup` | `signup.html` | New tenants | Create workspace |
| `/onboarding` | `onboarding.html` | After signup | First persona setup |
| `/pricing` | `pricing.html` | Public | Plan comparison |
| `/ride-along` | `ride-along.html` | Admin | Meeting bot control |
| `/solutions/{slug}` | `solution.html` | Public | Vertical solution pages |
| `/sdk/superhuman-widget.js` | `sdk/widget.js` | Customer websites | Embeddable widget |
| `/docs` | FastAPI auto | Developers | Swagger API docs |

---

## 3. How the Frontend Calls the Backend

Every page talks to the backend via `fetch()` using **relative URLs** — no hardcoded domains:

```javascript
// homepage.html — train Aiza
fetch('/api/studio/train', { method: 'POST', body: JSON.stringify({...}) })

// index.html — start a live session
fetch('/api/sessions', { method: 'POST', body: JSON.stringify({...}) })

// admin.html — load leads
fetch('/api/leads')

// login.html — authenticate
fetch('/api/auth/login', { method: 'POST', body: JSON.stringify({email, password}) })
```

The widget (`superhuman-widget.js`) is the only file that uses an **absolute URL** —
it reads `data-api="https://your-app.railway.app"` from its own `<script>` tag.

**Auth flow:**

```
POST /api/auth/login → returns { token: "eyJ..." }
  ↓
Stored in localStorage.savant_token  AND  Cookie: savant_token
  ↓
All subsequent API calls: Authorization: Bearer <token>
  ↓
Backend middleware: verify JWT → set active tenant context
```

---

## 4. Local Development

### Prerequisites

- Python 3.11+ (check: `python --version`)
- The `.env` file filled in (copy from `.env.example`)

### Step 1 — Set up Python environment

```powershell
# Windows PowerShell
cd d:\superhuman\Rehu.Ai

# Option A: run the batch file (creates venv automatically)
.\setup_and_run.bat

# Option B: manual
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Step 2 — Configure environment

```powershell
# Copy example to real .env (already done if you have one)
copy .env.example .env
# Edit .env and fill in your API keys
```

Minimum required for the frontend to work locally:

```ini
# .env
OPENAI_API_KEY=sk-...          # needed for AI responses
LIVEAVATAR_API_KEY=...          # needed for avatar video
LIVEAVATAR_USE_SANDBOX=true     # use free Wayne avatar in dev
DEBUG=true                      # allows default secrets in local dev
ADMIN_PASSWORD=anything         # set any password to access /admin
```

### Step 3 — Run the server

```powershell
cd d:\superhuman\Rehu.Ai\backend
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Step 4 — Open in browser

| URL | What you see |
|-----|-------------|
| `http://localhost:8000` | Homepage — Train + Talk studio |
| `http://localhost:8000/call` | Live call screen |
| `http://localhost:8000/admin` | Admin panel (prompted for password) |
| `http://localhost:8000/login` | Sign in page |
| `http://localhost:8000/signup` | Create workspace page |
| `http://localhost:8000/docs` | Swagger API docs |

### Editing the frontend in dev

Because the backend serves the files directly from the `frontend/` folder, **edits are
live immediately** — just refresh the browser. No build step, no hot reload config needed.

```powershell
# Edit any HTML/CSS/JS file
notepad frontend\homepage.html     # or open in VS Code
# Refresh browser — changes appear instantly
```

---

## 5. Production Deployment — Railway (Recommended)

Railway is the simplest path: push to GitHub, Railway reads `railway.json` and
`Dockerfile`, builds a container, and gives you a public HTTPS URL.

### Step 1 — Push your code to GitHub

```powershell
cd d:\superhuman\Rehu.Ai
git add .
git commit -m "ready for production"
git push fork fix/critical-security-hardening
# or push to main if you're ready
```

### Step 2 — Create Railway project

1. Go to [railway.app](https://railway.app) → **New Project**
2. Click **Deploy from GitHub repo**
3. Select `RupakGhosh4865/Rehu.Ai`
4. Railway detects `railway.json` → builds with the `Dockerfile` automatically

### Step 3 — Set environment variables

In Railway dashboard → your service → **Variables** tab, add every variable:

```ini
# ── Required ──────────────────────────────────────────────────────
OPENAI_API_KEY=sk-proj-...
LIVEAVATAR_API_KEY=...
LIVEAVATAR_USE_SANDBOX=false        # false = production avatar
ELEVENLABS_API_KEY=sk_...
DEEPGRAM_API_KEY=...

# ── Security (MUST set — app refuses to boot without these) ───────
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<strong-password>
JWT_SECRET=<48-char random string>
SECRET_KEY=<48-char random string>

# Generate random secrets with:
# python -c "import secrets; print(secrets.token_urlsafe(48))"

# ── App identity ──────────────────────────────────────────────────
APP_BASE_URL=https://your-app.up.railway.app
APP_ROOT_DOMAIN=your-app.up.railway.app   # for tenant subdomains
DEBUG=false

# ── CORS — lock to your domains ───────────────────────────────────
CORS_ORIGINS=["https://your-app.up.railway.app","https://yourclientsite.com"]

# ── Optional integrations ──────────────────────────────────────────
STRIPE_API_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_PILOT=price_...
STRIPE_PRICE_PROFESSIONAL=price_...
STRIPE_PRICE_BUSINESS=price_...

SMARTSHEET_ACCESS_TOKEN=...
SMARTSHEET_SHEET_ID=...

HUBSPOT_CLIENT_ID=...
HUBSPOT_CLIENT_SECRET=...

GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...

# ── Email notifications ────────────────────────────────────────────
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@yourdomain.com
SMTP_PASSWORD=...
SMTP_FROM_EMAIL=aiza@yourdomain.com
SMTP_USE_TLS=true
```

### Step 4 — Deploy

Click **Deploy** in Railway. The build takes 2–3 minutes. Railway:
1. Pulls your repo
2. Runs `docker build` using the root `Dockerfile`
3. Copies `backend/` and `frontend/` into the container
4. Starts: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

### Step 5 — Set your public URL

After deploy, Railway gives you a URL like `https://rehuai-production.up.railway.app`.

Go back to Railway → Variables → update:
```ini
APP_BASE_URL=https://rehuai-production.up.railway.app
```

Redeploy (click **Deploy** again or push a commit).

### Step 6 — Verify it's running

```
https://your-app.up.railway.app/health
```

Should return:
```json
{"status":"healthy","version":"2.0.0","services":{"active_sessions":0,"sandbox_mode":false,"liveavatar_key_set":true}}
```

---

## 6. Production Deployment — Docker (Self-Hosted / VPS)

If you prefer your own server (AWS EC2, DigitalOcean, Hetzner, etc.):

### Build and run

```bash
# On your server or locally to build
git clone https://github.com/RupakGhosh4865/Rehu.Ai.git
cd Rehu.Ai

# Build the image
docker build -t savant-ai .

# Run with your .env file
docker run -d \
  --name savant \
  -p 443:8000 \
  --env-file .env \
  -v savant_data:/app/backend/data \
  --restart unless-stopped \
  savant-ai
```

### With docker-compose

```bash
# Copy and fill in the .env
cp .env.example .env
nano .env   # fill in your keys

# Start
docker-compose up -d --build

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Persistent storage

The data volume `-v savant_data:/app/backend/data` keeps:
- All tenant accounts (`tenants.json`)
- All session transcripts (`sessions/*.json`)
- All knowledge bases (`chromadb/*.json`)
- Notification + compliance settings

**Without this volume, all data is lost on container restart.**

### HTTPS (required for microphone access)

Browsers block microphone access on plain HTTP. You need HTTPS.
Use [Caddy](https://caddyserver.com) as a reverse proxy — it auto-provisions Let's Encrypt:

```
# /etc/caddy/Caddyfile
your-domain.com {
    reverse_proxy localhost:8000
}
```

```bash
sudo caddy reload
```

That's it — HTTPS is live with auto-renewing certificates.

---

## 7. Deploying on a Custom Domain

### Railway custom domain

1. Railway dashboard → your service → **Settings** → **Domains**
2. Click **Add Custom Domain** → type `app.yourdomain.com`
3. Railway shows a CNAME record to add
4. Go to your DNS provider (Cloudflare, Route53, etc.):
   ```
   Type: CNAME
   Name: app
   Value: <what Railway shows>
   TTL: Auto
   ```
5. Wait 5–30 minutes for DNS propagation
6. Update your env vars:
   ```ini
   APP_BASE_URL=https://app.yourdomain.com
   CORS_ORIGINS=["https://app.yourdomain.com"]
   ```

### Multi-tenant subdomains

If you want each customer to have `customer-name.yourdomain.com`:

```ini
APP_ROOT_DOMAIN=yourdomain.com
```

The backend reads the subdomain from the `Host` header and resolves the correct tenant automatically.

DNS setup: add a wildcard CNAME at your DNS provider:
```
Type: CNAME
Name: *
Value: your-app.up.railway.app
```

---

## 8. Embedding the Widget on a Customer Website

The widget (`/sdk/superhuman-widget.js`) is the way customers deploy Aiza on their own sites.

### Basic embed

```html
<!-- Add before </body> on the customer's website -->
<script
  src="https://your-app.up.railway.app/sdk/superhuman-widget.js"
  data-persona="default"
  data-position="bottom-right"
  data-color="#0EA5A4"
  data-label="Talk to our Expert"
  data-api="https://your-app.up.railway.app">
</script>
```

### All widget options

```html
<script
  src="https://your-app.up.railway.app/sdk/superhuman-widget.js"

  <!-- Core -->
  data-api="https://your-app.up.railway.app"
  data-persona="default"              <!-- persona ID from admin panel -->
  data-position="bottom-right"        <!-- bottom-right | bottom-left | bottom-center -->
  data-color="#0EA5A4"                <!-- bubble and button colour -->
  data-label="Talk to Aiza"           <!-- button text -->

  <!-- Auto-open triggers (pick one or combine) -->
  data-trigger="auto"
  data-trigger-delay="30"             <!-- open after 30 seconds on page -->
  data-trigger-scroll="50"            <!-- open after 50% page scroll -->
  data-trigger-exit="true"            <!-- open on exit intent (mouse leaves viewport) -->
  data-trigger-idle="60"              <!-- open after 60 seconds of inactivity -->
  data-trigger-selector=".upgrade-btn" <!-- open when visitor hovers this CSS selector -->

  <!-- User context (passed to AI as system prompt) -->
  data-user-id="usr_abc123"
  data-user-plan="pro"
  data-user-stage="trial"
  data-page-context="Pricing page"
  data-lang="en"

  <!-- Custom opening message -->
  data-greeting="Hi! I noticed you're on the pricing page — want me to walk you through the plans?">
</script>
```

### JavaScript API (for in-product triggers)

```javascript
// After the script tag loads, these are available:
window.Savant.open();                       // open the chat
window.Savant.close();                      // close the chat
window.Savant.toggle();                     // toggle open/close
window.Savant.openWithGreeting("Hi there!"); // open with a specific message

// window.SuperHuman works too (legacy alias)
```

### Programmatic example — open on button click

```html
<button onclick="window.Savant.openWithGreeting('Tell me about enterprise pricing')">
  Talk to Sales
</button>
```

### Get the embed code automatically

Go to `/admin` → **Embed Widget** tab → fill in your options → copy the generated `<script>` tag. No manual coding needed.

---

## 9. Frontend File Reference

### Pages

| File | Route | Auth required | Key JS dependencies |
|------|-------|--------------|---------------------|
| `homepage.html` | `/` | No | None (vanilla fetch) |
| `index.html` | `/call` | No | `livekit-client` (CDN), `persona-live.js`, `call-experience.js` |
| `admin.html` | `/admin` | Yes (HTTP Basic) | None (vanilla fetch with JWT) |
| `login.html` | `/login` | No | None |
| `signup.html` | `/signup` | No | None |
| `onboarding.html` | `/onboarding` | Yes (JWT) | None |
| `pricing.html` | `/pricing` | No | None |
| `ride-along.html` | `/ride-along` | Yes (HTTP Basic) | None |
| `solution.html` | `/solutions/:slug` | No | None |

### Static assets (`/static/...`)

| File | Purpose |
|------|---------|
| `savant-home.css` | Homepage styles |
| `savant-light.css` | Call screen styles |
| `styles.css` | Auth pages (login/signup) |
| `call-experience.js` | Persona preview image, connect overlay, fade transitions |
| `persona-live.js` | LiveKit room connection, avatar video display |
| `savant-logo.svg` | Nav bar logo |
| `savant-icon.svg` | Browser tab favicon |
| `aiza.mp4` | Idle avatar video (homepage hero) |
| `aiza-hero.mp4` | Hero background video |

### SDK (`/sdk/...`)

| File | Purpose |
|------|---------|
| `superhuman-widget.js` | Self-contained embeddable widget — injects iframe, bubble button, all CSS |

---

## 10. Making Frontend Changes

Since there is no build system, editing the frontend is direct file editing.

### Change the brand name / logo

1. Replace `frontend/assets/savant-logo.svg` with your logo SVG
2. Replace `frontend/assets/savant-icon.svg` with your favicon
3. Search and replace "Savant.ai" in `homepage.html`, `index.html`, `admin.html`

```powershell
# Find all occurrences to replace
grep -r "Savant.ai" frontend/ --include="*.html"
```

### Change the default persona name ("Aiza")

Edit [backend/app/config.py](backend/app/config.py):
```python
DEFAULT_PERSONA_NAME: str = "Aiza"   # change this
```

Or update it per-persona in the Admin panel → Personas → Edit.

### Change the brand colour

The teal `#0EA5A4` appears in:
- `frontend/assets/savant-home.css` — homepage
- `frontend/assets/savant-light.css` — call screen
- `frontend/sdk/superhuman-widget.js` — widget default (overridden by `data-color`)

### Change the hero video

Replace `frontend/assets/aiza.mp4` and `frontend/assets/aiza-hero.mp4` with your own
MP4 files (keep the same filenames or update the `src` attributes in `homepage.html`).

### Add a new page

1. Create `frontend/yournewpage.html`
2. Add a route in `backend/app/main.py`:
   ```python
   @app.get("/yourpage")
   async def your_page():
       p = os.path.join(FRONTEND_DIR, "yournewpage.html")
       return FileResponse(p) if os.path.isfile(p) else HTTPException(404)
   ```
3. Restart the server — the page is live at `/yourpage`

---

## 11. Troubleshooting

### "Module not found" on startup

```powershell
cd d:\superhuman\Rehu.Ai\backend
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### "REFUSING to start with insecure configuration"

The app found empty `ADMIN_PASSWORD` or default `JWT_SECRET` with `DEBUG=false`.

**Fix for local dev:** add `DEBUG=true` to `.env`

**Fix for production:** set real values:
```ini
ADMIN_PASSWORD=your-strong-password
JWT_SECRET=<run: python -c "import secrets; print(secrets.token_urlsafe(48))">
SECRET_KEY=<same command>
DEBUG=false
```

### Admin panel asks for password but I don't know it

Check your `.env` file for `ADMIN_PASSWORD`. If it's empty, set one and restart.

### Avatar video doesn't appear / black screen

- Check `LIVEAVATAR_API_KEY` is set in `.env`
- Check `LIVEAVATAR_USE_SANDBOX=true` for dev (uses free Wayne avatar)
- Open browser DevTools → Console for WebRTC errors
- Check `/health` endpoint to confirm `liveavatar_key_set: true`

### Microphone doesn't work

Browsers require HTTPS for microphone access. On localhost this is fine.
On a server: set up HTTPS via Caddy or a reverse proxy before going live.

### "Too many requests" (429 error)

The rate limiter is active. Limits per IP:
- Session creation: 30 per minute
- Studio train: 10 per minute
- Lead capture: 20 per minute
- Login/signup: 15 per minute

Wait 60 seconds, then try again.

### Widget doesn't open on customer site

1. Check the `data-api` URL — must match exactly where the backend is running
2. Check CORS: `CORS_ORIGINS` in `.env` must include the customer's domain
3. Check browser console for CORS errors or 4xx responses

### Sessions lost after server restart

Expected behaviour currently — sessions live in memory. To persist across restarts,
a Redis-backed session store is needed (see roadmap). For now, ensure the data volume
is mounted so at least transcripts and leads survive.

---

## 12. Deployment Checklist

Before going live with real customers, verify every item:

### Security
- [ ] `ADMIN_PASSWORD` set to a strong password (not empty)
- [ ] `JWT_SECRET` set to a random 48-char string (not the default)
- [ ] `SECRET_KEY` set to a random 48-char string
- [ ] `DEBUG=false`
- [ ] `CORS_ORIGINS` locked to your actual domains (not `["*"]`)
- [ ] `LIVEAVATAR_USE_SANDBOX=false` for production avatar
- [ ] All API keys rotated and confirmed working
- [ ] HTTPS configured (browser requires it for microphone)

### Functionality
- [ ] `/health` returns `{"status":"healthy"}`
- [ ] Homepage loads at `/`
- [ ] "Talk to Aiza" button starts a session successfully
- [ ] Admin panel loads at `/admin` with your password
- [ ] Knowledge upload works (upload a PDF, query it)
- [ ] Lead capture fires (session ends → email received)
- [ ] Widget embed works on a test page

### Infrastructure
- [ ] Data volume mounted (`/app/backend/data`) — without this all data is lost on restart
- [ ] Railway volume OR Docker `-v` flag configured
- [ ] Healthcheck passing in Railway dashboard (green dot)
- [ ] Custom domain configured and HTTPS working

---

## 13. Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│  DEPLOYMENT = just run the backend                                  │
│                                                                     │
│  Local dev:   uvicorn app.main:app --reload --port 8000             │
│  Docker:      docker-compose up --build                             │
│  Railway:     git push → auto-deploy via Dockerfile                 │
│                                                                     │
│  All HTML/CSS/JS in frontend/ is served by the backend              │
│  No build step · No npm · No separate frontend server               │
│                                                                     │
│  To embed on a customer site:                                       │
│    <script src="https://yourapp.railway.app/sdk/superhuman-widget.js│
│      data-api="https://yourapp.railway.app"                         │
│      data-persona="default">                                        │
│    </script>                                                        │
└─────────────────────────────────────────────────────────────────────┘
```

Built by SSPM Consultants | Savant.ai
