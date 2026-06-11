# AI Reel SaaS — Codex Instructions

You are building the AI Reel SaaS described in `ai_reel_saas_build_plan_md/`.

## Source of Truth
Before coding, read:
1. `ai_reel_saas_build_plan_md/00-master-roadmap.md`
2. The current week file
3. Relevant architecture/API/database/UI docs

Do not invent architecture if the plan already defines it.

## Build Order
Follow the 12-week roadmap strictly:
- Week 1: SaaS skeleton
- Week 2: Upload + processing
- Week 3: Transcription + captions
- Week 4: MVP export
- Week 5: AI edit planner
- Week 6: Remotion renderer
- Week 7: B-roll
- Week 8: Music/SFX
- Week 9: Effects/color
- Week 10: Controls/versioning
- Week 11: Stripe/payments
- Week 12: scaling/launch

## Tech Stack
- Frontend: Next.js App Router, TypeScript, Tailwind, shadcn/ui
- Backend: FastAPI, Pydantic
- DB/Auth/Storage: Supabase
- Queue: Redis + Celery/RQ
- Video: FFmpeg, Remotion
- AI: Groq transcription + OpenRouter structured edit planning
- Payments: Stripe
- Observability: Sentry

## Coding Rules
- Keep code modular and production-oriented.
- Add types/schemas before implementing business logic.
- Never hardcode secrets.
- Use `.env.example`.
- Add validation at API boundaries.
- Add tests for core logic.
- Prefer small PR-sized changes.

## Before Finishing Any Task
Run or explain:
- frontend typecheck/lint
- backend tests
- relevant build command
- what changed
- what remains
