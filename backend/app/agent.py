"""
Savant.ai -- Voice Pipeline & Opening Pitch Generator

In LiveAvatar (FULL) mode:  generate_opening_pitch() feeds the LiveAvatar context.
In voice-only fallback mode: handle_voice_session() runs a WebSocket voice loop.
"""
import asyncio
import base64
import json
import logging
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect
from openai import AsyncOpenAI
from deepgram import AsyncDeepgramClient
from elevenlabs import AsyncElevenLabs, VoiceSettings

from .config import settings
from .knowledge import query_knowledge

logger = logging.getLogger(__name__)

_active_sessions: dict[str, dict] = {}


def list_active_sessions() -> list:
    return list(_active_sessions.keys())


async def stop_voice_pipeline(session_id: str) -> bool:
    session = _active_sessions.pop(session_id, None)
    if session and session.get("task"):
        session["task"].cancel()
    return bool(session)


# ── Prompt builders ───────────────────────────────────────────────────────────

def build_system_prompt(
    persona_name: str, company_name: str,
    knowledge_context: str, tone: str = "professional",
    prompt_override: Optional[str] = None,
    language: str = "en",
    calendly_url: Optional[str] = None,
    user_context: Optional[str] = None,
) -> str:
    from .languages import language_name, normalize_language
    lang_code = normalize_language(language)
    lang_label = language_name(lang_code)
    lang_instruction = (
        f"Respond entirely in {lang_label}. Do not switch languages unless the visitor explicitly asks."
    )
    lead_instruction = (
        "After initial rapport, if you do not know the visitor's name or email, ask naturally for both "
        "so the team can follow up. When they provide details, confirm spelling and thank them."
    )
    booking_instruction = ""
    if calendly_url:
        booking_instruction = (
            f"If the visitor wants to book a meeting, demo, or follow-up call, share this booking link: "
            f"{calendly_url}. Say something natural like: 'Great — you can pick a time that works here: "
            f"{calendly_url}'. Confirm their email so the team can join."
        )
    user_ctx_block = f"User context: {user_context}" if user_context else ""
    if prompt_override:
        base = (
            f"{prompt_override}\n\n"
            f"Current knowledge about {company_name}:\n"
            f"{knowledge_context or 'No documents uploaded yet — use general expertise and ask clarifying questions.'}"
        )
        extras = "\n".join(x for x in (user_ctx_block, lang_instruction, lead_instruction, booking_instruction) if x)
        return f"{base}\n\n{extras}" if extras else base
    tone_map = {
        "professional": "You are polished, confident, and knowledgeable — like a top sales executive.",
        "friendly":     "You are warm, enthusiastic, and personable. Like a trusted friend who is an expert.",
        "casual":       "You are relaxed, natural, and conversational. No corporate speak.",
        "formal":       "You are precise, measured, and authoritative.",
    }
    booking_block = f"{booking_instruction}\n\n" if booking_instruction else ""
    user_ctx_line = f"{user_ctx_block}\n\n" if user_ctx_block else ""
    return (
        f"You are {persona_name}, a human expert representing {company_name}. "
        f"You have memorized every product, feature, price, and use case for {company_name}. "
        f"Speak naturally out loud — short punchy sentences, 2-3 max per response. "
        f"Use the visitor's name if you know it. Be direct, confident, never robotic. "
        f"Never say you are an AI, a bot, or an assistant unless directly asked. "
        f"{tone_map.get(tone, tone_map['professional'])}\n\n"
        f"{user_ctx_line}"
        f"{lead_instruction}\n\n"
        f"{booking_block}"
        f"{lang_instruction + chr(10) + chr(10) if lang_instruction else ''}"
        f"Your knowledge about {company_name}:\n{knowledge_context or 'Ready to help — ask me anything!'}"
    )


