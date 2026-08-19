# Architecture

DraftSpring is a two-process app:

- **FastAPI web/API process** — authentication, settings, review links, dashboard API, Ghost connection checks, Stripe endpoints, and static frontend serving in production.
- **Worker process** — advances article batches through the long-running content pipeline.

The split is deliberate. AI calls, image generation, Ghost publishing, and review waits do not belong inside request/response handlers.

## Repository layout

```text
backend/
  app/
    main.py                  FastAPI app factory and route registration
    worker_main.py           worker entrypoint
    config.py                env-driven configuration
    database.py              SQLite connection + migrations
    providers.py             provider factories for LLM/storage/email
    llm/                     live/mock LLM providers and prompts
    pipeline/                article state machine and transitions
    routes/                  API routes
    services/                Ghost, email, encryption, blog analysis
    storage/                 local/S3 storage adapters
  migrations/                SQLite migrations
  scripts/                   operational helpers
  tests/                     backend tests
frontend/
  src/                       React dashboard
  public/                    static frontend assets
docs/                        setup and customization docs
```

## Data model

DraftSpring uses SQLite by default. `DATABASE_PATH=./data/ghostwriter.db` creates a local DB under `backend/data/`.

Core records include:

- users and magic-link sessions
- Ghost connection metadata
- seed batches
- generated ideas/outlines/articles
- usage/cost ledger entries
- review checkpoints
- publishing status

The app encrypts stored Ghost Admin API keys. Set `ENCRYPTION_KEY` before real use.

## Pipeline shape

The content pipeline is state-machine driven. The worker looks for eligible records and moves them through transitions:

1. Ideation
2. Idea approval checkpoint
3. Outlining
4. Drafting
5. Humanizing/editing
6. Critique/review
7. Media assembly
8. Article approval checkpoint
9. Revision if requested
10. Ghost publishing

Humans stay in the loop at checkpoints. This is not a blind “generate and spam the CMS” system. That was the right call.

## Integrations

- **Ghost Admin API** — validates Ghost access and publishes posts.
- **OpenAI / Gemini / Anthropic** — text generation, editing, critique, and image prompt work.
- **Google GenAI** — image generation with Gemini 3 Pro Image.
- **Resend** — magic links and review emails.
- **Stripe** — optional SaaS billing/subscription layer.
- **S3-compatible storage** — optional object storage for generated media.

## Production model

A production deployment typically runs:

```text
Nginx / reverse proxy
  -> FastAPI web process
       -> SQLite or external DB path
       -> frontend/dist static bundle
  -> Worker process
       -> same DB + storage
```

For serious multi-user SaaS use, you should review auth, rate limits, queueing, DB durability, backups, and provider cost controls before letting strangers use it.
