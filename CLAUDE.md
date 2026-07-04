# ResumeAI Backend

FastAPI + SQLAlchemy + Alembic + Postgres. Sits between the React frontend (`../resume-ai-frontend`) and the LLM service (`../resumeai-AI`). See the frontend CLAUDE.md for the cross-repo overview.

## Run / deploy

- Local: Docker via root `docker-compose.yml`. Code is volume-mounted but uvicorn has **no `--reload`** → after edits run `docker compose restart resumeai-backend` (~30–60s, pip re-runs on boot; no rebuild needed).
- Env: loads from `resumeai-backend/.env` (not compose). Prod: Railway (`railway.toml`), also `render.yaml`/`Procfile` exist.
- Migrations: Alembic in `alembic/versions/`. Pricing plans are **seeded by migrations** (`g4b5c6d7e8f9_polar_plans.py`): weekly $14.99, monthly $35.99 (Most Popular), three_months $79.99.
- Debug scripts inside the container: `docker exec resumeai-backend sh -c "cd /srv/backend && python <script>"` — import all models first (`pkgutil.iter_modules(app.models.__path__)`) or SQLAlchemy fails resolving relationships.

## Layout

- `app/routers/` — one file per feature: `auth`, `resumes` (incl. `POST /resumes/{id}/ai/optimize`), `cover_letter`, `qa_answers`, `hr_email`, `export`, `payments` (Polar checkout/sync/switch/cancel/portal), `webhooks` (Polar), `pricing`, `dashboard`, `settings`, `profile`, `templates`, `admin`, `juno` (AI chat), `help_center`, `user_routes`.
- `app/models/` — `user` (has `plan_id`, `credits_remaining`, `role`), `pricing_plan`, `resume`, `ai_usage`, `ats_score`, `template`, `user_settings`, `session_tracking`, `juno_prompt`, `help_article`.
- `app/core/` — `config.py` (settings incl. `UsageLimitSettings`, `PolarSettings`), `security.py` (JWT, `get_current_user`, `require_roles`, `require_paid_plan`).
- `app/utils/` — `usage_limits.py`, `ai_client.py` (streaming proxy to AI service), `resume_ai_adapter.py` (backend resume content → AI request shape), `polar_client.py`.

## Monetization model (three separate mechanisms — don't confuse them)

1. **Paid-plan gate (402)** — `require_paid_plan()` dependency returns `402 {code: "requires_plan"}` for users with `plan_id = NULL`. Applied to ALL four AI endpoints: resume optimize, cover letter, Q&A answers, HR email drafts (QA/HR gated 2026-07-04 — free tier keeps only the resume builder, no AI). Note: the resume-optimization feature is currently paused product-side.
2. **Hidden weekly usage caps (429)** — anti-abuse, applies to everyone incl. paid (admins bypass). `enforce_usage_limit(db, user, feature)` in `app/utils/usage_limits.py`, called in all four AI endpoints after input validation, before the AI call (records the event up front). Rolling window (`USAGE_WINDOW_DAYS`, default 7), one `ai_usage_events` row per generation. Cap = base per feature (`USAGE_LIMIT_*`, default 20) × plan multiplier (`USAGE_MULT_*`: free/weekly ×1, monthly ×2, three_months ×3). Returns `429 {code: "usage_limit_reached", feature, resets_at}`. **Counts generations, not tokens** — no token metering exists anywhere. Tune via env vars, no deploy needed.
3. **`users.credits_remaining`** — display-only balance set on purchase; never decremented by AI use. Do not build logic on it.

Feature keys (`UsageFeature` in `config.py`): `resume_ai`, `cover_letter`, `qa_answers`, `hr_email`.

## Payments (Polar)

- Source of truth for PRO status is `user.plan_id`, set by the `/webhooks/polar` handler and by `POST /payments/polar/sync` (idempotent reconcile; matches Polar customer by external_id, falling back to **email** because ids drift when the DB is reseeded).
- Polar SDK returns `status` as an enum — always normalize via `.value` before comparing (`_status_str()`/`_is_active()` in `payments.py`).
- Post-checkout redirect is built from the request `Origin` (allowlist `POLAR_ALLOWED_ORIGINS`), fallback `POLAR_SUCCESS_URL` — keeps localhost checkouts on localhost.
- Webhooks can't reach localhost; the frontend's once-per-session sync call is the self-heal for dev.
- **Discounts / launch offer**: set `POLAR_DISCOUNT_ID` to a Polar discount UUID and every checkout gets it pre-applied (`payments.py` checkout create). The frontend does NOT read prices from Polar — displayed prices are static in `pricing.tsx` with the offer applied by `src/lib/launch-offer.ts`; keep the two in sync.

## AI proxying

Cover letter / Q&A / HR email stream: router builds a payload (resume from DB via `resume_ai_adapter`, or raw `resume_text`), calls the AI service (`/generate_cover_letter`, `/generate_qa_answers`, `/generate_hr_email`) and streams `text/plain` chunks through with `X-Accel-Buffering: no`. Q&A/HR prime the first chunk inside the handler so connection errors surface as a clean 502 before streaming starts. Resume optimize (`/generate_resume`) is non-streaming JSON.
