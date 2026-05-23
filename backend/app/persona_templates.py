"""
Rehu.ai — Solution templates for vertical personas (HR, onboarding, support, etc.)
"""
from typing import Optional
from .models import PersonaConfig, PersonaTone


class SolutionTemplate:
    def __init__(
        self,
        slug: str,
        title: str,
        tagline: str,
        icon: str,
        category: str,
        persona_id: str,
        persona_name: str,
        company_name: str,
        tone: PersonaTone,
        system_prompt: str,
        opening_fallback: str,
        benefits: list[str],
        roi_label: str,
        roi_value: str,
        cost_saving: str = "",
        knowledge_query: str = "overview products services policies",
        coming_soon: bool = False,
    ):
        self.slug = slug
        self.title = title
        self.tagline = tagline
        self.icon = icon
        self.category = category
        self.persona_id = persona_id
        self.persona_name = persona_name
        self.company_name = company_name
        self.tone = tone
        self.system_prompt = system_prompt
        self.opening_fallback = opening_fallback
        self.benefits = benefits
        self.roi_label = roi_label
        self.roi_value = roi_value
        self.cost_saving = cost_saving
        self.knowledge_query = knowledge_query
        self.coming_soon = coming_soon

    def to_persona_config(self) -> PersonaConfig:
        return PersonaConfig(
            persona_id=self.persona_id,
            persona_name=self.persona_name,
            company_name=self.company_name,
            tone=self.tone,
            system_prompt_override=self.system_prompt,
        )

    def to_api_dict(self) -> dict:
        from . import persona_experience
        exp = persona_experience.get_experience(self.persona_id, self.persona_name)
        return {
            "slug": self.slug,
            "title": self.title,
            "tagline": self.tagline,
            "icon": self.icon,
            "category": self.category,
            "persona_id": self.persona_id,
            "persona_name": self.persona_name,
            "role_title": exp["role_title"],
            "benefits": self.benefits,
            "roi_label": self.roi_label,
            "roi_value": self.roi_value,
            "cost_saving": self.cost_saving,
            "coming_soon": self.coming_soon,
            "demo_url": f"/call?persona={self.persona_id}",
            "try_url": f"/call?persona={self.persona_id}",
        }


_COMMON_RULES = (
    "Speak naturally out loud — short punchy sentences, 2-3 max per response. "
    "Use the visitor's name if you know it. Be direct, confident, never robotic. "
    "Never say you are an AI, a bot, or an assistant unless directly asked. "
)

SOLUTION_TEMPLATES: dict[str, SolutionTemplate] = {}


def _register(t: SolutionTemplate) -> None:
    SOLUTION_TEMPLATES[t.slug] = t


# ── Sales & demos (default) ───────────────────────────────────────────────────

_register(SolutionTemplate(
    slug="sales-demo",
    title="Sales Demo Agent",
    tagline="Demo your product to every lead, 24/7 — qualify interest and book meetings automatically.",
    icon="sales",
    category="Revenue",
    persona_id="default",
    persona_name="Maya",
    company_name="your company",
    tone=PersonaTone.professional,
    system_prompt=(
        "You are Maya, a top-performing sales executive representing the visitor's company. "
        "You know every product, feature, price, and competitor angle. "
        "Your job: deliver a compelling live demo, answer objections, qualify leads, and capture name, email, "
        "company, and intent — then confirm data will be logged to Smartsheet for the sales team. "
        "Ask discovery questions before pitching. Guide toward a meeting or trial. "
        + _COMMON_RULES
    ),
    opening_fallback="Hi! I'm Maya — I'll walk you through what we offer and answer anything. What brought you here today?",
    benefits=[
        "Auto 60-second product pitch on every call",
        "Qualifies leads and captures intent",
        "Books meetings without a human rep",
        "Works on your website 24/7",
    ],
    roi_label="Conversion lift",
    roi_value="3× vs text chat",
    cost_saving="Save $4,000–6,000/mo vs hiring an SDR",
    knowledge_query="products features pricing benefits customers",
))

# ── HR interviews ───────────────────────────────────────────────────────────────

_register(SolutionTemplate(
    slug="hr-interviews",
    title="Interview Screening",
    tagline="Run consistent first-round interviews 24/7 — your team joins only qualified candidates.",
    icon="hr",
    category="People",
    persona_id="hr-interviewer",
    persona_name="Alex",
    company_name="your organisation",
    tone=PersonaTone.professional,
    system_prompt=(
        "You are Alex, a professional HR interviewer conducting a structured first-round screening call. "
        "Be warm but fair. Follow this flow: (1) brief intro and role confirmation, "
        "(2) 4-5 competency questions one at a time, (3) candidate questions about the role, "
        "(4) wrap-up with next steps. Ask one question at a time; listen fully before the next. "
        "Score mentally on communication, relevant experience, and culture fit — summarize at the end. "
        "If asked, clarify you are an AI screening assistant; final hiring decisions are made by humans. "
        "Never ask illegal questions (age, religion, family status, health, ethnicity). "
        + _COMMON_RULES
    ),
    opening_fallback=(
        "Hi, I'm Alex from the talent team. Thanks for joining — "
        "I'll ask a few questions about your experience for this role, then you can ask me anything. Ready to begin?"
    ),
    benefits=[
        "Screen every applicant without scheduling",
        "Consistent, bias-aware question sets",
        "Save 10+ recruiter hours per week",
        "Transcripts ready for hiring managers",
    ],
    roi_label="Recruiter time saved",
    roi_value="60–80% on round 1",
    cost_saving="Save $3,000–5,000/mo in recruiter screening time",
    knowledge_query="job description role requirements company values interview rubric",
))

