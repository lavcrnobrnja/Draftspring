# Stripe: productize it or remove it

DraftSpring includes Stripe because the original experiment was a hosted SaaS. The billing layer is optional.

## If you want to productize DraftSpring

Create a Stripe product + recurring price, then configure:

```bash
STRIPE_SECRET_KEY=your_stripe_secret_key
STRIPE_PRICE_ID=your_stripe_price_id
STRIPE_WEBHOOK_SECRET=your_stripe_webhook_signing_secret
STRIPE_TRIAL_DAYS=7
APP_BASE_URL=https://your-app.example
```

Routes involved:

- `GET /api/checkout/session` — creates a Stripe Checkout subscription session.
- `POST /api/billing/portal` — creates a Stripe Customer Portal session.
- `POST /webhooks/stripe` — receives subscription lifecycle events.

Frontend surfaces:

- `frontend/src/pages/Subscribe.jsx`
- `frontend/src/pages/Settings.jsx`
- `frontend/src/components/RequireSubscription.jsx`

Backend subscription checks:

- `backend/app/middleware/subscription.py`
- route handlers that call `require_active_subscription(...)`
- worker/query logic that checks `subscription_status`

Stripe webhooks update each user's `subscription_status` to values like `trialing`, `active`, `past_due`, or `canceled`.

## Local Stripe test flow

1. Install and log in to Stripe CLI.
2. Forward webhooks:

```bash
stripe listen --forward-to localhost:8223/webhooks/stripe
```

3. Copy the displayed signing secret to `.env`:

```bash
STRIPE_WEBHOOK_SECRET=whsec_your_local_secret
```

4. Use Stripe test keys and a test recurring price.

## If you want in-house use with no Stripe

This is probably the right mode for most people.

Leave Stripe env vars blank:

```bash
STRIPE_SECRET_KEY=
STRIPE_PRICE_ID=
STRIPE_WEBHOOK_SECRET=
```

Then choose one of these approaches.

### Option A — Mark internal users active

Keep the billing code in place but manually set trusted users active in the DB:

```sql
UPDATE users
SET subscription_status = 'active', articles_per_cycle_limit = 8
WHERE email = 'you@example.com';
```

This is the smallest change and preserves all app behavior.

### Option B — Disable dashboard subscription redirect

Edit `frontend/src/components/RequireSubscription.jsx` so it simply returns `children`:

```jsx
export function RequireSubscription({ children }) {
  return children;
}
```

Then update backend route checks that call `require_active_subscription(user)` if you want write actions to work for all authenticated users.

### Option C — Remove Stripe routes entirely

For a clean internal fork:

1. Remove router registration from `backend/app/main.py`:

```python
from app.routes.webhooks import router as webhooks_router
from app.routes.checkout import router as checkout_router
...
app.include_router(webhooks_router)
app.include_router(checkout_router)
```

2. Delete or ignore:

```text
backend/app/routes/checkout.py
backend/app/routes/webhooks.py
frontend/src/pages/Subscribe.jsx
frontend/src/components/RequireSubscription.jsx
```

3. Remove subscription redirects/routes from `frontend/src/App.jsx`.
4. Remove `stripe>=...` from `backend/pyproject.toml` if no other code imports it.
5. Replace subscription checks with your own internal access rule.
6. Run backend and frontend tests.

## SaaS warning

If you expose this publicly, review payment edge cases properly: failed payments, cancellation timing, account deletion, abuse, refund policy, webhook replay behavior, rate limits, and provider cost caps. Stripe wiring gets you started; it is not a complete business operating system.
