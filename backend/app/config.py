"""
SuperHuman AI Persona Platform -- Configuration
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    APP_NAME:    str  = "SuperHuman AI Persona Platform"
    APP_VERSION: str  = "2.0.0"
    DEBUG:       bool = False
    HOST:        str  = "0.0.0.0"
    PORT:        int  = 8000
    SECRET_KEY:  str  = "change-me-in-production"
    CORS_ORIGINS: List[str] = ["*"]

    # ── LiveAvatar (NEW HeyGen API) ────────────────────────────────────────────
    # Get your key from: https://app.liveavatar.com/settings/api
    LIVEAVATAR_API_KEY:   str  = ""
    LIVEAVATAR_API_BASE:  str  = "https://api.liveavatar.com"
    LIVEAVATAR_AVATAR_ID: str  = ""          # Leave blank to use Wayne in sandbox
    LIVEAVATAR_VOICE_ID:  str  = ""          # Leave blank to use avatar default voice
    LIVEAVATAR_USE_SANDBOX: bool = True      # True = free Wayne avatar, ~1 min sessions

    # ── OpenAI ────────────────────────────────────────────────────────────────
    OPENAI_API_KEY:      str   = ""
    OPENAI_MODEL:        str   = "gpt-4o-mini"
    OPENAI_TEMPERATURE:  float = 0.75
    OPENAI_MAX_TOKENS:   int   = 512

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

    # ── Persona Defaults ──────────────────────────────────────────────────────
    DEFAULT_PERSONA_NAME: str = "Maya"
    DEFAULT_SYSTEM_PROMPT: str = (
        "You are {persona_name}, a human expert representing {company_name}. "
        "You have memorized every product, feature, use case, and benefit. "
        "Speak naturally -- short punchy sentences, maximum 2-3 per response. "
        "Be warm, confident, and direct. Never say you are an AI unless asked. "
        "Context from the knowledge base:\n\n{knowledge_context}"
    )


settings = Settings()
