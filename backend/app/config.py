"""
Savant.ai -- Configuration
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    APP_NAME:    str  = "Savant.ai"
    APP_VERSION: str  = "2.0.0"
    DEBUG:       bool = False
    HOST:        str  = "0.0.0.0"
    PORT:        int  = 8000
    SECRET_KEY:  str  = "change-me-in-production"
    CORS_ORIGINS: List[str] = ["*"]

    # ── Enterprise / security ─────────────────────────────────────────────────
    ENVIRONMENT:        str  = "development"   # set to "production" to enforce hardening
    FORCE_HTTPS:        bool = False           # emit HSTS + upgrade headers (enable behind TLS)
    RATE_LIMIT_ENABLED: bool = True
    AUDIT_LOG_ENABLED:  bool = True

    # ── Rate limiting ──────────────────────────────────────────────────────────
    # Single source of truth lives in security.py. Each value is "max_requests/window_seconds".
    # Override per-env to tune. REDIS_URL backs the limiter across workers; without
    # it the limiter falls back to in-memory (single-process only).
    RATE_LIMIT_AUTH:      str = "20/300"   # /api/auth/* — brute-force / signup abuse
    RATE_LIMIT_SESSIONS:  str = "40/300"   # /api/sessions — expensive avatar provisioning
    RATE_LIMIT_KNOWLEDGE: str = "60/300"   # /api/knowledge/* — ingestion abuse
    RATE_LIMIT_LEADS:     str = "30/60"    # /api/leads — public inbound form spam
    RATE_LIMIT_DEFAULT:   str = "120/60"   # any other mutating API call
    REDIS_URL:            str = ""          # e.g. redis://localhost:6379/0 (blank = in-memory)

    # ── Durable session state ───────────────────────────────────────────────────
    # Live session state lives in Redis (when REDIS_URL is set) so we can run >1
    # worker and survive restarts; blank REDIS_URL = in-memory (single worker, dev).
    # A session's Redis key TTL = idle_timeout + this margin, so the idle-kill
    # sweep has a grace window before the key disappears.
    SESSION_TTL_MARGIN_SECONDS: int = 120
    # The orphan-recovery sweeper interval (bills crashed sessions, reclaims streams).
    SESSION_SWEEP_INTERVAL_SECONDS: int = 30

    # ── Database ──────────────────────────────────────────────────────────────
    # Empty -> SQLite file at ./data/savant.db (dev). In production set to a
    # Postgres URL, e.g. postgresql+psycopg2://user:pass@host:5432/savant
    DATABASE_URL: str = ""

    # ── Observability ─────────────────────────────────────────────────────────
    SENTRY_DSN: str = ""   # set to enable error monitoring (sentry-sdk)

    # Admin panel HTTP Basic Auth (leave ADMIN_PASSWORD empty to disable in dev)
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = ""

    # ── LiveAvatar (NEW HeyGen API) ────────────────────────────────────────────
    # Get your key from: https://app.liveavatar.com/settings/api
    LIVEAVATAR_API_KEY:   str  = ""
    LIVEAVATAR_API_BASE:  str  = "https://api.liveavatar.com"
    LIVEAVATAR_AVATAR_ID: str  = ""          # Leave blank to use Wayne in sandbox
    LIVEAVATAR_VOICE_ID:  str  = ""          # Leave blank to use avatar default voice
    LIVEAVATAR_USE_SANDBOX: bool = True      # True = free Wayne avatar, ~1 min sessions

    # ── Expressiveness (avatar_persona.voice_settings, ElevenLabs-backed) ──────
    # LiveAvatar defaults to style=0 (flat delivery) and the latency-optimized
    # flash model. These defaults trade ~300ms of latency for audibly more
    # human prosody. Tune by ear via env vars — no deploy needed.
    # Accent voices: locale -> LiveAvatar voice_id (JSON). These are ElevenLabs
    # voices imported via /v1/voices/third_party. en-US intentionally absent —
    # it falls through to LIVEAVATAR_VOICE_ID (June).
    LIVEAVATAR_VOICE_BY_LOCALE: str = ('{"en-GB": "21610320-29b1-4a7a-8b58-dd0e0a9df036", '
                                       '"en-IN": "bd46be4f-3b51-4fb0-b925-c0e1dbe2cbae"}')

    # Voice audition: named candidates reachable via /call?voice=<name> for live
    # A/B listening. Allowlist by design — arbitrary voice ids are rejected.
    LIVEAVATAR_VOICE_CANDIDATES: str = ('{"lauren": "6ec7f74c-2bd3-487a-8526-ca58e2992582", '
                                        '"hope": "1e26fb62-9339-445f-90d4-b227e71ee1be", '
                                        '"tarini": "5c76d1ff-4757-4b5f-a055-a4185bd3627e"}')

    LIVEAVATAR_VOICE_MODEL:         str   = "eleven_multilingual_v2"
    LIVEAVATAR_VOICE_STYLE:         float = 0.5   # 0 = flat … 1 = theatrical
    LIVEAVATAR_VOICE_STABILITY:     float = 0.5   # lower = more dynamic intonation
    LIVEAVATAR_VOICE_SIMILARITY:    float = 0.75
    LIVEAVATAR_VOICE_SPEED:         float = 1.0   # API clamps to 0.8–1.2
    LIVEAVATAR_VOICE_SPEAKER_BOOST: bool  = True

    # ── Avatar metering & cost-control ──────────────────────────────────────────
    # Avatar streaming is ~10x the cost of text/voice, billed for the whole session
    # incl. silence. These guardrails stop a runaway/abusive call burning money.
    AVATAR_IDLE_TIMEOUT_SECONDS: int   = 60     # auto-end an avatar call after this much silence
    AVATAR_CAP_FALLBACK:        str    = "chat"   # "block" | "chat" — pool used up: refuse calls, or degrade to text chat (Aiza keeps selling)
    AVATAR_COST_PER_MIN_INR:    float  = 38.0   # planning cost/min; powers spend estimates & alerts
    USAGE_ALERT_THRESHOLDS:     str    = "0.8,1.0"  # fire usage alerts at these fractions of the cap

    # The workspace that powers the public homepage "Talk to Aiza" demo (slot=hero).
    # Set to a tenant_id so that tenant's dashboard (knowledge, slides, persona)
    # controls the demo and homepage leads land in their dashboard. Blank = default.
    DEMO_TENANT_ID:             str    = ""

    # ── OpenAI ────────────────────────────────────────────────────────────────
    OPENAI_API_KEY:      str   = ""
    OPENAI_MODEL:        str   = "gpt-4o-mini"
    OPENAI_TEMPERATURE:  float = 0.75
    OPENAI_MAX_TOKENS:   int   = 512

    # ── Conversation intelligence (post-session transcript analysis) ──────────
    CONVERSATION_INTELLIGENCE_ENABLED: bool = True
    CONVERSATION_INTELLIGENCE_MODEL:   str  = ""   # blank = OPENAI_MODEL

    # ── ElevenLabs (kept for voice-only fallback) ──────────────────────────────
    ELEVENLABS_API_KEY:  str = ""
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"   # Rachel
    ELEVENLABS_MODEL_ID: str = "eleven_turbo_v2_5"

    # ── Deepgram (kept for voice-only fallback) ────────────────────────────────
    DEEPGRAM_API_KEY: str = ""
    DEEPGRAM_MODEL:   str = "nova-3"
    DEEPGRAM_LANGUAGE: str = "en-US"

    # ── Knowledge Base ────────────────────────────────────────────────────────
    CHROMA_PERSIST_DIR:  str = "./data/chromadb"
    EMBEDDING_MODEL:     str = "all-MiniLM-L6-v2"
    RAG_TOP_K:           int = 5
    RAG_MAX_CONTEXT_CHARS: int = 3000
    # Semantic (vector) search. When RAG_USE_SEMANTIC is on AND an OpenAI key is
    # present, retrieval fuses BM25 keyword scores with embedding similarity
    # (hybrid search). With no key it transparently falls back to BM25 only.
    RAG_USE_SEMANTIC:       bool = True
    OPENAI_EMBEDDING_MODEL: str  = "text-embedding-3-small"
    RAG_RRF_K:              int  = 60     # Reciprocal Rank Fusion constant

    # Operational alerts (credit exhaustion etc.) go here via SMTP when set up.
    ALERT_EMAIL: str = "info@sspmconsultants.com"

    # ── Notifications (SMTP for outbound email) ───────────────────────────────
    SMTP_HOST:        str  = ""
    SMTP_PORT:        int  = 587
    SMTP_USERNAME:    str  = ""
    SMTP_PASSWORD:    str  = ""
    SMTP_USE_TLS:     bool = True
    SMTP_USE_SSL:     bool = False
    SMTP_FROM_EMAIL:  str  = ""

    # ── Stripe Billing ────────────────────────────────────────────────────────
    STRIPE_API_KEY:        str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    # Public plans we actually sell self-serve (see tenants.PLAN_DEFAULTS).
    STRIPE_PRICE_GROWTH:       str = ""
    STRIPE_PRICE_SCALE:        str = ""
    STRIPE_PRICE_ENTERPRISE:   str = ""
    # Legacy aliases — kept so older Stripe products / tenant rows keep mapping.
    STRIPE_PRICE_PILOT:        str = ""
    STRIPE_PRICE_PROFESSIONAL: str = ""
    STRIPE_PRICE_BUSINESS:     str = ""

    # ── Multi-tenant ──────────────────────────────────────────────────────────
    APP_BASE_URL:     str  = "http://localhost:8000"
    APP_ROOT_DOMAIN:  str  = ""           # e.g. savant.ai (enables tenant subdomains)
    JWT_SECRET:       str  = "change-me-jwt-secret"
    JWT_TTL_HOURS:    int  = 168          # 7 days

    # ── HubSpot integration ────────────────────────────────────────────────────
    HUBSPOT_CLIENT_ID:     str = ""
    HUBSPOT_CLIENT_SECRET: str = ""
    HUBSPOT_REDIRECT_URI:  str = ""

    # ── Smartsheet CRM (append leads as rows) ─────────────────────────────────
    SMARTSHEET_ACCESS_TOKEN: str = ""
    SMARTSHEET_SHEET_ID:     str = ""

    # ── Google Calendar integration ────────────────────────────────────────────
    GOOGLE_CLIENT_ID:     str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI:  str = ""

    # ── Persona Defaults ──────────────────────────────────────────────────────
    DEFAULT_PERSONA_NAME: str = "Aiza"
    DEFAULT_SYSTEM_PROMPT: str = (
        "You are {persona_name}, a human expert representing {company_name}. "
        "You have memorized every product, feature, use case, and benefit. "
        "Speak naturally -- short punchy sentences, maximum 2-3 per response. "
        "Be warm, confident, and direct. Never say you are an AI unless asked. "
        "{user_context}"
        "Context from the knowledge base:\n\n{knowledge_context}"
    )


settings = Settings()