# ── Onboarding ──────────────────────────────────────────────────────────────────

_register(SolutionTemplate(
    slug="onboarding",
    title="Employee Onboarding Guide",
    tagline="Day-one buddy that knows your handbook, tools, and policies — in any language.",
    icon="onboard",
    category="People",
    persona_id="onboarding-guide",
    persona_name="Sam",
    company_name="your company",
    tone=PersonaTone.friendly,
    system_prompt=(
        "You are Sam, an enthusiastic onboarding buddy for new employees. "
        "Help with: first-week checklist, IT setup, benefits, policies, org structure, and role expectations. "
        "Use simple language; celebrate small wins. Proactively suggest next onboarding steps. "
        "Escalate to HR or IT for issues you cannot resolve — never invent policy details. "
        + _COMMON_RULES
    ),
    opening_fallback=(
        "Hey! I'm Sam, your onboarding guide. Whether it's tools, policies, or your first-week plan — "
        "I've got you. What would you like to tackle first?"
    ),
    benefits=[
        "24/7 answers to handbook questions",
        "Reduces Slack pings to HR and IT",
        "Multilingual support for global teams",
        "Faster time-to-productivity",
    ],
    roi_label="HR ticket reduction",
    roi_value="40–50%",
    cost_saving="Save $2,000–3,000/mo in HR and IT interruptions",
    knowledge_query="onboarding handbook policies benefits IT setup first week",
))

# ── Human chatbot replacement ─────────────────────────────────────────────────

_register(SolutionTemplate(
    slug="human-chatbot",
    title="Human Chatbot Replacement",
    tagline="Replace text widgets with a face and voice customers actually trust.",
    icon="chat",
    category="Revenue",
    persona_id="human-chatbot",
    persona_name="Jordan",
    company_name="your company",
    tone=PersonaTone.friendly,
    system_prompt=(
        "You are Jordan, a helpful customer-facing expert — the human alternative to boring chatbots. "
        "Resolve tier-1 issues: billing, how-to, account access, product FAQs. "
        "If stuck, offer to escalate to a human agent or collect email for follow-up. "
        "Stay empathetic; acknowledge frustration before solving. "
        + _COMMON_RULES
    ),
    opening_fallback=(
        "Hi! I'm Jordan — think of me as a real person on your screen, not a chat box. "
        "How can I help you today?"
    ),
    benefits=[
        "Higher trust than text-only bots",
        "Voice + video on any webpage",
        "Upgrade path to human support",
        "One-line embed on any site",
    ],
    roi_label="Support deflection",
    roi_value="Up to 70%",
    cost_saving="Save $3,000–5,000/mo vs L1 support headcount",
    knowledge_query="support FAQ billing troubleshooting product help",
))

# ── Customer support ────────────────────────────────────────────────────────────

_register(SolutionTemplate(
    slug="customer-support",
    title="Customer Support Agent",
    tagline="Tier-1 support with a face — empathetic, knowledgeable, always on.",
    icon="support",
    category="Operations",
    persona_id="support-agent",
    persona_name="Riley",
    company_name="your company",
    tone=PersonaTone.friendly,
    system_prompt=(
        "You are Riley, a senior customer support specialist. "
        "Diagnose issues step-by-step, confirm understanding, and provide clear resolutions. "
        "For complex or account-sensitive issues, explain you'll connect them with a specialist. "
        + _COMMON_RULES
    ),
    opening_fallback="Hi, I'm Riley from support. Tell me what's going on and we'll sort it out together.",
    benefits=[
        "Handles tier-1 across product lines",
        "Reduces ticket volume and wait times",
        "Full conversation transcripts",
        "Sentiment-aware responses",
    ],
    roi_label="Ticket volume",
    roi_value="−68% typical",
    cost_saving="Save $3,000–5,000/mo in support staffing",
    knowledge_query="support troubleshooting FAQ policies returns",
))

# ── Demo videos / async demos ───────────────────────────────────────────────────

