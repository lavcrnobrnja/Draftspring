# DraftSpring frontend

React/Vite dashboard for DraftSpring.

## Run locally

```bash
cd frontend
npm install
npm run dev
```

The frontend talks to the backend API through `frontend/src/api/client.js` and related helpers. For local development, run the backend separately on `http://localhost:8223` and adjust proxy/API settings if needed.

## Main screens

- Login and magic-link verification
- Subscription page if Stripe/product mode is enabled
- Dashboard layout
- New content batch creation
- Idea review
- Article review
- Ghost settings and health checks
- Usage/admin views

## Stripe-free internal use

If you remove subscription gating, update:

- `src/App.jsx`
- `src/components/RequireSubscription.jsx`
- `src/pages/Subscribe.jsx`
- settings screens that mention checkout/billing

See `../docs/stripe.md`.
