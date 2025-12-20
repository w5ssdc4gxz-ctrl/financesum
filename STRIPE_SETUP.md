# Stripe Billing (Subscriptions) Setup

This project uses **Stripe Checkout** for starting subscriptions and the **Stripe Customer Portal** for managing them.

## 1) Create a recurring price in Stripe

In the Stripe Dashboard (Test mode):
- Create a Product (e.g., “FinanceSum Pro”)
- Create a **recurring** Price (e.g., `$20/month`)
- Either:
  - copy the Price ID (looks like `price_...`) and set `STRIPE_PRICE_ID`, or
  - set a **lookup key** for the price (e.g., `pro_monthly`) and set `STRIPE_PRICE_LOOKUP_KEY`

## 2) Configure environment variables

Backend reads from the repo-root `.env`.

macOS shortcut (recommended): copy your `STRIPE_...` lines to the clipboard, then run:

```bash
python3 scripts/set_stripe_env_from_clipboard.py
```

Required:
- `STRIPE_SECRET_KEY`
- `STRIPE_PUBLISHABLE_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_ID` **or** `STRIPE_PRICE_LOOKUP_KEY` (required in production; in test mode the backend auto-creates a default `$20/month` Pro price if omitted)

Recommended:
- `SITE_URL` (e.g., `http://localhost:3000` in dev, your real domain in prod)

## 3) Create the billing tables in Supabase

Run the SQL migration:
- `supabase/migrations/002_billing.sql`

If you don’t use the Supabase CLI, paste it into the Supabase SQL editor and run it once.

## 4) Configure the Stripe Customer Portal

In Stripe Dashboard:
- Settings → Billing → Customer portal
- Save a default configuration (Stripe will prompt you if it’s not configured)

## 5) Configure and run the webhook (dev)

Install Stripe CLI, then:

```bash
stripe listen --forward-to http://localhost:8000/api/v1/billing/webhook
```

Copy the printed webhook secret (`whsec_...`) into `STRIPE_WEBHOOK_SECRET`.

## 6) Test the flow

1. Start the app (e.g. `python3 start.py`)
2. Verify Stripe is configured (no secrets printed):

```bash
curl -s http://localhost:8000/api/v1/billing/config | jq
```

Ensure `secret_key_configured: true` (and optionally `mode: "test"` while testing).
3. Sign in
4. Click **Upgrade to Pro**
5. Use Stripe test card `4242 4242 4242 4242`
6. Confirm `/billing` shows **Pro**, and **Open Billing Portal** works

## Backend endpoints

- `POST /api/v1/billing/create-checkout-session` (auth required)
- `POST /api/v1/billing/create-portal-session` (auth required)
- `POST /api/v1/billing/sync` (auth required; used by success page)
- `GET /api/v1/billing/subscription` (auth required)
- `POST /api/v1/billing/webhook` (Stripe webhook)
