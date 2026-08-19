# DraftSpring

DraftSpring is an open-source automation app for publishing content to Ghost blogs with AI-assisted ideation, outlining, drafting, editing, image generation, human review checkpoints, and scheduled publishing.

It started as a hosted product experiment. We killed the SaaS, kept the useful machinery, and cleaned up the repo so other Ghost users and technical teams can run or fork the app for their own publishing workflows.

## What it does

DraftSpring helps turn a Ghost blog into a repeatable publishing pipeline:

1. Analyze an existing Ghost site or content niche.
2. Collect seed ideas, URLs, notes, or prompts.
3. Generate article ideas.
4. Let a human approve ideas before work continues.
5. Build outlines and drafts with configurable AI models.
6. Humanize/edit/review the article.
7. Generate or attach article images.
8. Send review links for approval.
9. Publish approved posts to Ghost.
10. Track usage/cost and publishing status.

The app has two parts:

- `backend/` — FastAPI API, SQLite persistence, Ghost publishing integration, LLM pipeline, worker process, email, Stripe billing hooks.
- `frontend/` — React/Vite dashboard for login, settings, article batches, review workflows, usage, and admin screens.

## What this repo is not

- It is not the old DraftSpring hosted SaaS.
- It does not include our production secrets, databases, customer data, Ghost configs, server configs, or private runtime files.
- It does not include the old DraftSpring marketing Ghost theme. This repo is about the app.
- It is provided as source code. You are responsible for deployment, security, billing configuration, and model/API costs.

## Quick start

### Requirements

- Python 3.12+
- Node.js 20+
- npm
- A Ghost site with a Ghost Admin API key
- At least one supported AI provider key for production use

### Backend

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8223
```

The backend runs migrations automatically on startup and stores local data in `./data/ghostwriter.db` by default.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

By default the frontend expects the API to be available on the same origin in production. For local development, adjust `frontend/src/api/client.js` or Vite proxy settings if you run the API on a different port.

### Worker

In a second backend shell:

```bash
cd backend
source .venv/bin/activate
python -m app.worker_main
```

The API accepts and schedules work; the worker advances article batches through the pipeline.

## Configuration

Copy `backend/.env.example` to `backend/.env` and fill in only the providers you use.

Important defaults:

- `APP_ENV=development` uses mock LLM behavior by default.
- `APP_ENV=production` uses live model providers.
- Local storage is enabled by default.
- Stripe is optional. Keep Stripe variables empty for in-house use.

See:

- [`docs/configuration.md`](docs/configuration.md)
- [`docs/ai-models.md`](docs/ai-models.md)
- [`docs/stripe.md`](docs/stripe.md)
- [`docs/ghost.md`](docs/ghost.md)
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/development.md`](docs/development.md)

## Default AI models

Current defaults live in `backend/app/config.py`:

- OpenAI text: `gpt-5.4`
- Gemini text: `gemini-2.5-pro`
- Anthropic text: `claude-sonnet-4-6`
- Image generation: `gemini-3-pro-image-preview` through the Google GenAI SDK

Change them with environment variables:

```bash
OPENAI_MODEL_ID=gpt-4.1
GEMINI_MODEL_ID=gemini-2.5-pro
ANTHROPIC_MODEL_ID=claude-sonnet-4-6
```

See [`docs/ai-models.md`](docs/ai-models.md) for the exact pipeline/provider behavior.

## Stripe: optional productization layer

DraftSpring includes Stripe Checkout, Customer Portal, webhooks, and subscription gating because the original experiment was a hosted SaaS.

If you want to productize it, configure:

```bash
STRIPE_SECRET_KEY=...
STRIPE_PRICE_ID=...
STRIPE_WEBHOOK_SECRET=...
STRIPE_TRIAL_DAYS=7
```

If you want to use it internally, leave Stripe empty and remove or bypass subscription gating as described in [`docs/stripe.md`](docs/stripe.md).

## Security note

No real Cloud Horizon/CofounderGPT production keys are included in this repository. The repo contains environment variable names and fake test placeholders only.

Before deploying your fork:

- Generate your own `ENCRYPTION_KEY`.
- Use your own Ghost Admin API key.
- Use your own model provider API keys.
- Use your own Stripe/Resend/S3 credentials if enabling those features.
- Never commit `.env`, Ghost production config, databases, logs, or uploaded content.

## License

MIT. Use it, fork it, strip it down, or turn it into something better.
