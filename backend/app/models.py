"""
SuperHuman AI Persona Platform -- Pydantic Models (API Request/Response schemas)
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum


# -- Persona -------------------------------------------------------------------

class PersonaTone(str, Enum):
    professional = "professional"
    friendly = "friendly"
    casual = "casual"
    formal = "formal"


class PersonaConfig(BaseModel):
    persona_id: str = Field(default="default", description="Unique persona identifier")
    persona_name: str = Field(default="Alex", description="The persona display name")
    company_name: str = Field(default="our company", description="Company the persona represents")
    tone: PersonaTone = PersonaTone.professional
    avatar_id: Optional[str] = Field(None, description="HeyGen avatar ID override")
    voice_id: Optional[str] = Field(None, description="ElevenLabs voice ID override")
    system_prompt_override: Optional[str] = Field(None, description="Custom system prompt")


# -- Session ------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    persona_id: str = Field(default="default")
    visitor_name: Optional[str] = Field(None, description="Name of the website visitor")
    visitor_email: Optional[str] = Field(None)
    context: Optional[str] = Field(None, description="Optional extra context for this session")


class SessionResponse(BaseModel):
    session_id: str
    heygen_session_id: str
    heygen_access_token: str
    heygen_ice_servers: List[dict]
    persona_name: str
    avatar_id: str


class EndSessionRequest(BaseModel):
    session_id: str
    heygen_session_id: str


# -- Knowledge Base -----------------------------------------------------------

class KnowledgeSourceType(str, Enum):
    text = "text"
    url = "url"
    file = "file"


class AddKnowledgeRequest(BaseModel):
    persona_id: str = Field(default="default")
    source_type: KnowledgeSourceType = KnowledgeSourceType.text
    content: str = Field(description="Raw text, URL, or file path")
    title: Optional[str] = Field(None, description="Optional label for this knowledge chunk")
    tags: Optional[List[str]] = Field(default_factory=list)


class KnowledgeQueryRequest(BaseModel):
    persona_id: str = Field(default="default")
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class KnowledgeQueryResult(BaseModel):
    text: str
    title: Optional[str]
    score: float
    tags: List[str]


class KnowledgeQueryResponse(BaseModel):
    results: List[KnowledgeQueryResult]
    combined_context: str


# -- HeyGen ------------------------------------------------------------------

class HeyGenSpeakRequest(BaseModel):
    session_id: str
    text: str
    task_type: str = Field(default="repeat", description="repeat or talk")


class HeyGenSpeakResponse(BaseModel):
    task_id: str
    status: str


# -- Analytics ----------------------------------------------------------------

class ConversationMessage(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None


class SessionAnalytics(BaseModel):
    session_id: str
    persona_id: str
    duration_seconds: int
    message_count: int
    visitor_name: Optional[str]
    visitor_email: Optional[str]
    messages: List[ConversationMessage]


# -- Health ------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    version: str
    services: dict