async def generate_opening_pitch(
    persona_name: str,
    company_name: str,
    knowledge_context: str,
    visitor_name: Optional[str] = None,
    role_hint: str = "sales",
    opening_fallback: Optional[str] = None,
    language: str = "en",
) -> str:
    """
    Generate a compelling 60-second spoken auto-demo pitch from the knowledge base.
    Used as opening_text in LiveAvatar context — avatar speaks this the moment it appears.
    """
    if not settings.OPENAI_API_KEY:
        if opening_fallback:
            addr = f"Hi {visitor_name}! " if visitor_name else ""
            return f"{addr}{opening_fallback}" if not visitor_name else opening_fallback.replace("Hi,", f"Hi {visitor_name},")
        addr = f"Hi {visitor_name}! " if visitor_name else "Hi there! "
        return f"{addr}I'm {persona_name} from {company_name}. What would you like to know?"

    address = f"Hi {visitor_name}!" if visitor_name else "Hi there!"
    role_instructions = {
        "hr": (
            f"Write a spoken HR screening opener (80-100 words) for {persona_name} at {company_name}.\n"
            f"1. Opens with '{address} I'm {persona_name} from the talent team...'\n"
            f"2. Sets expectations for a brief structured interview\n"
            f"3. Asks if they're ready to begin\n"
            f"Professional, welcoming, no sales pitch."
        ),
        "onboarding": (
            f"Write a spoken onboarding welcome (80-100 words) for buddy {persona_name} at {company_name}.\n"
            f"1. Opens with '{address} I'm {persona_name}, your onboarding guide...'\n"
            f"2. Mentions you help with tools, policies, and first-week tasks\n"
            f"3. Invites them to ask anything\n"
            f"Warm, encouraging tone."
        ),
        "support": (
            f"Write a spoken support greeting (60-80 words) for {persona_name} at {company_name}.\n"
            f"1. Opens with '{address} I'm {persona_name} from support...'\n"
            f"2. Offers to help resolve their issue\n"
            f"Empathetic, efficient."
        ),
        "demo": (
            f"Write a spoken product demo intro (100-120 words) for {persona_name} at {company_name}.\n"
            f"1. Opens with '{address} I'm {persona_name}...'\n"
            f"2. Brief value prop from knowledge below\n"
            f"3. Invites questions during the walkthrough"
        ),
    }
    instructions = role_instructions.get(
        role_hint,
        (
            f"Write a compelling spoken pitch (120-150 words, ~60 seconds when spoken) that:\n"
            f"1. Opens with '{address} I'm {persona_name}...' — warm and human\n"
            f"2. States what {company_name} does and who it helps\n"
            f"3. Highlights 2-3 specific standout features or benefits with real specifics\n"
            f"4. Creates genuine excitement — your best rep on their best day\n"
            f"5. Ends with an open invitation like 'What would you like to know more about?'"
        ),
    )
    from .languages import language_name, normalize_language
    lang_label = language_name(normalize_language(language))
    prompt = (
        f"You are {persona_name} representing {company_name}.\n"
        f"Based on this knowledge:\n{knowledge_context[:2500]}\n\n"
        f"{instructions}\n\n"
        f"Write the response entirely in {lang_label}. Do not use any other language.\n"
        f"ONLY the spoken words. No stage directions. Natural speech rhythm."
    )

    try:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        resp = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.8,
        )
        text = resp.choices[0].message.content
        return text or (opening_fallback or f"Hi! I'm {persona_name} from {company_name}. How can I help?")
    except Exception as e:
        logger.error("Opening pitch generation failed: %s", e)
        return opening_fallback or f"Hi! I'm {persona_name}, your expert at {company_name}. I'm here to answer any question you have!"


# ── Voice-only WebSocket fallback ─────────────────────────────────────────────