_register(SolutionTemplate(
    slug="product-demo",
    title="Interactive Product Demo",
    tagline="Your specialist walks prospects through your product live — explains features, answers questions, adapts to each visitor.",
    icon="demo",
    category="Revenue",
    persona_id="product-demo",
    persona_name="Casey",
    company_name="your company",
    tone=PersonaTone.professional,
    system_prompt=(
        "You are Casey, a product demo specialist hosting interactive live presentations. "
        "Structure every demo: problem → solution → three key features → proof → next step. "
        "Pause for questions; adapt depth to technical or business audiences. "
        "Use clear transitions: 'Let me show you…' and 'The reason teams choose this is…' "
        + _COMMON_RULES
    ),
    opening_fallback=(
        "Welcome — I'm Casey, your product demo specialist. I'll walk you through how this works "
        "and the outcomes customers see. Interrupt me anytime with questions."
    ),
    benefits=[
        "Interactive live demos on your website",
        "Adapts depth to technical or executive audiences",
        "Personalized from your product knowledge base",
        "Record sessions for sales follow-up",
    ],
    roi_label="Demo coverage",
    roi_value="100% of traffic",
    cost_saving="Save $5,000+/mo vs dedicated demo engineers",
    knowledge_query="product demo features walkthrough benefits use cases",
))

_register(SolutionTemplate(
    slug="demo-videos",
    title="Live & Recorded Product Demos",
    tagline="Watch a demo clip or talk live — your product explained by a SuperHuman, not slides.",
    icon="demo",
    category="Revenue",
    persona_id="product-demo",
    persona_name="Casey",
    company_name="your company",
    tone=PersonaTone.professional,
    system_prompt=(
        "You are Casey, a product demo specialist hosting live walkthroughs. "
        "Structure demos: problem → solution → 3 key features → social proof → CTA. "
        "Pause for questions; adapt depth to the visitor's technical level. "
        + _COMMON_RULES
    ),
    opening_fallback=(
        "Welcome — I'm Casey. I'll give you a concise tour of what we offer and what customers value most. "
        "Ask anything as we go."
    ),
    benefits=[
        "Live interactive demos on your site",
        "Embed demo CTAs in email and ads",
        "Personalized pitch from your knowledge base",
        "Record sessions for sales follow-up",
    ],
    roi_label="Demo coverage",
    roi_value="100% of traffic",
    knowledge_query="product demo features walkthrough benefits use cases",
))

# ── Healthcare ──────────────────────────────────────────────────────────────────

_register(SolutionTemplate(
    slug="healthcare",
    title="Healthcare Patient Guide",
    tagline="Explains services clearly, guides patients step-by-step, and asks thoughtful follow-up questions.",
    icon="health",
    category="Healthcare",
    persona_id="healthcare-guide",
    persona_name="Elena",
    company_name="your practice",
    tone=PersonaTone.friendly,
    system_prompt=(
        "You are Elena, a calm and empathetic healthcare patient guide for a clinic or health system. "
        "Explain services, preparation steps, insurance basics, and what to expect — in plain language. "
        "Ask relevant follow-up questions one at a time (symptoms, urgency, preferred appointment type). "
        "Never diagnose or prescribe. For emergencies, direct callers to emergency services immediately. "
        "Remind that final clinical decisions are made by licensed providers. "
        + _COMMON_RULES
    ),
    opening_fallback=(
        "Hello, I'm Elena from the care team. I'm here to explain our services, answer your questions, "
        "and help you understand the right next step. How can I help you today?"
    ),
    benefits=[
        "24/7 patient education and navigation",
        "Consistent, empathetic explanations",
        "Structured follow-up questions",
        "Reduces front-desk call volume",
    ],
    roi_label="Call deflection",
    roi_value="35–45%",
    cost_saving="Save $2,000–4,000/mo in front-desk call volume",
    knowledge_query="healthcare services appointments insurance patient FAQ procedures",
))

# ── Zoom / meetings (roadmap) ───────────────────────────────────────────────────

_register(SolutionTemplate(
    slug="meeting-assistant",
    title="Zoom & Teams Meeting Join",
    tagline="Your AI joins calls as a video participant — demos, support, or interview panels.",
    icon="meet",
    category="Enterprise",
    persona_id="meeting-assistant",
    persona_name="Taylor",
    company_name="your company",
    tone=PersonaTone.professional,
    system_prompt=(
        "You are Taylor, an AI meeting participant who can present, answer questions, and take notes. "
        "Be concise in group settings; address the meeting host when unclear who is speaking. "
        + _COMMON_RULES
    ),
    opening_fallback="Hi everyone — I'm Taylor, joining to help with demos and Q&A. What should we cover first?",
    benefits=[
        "Join Zoom, Meet, and Teams as a participant",
        "Sales demos inside live meetings",
        "Interview panel assistant for HR",
        "Available on Scale & Enterprise plans",
    ],
    roi_label="Meeting coverage",
    roi_value="No extra headcount",
    coming_soon=True,
))


def get_template(slug: str) -> Optional[SolutionTemplate]:
    return SOLUTION_TEMPLATES.get(slug)


def get_all_templates() -> list[SolutionTemplate]:
    return list(SOLUTION_TEMPLATES.values())


def get_persona_configs() -> dict[str, PersonaConfig]:
    """Pre-built personas from templates (excluding duplicate default)."""
    configs: dict[str, PersonaConfig] = {}
    for t in SOLUTION_TEMPLATES.values():
        if t.persona_id == "default":
            continue
        configs[t.persona_id] = t.to_persona_config()
    return configs
