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

### Subscription lifecycle & money-back policy (added 2026-07-04)

Local DB is the source of truth for **access**; Polar for **billing/refunds**. State lives on `users.subscription_state` (`app.core.config.SubscriptionState`: `active` | `canceled_reserved` | `canceled_refunded` | `expired`) plus `polar_subscription_id`, `polar_order_id`, `polar_order_amount`, `subscription_started_at`, `subscription_period_end`. Entitlement is computed by `app/utils/subscription.py::has_paid_access` (used by `require_paid_plan` AND the account-summary `current_plan`, so canceled-but-reserved users are correctly gated). Migration `i6d7e8f9a0b1` backfills existing plan holders to `active`.

Cancel policy (`POST /payments/polar/cancel`, per plan money-back window in `RefundSettings`: weekly/monthly **1 day**, three_months **7 days**, env `REFUND_WINDOW_*`):
- **Within window** → `refunds.create(order_id, satisfaction_guarantee, amount=order total, revoke_benefits=True)` + `subscriptions.revoke()` (immediate). State → `canceled_refunded`, plan cleared. Refund is **automatic**.
- **Past window** → `subscriptions.update(cancel_at_period_end=True)` (no renewal) + block access **now**. State → `canceled_reserved`, `plan_id` kept. User keeps a free-reactivate window until `subscription_period_end`.

`POST /payments/polar/reactivate` — free re-subscribe for a `canceled_reserved` user before `subscription_period_end` (flips state back to `active`, no charge; Polar stays cancel-at-period-end so it still expires on the original date). Returns 409 `must_repurchase` once the window has passed.

Webhook: only `subscription.revoked` clears access (period truly ended / immediate revoke) → `expired`. `subscription.canceled` (scheduled) is intentionally ignored so it doesn't kill access early. `/polar/subscription` is now **local-first** and exposes `state`, `refund_eligible_now`, `refund_window_days`, `can_reactivate_free`, `reserved_until` for the settings UI.

Feature keys (`UsageFeature` in `config.py`): `resume_ai`, `cover_letter`, `qa_answers`, `hr_email`.

## Payments (Polar)

- Source of truth for PRO status is `user.plan_id`, set by the `/webhooks/polar` handler and by `POST /payments/polar/sync` (idempotent reconcile; matches Polar customer by external_id, falling back to **email** because ids drift when the DB is reseeded).
- Polar SDK returns `status` as an enum — always normalize via `.value` before comparing (`_status_str()`/`_is_active()` in `payments.py`).
- Post-checkout redirect is built from the request `Origin` (allowlist `POLAR_ALLOWED_ORIGINS`), fallback `POLAR_SUCCESS_URL` — keeps localhost checkouts on localhost.
- Webhooks can't reach localhost; the frontend's once-per-session sync call is the self-heal for dev.
- **Discounts / launch offer**: set `POLAR_DISCOUNT_ID` to a Polar discount UUID and every checkout gets it pre-applied (`payments.py` checkout create). The frontend does NOT read prices from Polar — displayed prices are static in `pricing.tsx` with the offer applied by `src/lib/launch-offer.ts`; keep the two in sync.

## AI proxying

Cover letter / Q&A / HR email stream: router builds a payload (resume from DB via `resume_ai_adapter`, or raw `resume_text`), calls the AI service (`/generate_cover_letter`, `/generate_qa_answers`, `/generate_hr_email`) and streams `text/plain` chunks through with `X-Accel-Buffering: no`. Q&A/HR prime the first chunk inside the handler so connection errors surface as a clean 502 before streaming starts. Resume optimize (`/generate_resume`) is non-streaming JSON.
