# Polar.sh Payment Integration

This project uses [Polar.sh](https://polar.sh) for subscription billing. The flow is:

1. User picks a plan on the frontend pricing page
2. `/subscribe` calls `POST /payments/polar/checkout` on this backend
3. Backend creates a Polar Checkout Session via the SDK and returns the hosted-checkout URL
4. Browser is redirected to the Polar-hosted checkout (PCI compliant; we never see card data)
5. After payment, Polar redirects back to `POLAR_SUCCESS_URL` and fires a webhook to `/webhooks/polar`
6. Webhook handler verifies the signature, then activates the user's plan + tops up credits

---

## 1. Create your Polar account & products

1. Sign up at https://sandbox.polar.sh (test mode) — and later https://polar.sh (live).
2. Create an **Organization**.
3. Under **Products**, create three subscription products that match the frontend plans:
   - **Weekly** — recurring `weekly`, $14.99
   - **Monthly** — recurring `monthly`, $35.99
   - **3 Months** — recurring `monthly` × 3 (or use a custom interval), $79.99
4. Copy each product's **UUID** (visible on the product page URL or via the API).

## 2. Generate an Organization Access Token

Polar dashboard → **Settings → Developers → New Token** (Organization scope).
Copy the token — it's shown only once.

## 3. Create a Webhook endpoint

Polar dashboard → **Settings → Webhooks → Add endpoint**.

- **URL:** `https://<your-backend-host>/webhooks/polar`
  - For local dev, expose your FastAPI server with `ngrok http 8000` and use that URL.
- **Format:** Raw
- **Events to subscribe to:**
  - `subscription.active`
  - `subscription.created`
  - `subscription.canceled`
  - `subscription.revoked`
  - `order.paid`
- Copy the **signing secret** that's displayed once.

## 4. Set environment variables

Add to `.env` (or `.envs/.env.local`) on the backend:

```env
POLAR_SERVER=sandbox            # or "production" once live
POLAR_ACCESS_TOKEN=polar_oat_...
POLAR_WEBHOOK_SECRET=whsec_...

POLAR_SUCCESS_URL=http://localhost:5173/success?checkout_id={CHECKOUT_ID}

# UUIDs of the products you created in step 1
POLAR_PRODUCT_WEEKLY=00000000-0000-0000-0000-000000000000
POLAR_PRODUCT_MONTHLY=00000000-0000-0000-0000-000000000000
POLAR_PRODUCT_THREE_MONTHS=00000000-0000-0000-0000-000000000000
```

For production, also update `POLAR_SUCCESS_URL` to your production frontend URL.

## 5. Install backend dependencies & run migrations

```bash
pip install -r requirements.txt
alembic upgrade heads
```

The new migration `g4b5c6d7e8f9_polar_plans.py` seeds the three plan rows
(`weekly`, `monthly`, `three_months`) used by the checkout endpoint.

## 6. Frontend env (no changes required)

The frontend talks to the backend via `VITE_API_URL` (already set). No Polar
keys live in the frontend — checkout sessions are always created server-side.

---

## How the webhook updates the user

`/webhooks/polar` (in `app/routers/webhooks.py`) verifies the signature with
`polar_sdk.webhooks.validate_event` and reads `metadata.user_id` and
`metadata.plan_slug` (we set both when creating the checkout session). On
`subscription.active` / `subscription.created` / `order.paid`:

- `user.plan_id` is set to the matching `pricing_plans` row
- `user.credits_remaining` is topped up to `999_999` (effectively unlimited
  for the subscription period; tweak `SUBSCRIPTION_CREDIT_TOPUP` in the
  webhook router if you want metered credits per cycle)

On `subscription.canceled` / `subscription.revoked`, `plan_id` is cleared and
`credits_remaining` is set to `0`.

## Local development with webhooks

```bash
# Terminal 1
uvicorn app.main:app --reload --port 8000

# Terminal 2
ngrok http 8000
# copy the https URL into the Polar webhook endpoint
```

## Going live

1. Switch `POLAR_SERVER=production`
2. Re-issue an access token from the **production** dashboard
3. Recreate products in production (sandbox UUIDs aren't valid in production)
4. Update `POLAR_PRODUCT_*` env vars to the production UUIDs
5. Add a production webhook endpoint and update `POLAR_WEBHOOK_SECRET`
