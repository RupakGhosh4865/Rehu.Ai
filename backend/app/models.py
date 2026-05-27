"""
Savant.ai -- Pydantic Models (API Request/Response schemas)
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
    persona_name: str = Field(default="Aiza", description="The persona display name")
    company_name: str = Field(default="our company", description="Company the persona represents")
    tone: PersonaTone = PersonaTone.professional
    avatar_id: Optional[str] = Field(None, description="HeyGen avatar ID override")
    voice_id: Optional[str] = Field(None, description="ElevenLabs voice ID override")
    system_prompt_override: Optional[str] = Field(None, description="Custom system prompt")
    calendly_url: Optional[str] = Field(None, description="Calendly booking link offered when visitor wants a meeting")
    notification_email: Optional[str] = Field(None, description="Override email for lead/meeting alerts (per persona)")


# -- Session ------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    persona_id: str = Field(default="default")
    visitor_name: Optional[str] = Field(None, description="Name of the website visitor")
    visitor_email: Optional[str] = Field(None)
    language: str = Field(default="en", description="ISO 639-1 language code")
    context: Optional[str] = Field(None, description="Optional extra context for this session")
    metadata: Optional[dict] = Field(default_factory=dict, description="Client metadata (widget mode, user agent)")
    user_id: Optional[str] = Field(None, description="Logged-in user ID on the host site")
    user_plan: Optional[str] = Field(None, description="Visitor's current plan: free | pro | enterprise …")
    user_stage: Optional[str] = Field(None, description="Lifecycle stage: trial | active | churning …")
    page_context: Optional[str] = Field(None, description="Human-readable context of the page (e.g. 'Pricing page')")


class SessionEventRequest(BaseModel):
    role: str = Field(description="user or assistant")
    text: str = Field(description="Transcript line text")
    event_type: str = Field(default="transcript", description="transcript or status")


class UpdateVisitorRequest(BaseModel):
    visitor_name: Optional[str] = None
    visitor_email: Optional[str] = None


class UpdateSessionLanguageRequest(BaseModel):
    language: str = Field(default="en", description="ISO 639-1 language code")


class SessionMessageRequest(BaseModel):
    text: str = Field(description="Typed chat message from visitor")


class MeetingRequest(BaseModel):
    session_id: Optional[str] = None
    persona_id: str = Field(default="default")
    visitor_name: Optional[str] = None
    visitor_email: Optional[str] = None
    company_name: Optional[str] = None
    preferred_time: Optional[str] = None
    timezone: Optional[str] = None
    topic: Optional[str] = None
    notes: Optional[str] = None
    status: str = Field(default="new")


class MeetingStatusRequest(BaseModel):
    status: str = Field(default="contacted")
    notes: Optional[str] = None


class ProductCardRequest(BaseModel):
    id: Optional[str] = None
    persona_id: str = Field(default="default")
    title: str
    subtitle: str = ""
    eyebrow: str = ""
    image_url: str = ""
    slide_images: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    bullets: List[str] = Field(default_factory=list)
    value_points: List[str] = Field(default_factory=list)
    cta_label: str = "Learn more"
    cta_url: str = ""
    default: bool = False


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    company_name: str = ""
    plan: str = "trial"


class LoginRequest(BaseModel):
    email: str
    password: str


class TenantUpdateRequest(BaseModel):
    company_name: Optional[str] = None
    slug: Optional[str] = None
    plan: Optional[str] = None


class BillingCheckoutRequest(BaseModel):
    plan: str = Field(default="professional", description="pilot | professional | business")


class IntegrationConnectRequest(BaseModel):
    return_url: Optional[str] = None


class RideAlongJoinRequest(BaseModel):
    meeting_url: str = Field(description="Full Zoom / Google Meet / Teams meeting link")
    persona_id: str = Field(default="default")
    bot_name: str = Field(default="Maya | AI Specialist", description="Display name in the meeting")


class RideAlongSpeakRequest(BaseModel):
    text: str = Field(description="Text the bot should say in the meeting")


class LeadCaptureRequest(BaseModel):
    """Public inbound lead submitted from the website homepage forms."""
    name: Optional[str] = None
    email: str
    company: Optional[str] = None
    message: Optional[str] = None
    product_context: Optional[str] = Field(None, description="What the visitor told Aiza about their product")
    source: Optional[str] = Field(default="homepage", description="hero_ended | contact_form | homepage")


class ComplianceSettingsRequest(BaseModel):
    consent_required: bool = True
    consent_text: str = "This conversation may be transcribed and saved so the team can follow up and improve service."
    retention_days: int = Field(default=90, ge=1, le=3650)
    store_audio: bool = False
    pii_redaction: bool = False


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
