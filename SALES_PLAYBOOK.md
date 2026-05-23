# Rehu.ai — Sales Playbook

**Simple guide to sell, price, and deliver Rehu.ai to clients.**

---

## What You Are Selling

Rehu.ai puts **your client's smartest rep in the room** — on their website, 24/7.

Not a chatbot. A **live specialist** with a face, a voice, and deep knowledge of the client's business.

**One line pitch:**
> "Deploy your best rep on every page — qualifies leads, answers questions, and never takes a day off."

---

## Who Buys

| Vertical | Buyer | Pain | Your pitch |
|----------|-------|------|------------|
| **Sales** | CMO, VP Sales | Low conversion, slow follow-up | "Maya on your pricing page — every visitor gets a live demo" |
| **HR** | CHRO, Talent | Screening backlog | "Alex screens every applicant before your team spends an hour" |
| **Support** | CX Director | Ticket volume, wait times | "Riley handles tier-1 before humans step in" |
| **Healthcare** | Ops, Patient Access | Phone overload | "Elena guides patients after hours — fewer front-desk calls" |
| **Product** | Product Marketing | Demo bottleneck | "Casey demos to 100% of traffic, not just booked calls" |

---

## Pricing (What to Charge)

### Public packages (homepage)

| Plan | Price | Best for |
|------|-------|----------|
| **Pilot** | $997/mo | 30-day proof of value, 1 specialist, sandbox |
| **Professional** | $1,999/mo | 1 production specialist, website embed, 500 min/mo |
| **Business** | $3,499/mo | 3 specialists, 2,000 min/mo, priority support |
| **Enterprise** | Custom | Dedicated infra, SLA, compliance, on-prem |

**Setup fees (quote separately):**
- Professional: $2,500 one-time
- Business: $5,000 one-time
- Enterprise: $10,000–25,000+

**Annual discount:** 15–20% off monthly if paid upfront.

---

## Your Costs (COGS)

Per client instance, monthly:

| Item | Cost |
|------|------|
| LiveAvatar (production) | $99 – $399 |
| OpenAI | $20 – $80 |
| Hosting | $5 – $50 |
| **Total** | **~$125 – $570/mo** |

### Margin at $1,999/mo (Professional)

| | Amount |
|---|--------|
| Revenue | $1,999 |
| COGS (avg) | ~$350 |
| **Gross margin** | **~82%** |

### Margin at $3,499/mo (Business)

| | Amount |
|---|--------|
| Revenue | $3,499 |
| COGS (avg) | ~$450 |
| **Gross margin** | **~87%** |

**Rule:** Never sell production LiveAvatar at below **$1,500/mo** — you lose margin.

---

## Cost Savings to Quote (by use case)

Use these in proposals and on the homepage:

| Use case | Cost saving vs hiring | Why it pays for itself |
|----------|----------------------|------------------------|
| **Sales** | $4,000–6,000/mo vs SDR | One extra deal/month covers the platform |
| **HR screening** | $3,000–5,000/mo recruiter time | 60–80% less time on round-1 screens |
| **Support** | $3,000–5,000/mo L1 headcount | Up to 68% fewer tier-1 tickets |
| **Healthcare** | $2,000–4,000/mo front desk | 35–45% call deflection after hours |
| **Product demo** | $5,000+/mo demo engineers | Every visitor gets a live walkthrough |
| **Onboarding** | $2,000–3,000/mo HR/IT time | 40–50% fewer handbook questions |

**ROI framing:** Rehu at $1,999/mo costs less than **half of one junior hire** ($4,000–6,000/mo loaded).

---

## Sales Process (4 steps)

### 1. Demo (15 min)

- Open `/call` with their industry persona
- Show live voice + video conversation
- Load 2–3 of their FAQ items into knowledge (if time allows)

### 2. Pilot (30 days) — $997/mo

- Deploy sandbox instance
- Upload their docs in Admin
- Embed on **staging** site only
- Measure: conversations, time on page, leads captured

### 3. Close — Professional or Business

- Flip to production avatars (`LIVEAVATAR_USE_SANDBOX=false`)
- Annual contract preferred
- Setup fee + first month upfront

### 4. Expand

- Add second persona (HR + Sales)
- Upsell to Business tier
- Charge for knowledge updates ($200–500/batch)

---

## Objection Handling

| Objection | Response |
|-----------|----------|
| "We already have a chatbot" | "Chatbots get ignored. A live specialist on screen gets 3× more engagement." |
| "Too expensive" | "One SDR costs $5k/mo. This is $2k and works nights and weekends." |
| "Our customers want humans" | "They get a human presence — yours knows the product inside out." |
| "What about data privacy?" | "Dedicated instance per client. Enterprise tier adds SSO, compliance review, on-prem." |
| "Can we try first?" | "Pilot at $997/mo — 30 days, your content, staging embed." |

---

## What to Deliver (per client)

1. Deployed Rehu instance (Railway or client cloud)
2. 1+ personas configured with company name
3. Knowledge base uploaded (client docs)
4. Embed script for their website
5. 30-min handoff call with their marketing/IT
6. Monthly check-in (Business+)

See [DEPLOYMENT.md](DEPLOYMENT.md) for technical steps.

---

## Contract Terms (recommended)

- **Minimum term:** 12 months (Professional/Business)
- **Payment:** Monthly or annual upfront
- **Included minutes:** 500 (Pro) / 2,000 (Business) — overage at $0.15/min
- **Cancellation:** 30 days notice after initial term
- **Setup:** Non-refundable after knowledge upload begins

---

## Quick Reference

| Question | Answer |
|----------|--------|
| Business model? | Platform subscription + setup fee |
| Target price? | **$1,999–3,499/mo** for most clients |
| Target margin? | **70–85% gross** |
| First deal size? | Pilot $997 → Pro $1,999/mo |
| Who deploys? | You (managed service today) |
| Multi-tenant? | No — one instance per client |

---

**Contact:** info@sspmconsultants.com  
**Demo:** `/call` on your deployed URL