async def handle_voice_session(
    websocket: WebSocket,
    session_id: str,
    persona_id: str = "default",
    persona_name: str = "Aiza",
    company_name: str = "our company",
    tone: str = "professional",
    prompt_override: Optional[str] = None,
    heygen_session_id: Optional[str] = None,
    visitor_name: Optional[str] = None,
    opening_text: str = "",
    language: str = "en",
):
    openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    el_client     = AsyncElevenLabs(api_key=settings.ELEVENLABS_API_KEY)
    dg_client     = AsyncDeepgramClient(api_key=settings.DEEPGRAM_API_KEY)

    conversation_history = []
    _active_sessions[session_id] = {"websocket": websocket, "persona_id": persona_id, "language": language}

    async def send(msg: dict):
        try:
            await websocket.send_text(json.dumps(msg))
        except Exception:
            pass

    knowledge_ctx = await query_knowledge(persona_id, "overview products services features")
    system_prompt = build_system_prompt(
        persona_name, company_name, knowledge_ctx, tone,
        prompt_override=prompt_override, language=language,
    )
    conversation_history.append({"role": "system", "content": system_prompt})

    await send({"type": "status", "message": "connected"})

    try:
        while True:
            raw      = await websocket.receive_text()
            msg      = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "end":
                break

            elif msg_type == "start_demo":
                intro = opening_text or f"Hi! I'm {persona_name} from {company_name}. How can I help you today?"
                conversation_history.append({"role": "assistant", "content": intro})
                await send({"type": "transcript", "role": "assistant", "text": intro})
                await send({"type": "status", "message": "speaking"})
                await _speak(el_client, intro, send)
                await send({"type": "status", "message": "listening"})

            elif msg_type == "text":
                user_text = msg.get("text", "").strip()
                if user_text:
                    await _process_input(user_text, openai_client, el_client, conversation_history,
                                        persona_name, company_name, tone, persona_id, send,
                                        prompt_override=prompt_override, language=language)

            elif msg_type == "language":
                from .languages import language_name, normalize_language
                language = normalize_language(msg.get("language"))
                if session_id in _active_sessions:
                    _active_sessions[session_id]["language"] = language
                conversation_history[0]["content"] = build_system_prompt(
                    persona_name, company_name, knowledge_ctx, tone,
                    prompt_override=prompt_override, language=language,
                )
                await send({
                    "type": "status",
                    "message": f"language:{language_name(language)}",
                    "language": language,
                })

            elif msg_type == "audio":
                audio_data = base64.b64decode(msg.get("data", ""))
                if len(audio_data) >= 500:
                    transcript = await _transcribe(dg_client, audio_data, language=language)
                    if transcript and len(transcript.strip()) > 2:
                        await _process_input(transcript, openai_client, el_client, conversation_history,
                                            persona_name, company_name, tone, persona_id, send,
                                            prompt_override=prompt_override, language=language)

    except WebSocketDisconnect:
        logger.info("Voice session %s disconnected", session_id)
    except Exception as e:
        logger.error("Voice session %s error: %s", session_id, e)
    finally:
        _active_sessions.pop(session_id, None)


async def _process_input(user_text, openai_client, el_client, history,
                         persona_name, company_name, tone, persona_id, send,
                         prompt_override: Optional[str] = None, language: str = "en"):
    await send({"type": "transcript", "role": "user", "text": user_text})
    await send({"type": "status", "message": "thinking"})
    ctx = await query_knowledge(persona_id, user_text)
    history[0]["content"] = build_system_prompt(
        persona_name, company_name, ctx, tone, prompt_override=prompt_override, language=language,
    )
    history.append({"role": "user", "content": user_text})
    response = await _llm(openai_client, history)
    history.append({"role": "assistant", "content": response})
    await send({"type": "transcript", "role": "assistant", "text": response})
    await send({"type": "status", "message": "speaking"})
    await _speak(el_client, response, send)
    await send({"type": "status", "message": "listening"})


async def _llm(client: AsyncOpenAI, messages: list) -> str:
    try:
        r = await client.chat.completions.create(
            model=settings.OPENAI_MODEL, messages=messages,
            max_tokens=settings.OPENAI_MAX_TOKENS, temperature=settings.OPENAI_TEMPERATURE,
        )
        return r.choices[0].message.content or "Could you say that again?"
    except Exception as e:
        logger.error("OpenAI error: %s", e)
        return "Give me just a second — could you repeat that?"


async def _speak(el_client: AsyncElevenLabs, text: str, send):
    try:
        chunks = []
        async for chunk in await el_client.text_to_speech.convert(
            voice_id=settings.ELEVENLABS_VOICE_ID, text=text,
            model_id=settings.ELEVENLABS_MODEL_ID,
            voice_settings=VoiceSettings(stability=0.45, similarity_boost=0.82,
                                         style=0.35, use_speaker_boost=True),
            output_format="mp3_44100_128",
        ):
            if chunk:
                chunks.append(chunk)
        if chunks:
            await send({"type": "audio", "data": base64.b64encode(b"".join(chunks)).decode(), "format": "mp3"})
    except Exception as e:
        logger.error("ElevenLabs TTS error: %s", e)


async def _transcribe(dg_client: AsyncDeepgramClient, audio_bytes: bytes, language: str = "en") -> Optional[str]:
    from .languages import deepgram_language
    try:
        r = await dg_client.listen.v1.media.transcribe_file(
            request=audio_bytes, model=settings.DEEPGRAM_MODEL,
            language=deepgram_language(language), smart_format=True,
        )
        t = r.results.channels[0].alternatives[0].transcript
        return t.strip() if t else None
    except Exception as e:
        logger.error("Deepgram STT error: %s", e)
        return None
