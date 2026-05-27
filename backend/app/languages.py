"""
Curated language support — quality-controlled set for LiveAvatar + Deepgram fallback.
"""
from typing import Optional

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "zh": "Chinese",
    "ar": "Arabic",
    "es": "Spanish",
    "fr": "French",
    "hi": "Hindi",
    "te": "Telugu",
    "ja": "Japanese",
    "de": "German",
    "pt": "Portuguese",
}

DEEPGRAM_LANGUAGE_MAP: dict[str, str] = {
    "en": "en-US",
    "zh": "zh-CN",
    "ar": "ar",
    "es": "es",
    "fr": "fr",
    "hi": "hi",
    "te": "te",
    "ja": "ja",
    "de": "de",
    "pt": "pt-BR",
}


def language_name(code: str) -> str:
    return SUPPORTED_LANGUAGES.get(code, "English")


def deepgram_language(code: str) -> str:
    return DEEPGRAM_LANGUAGE_MAP.get(code, "en-US")


def normalize_language(code: Optional[str]) -> str:
    if not code:
        return "en"
    code = code.lower().split("-")[0]
    return code if code in SUPPORTED_LANGUAGES else "en"
