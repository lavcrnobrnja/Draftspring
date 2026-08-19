# Configuration

DraftSpring is configured with environment variables loaded by `backend/app/config.py`. Copy the example file first:

```bash
cd backend
cp .env.example .env
```

## Required for real use

```bash
APP_ENV=production
APP_BASE_URL=https://your-app.example
ENCRYPTION_KEY=...
GHOST_URL=https://your-ghost-site.example
GHOST_ADMIN_API_KEY=your_ghost_admin_api_key
```

Generate an encryption key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`ENCRYPTION_KEY` protects stored Ghost Admin API keys. Do not rotate it casually unless you also re-encrypt stored secrets.

## Development mode

```bash
APP_ENV=development
APP_BASE_URL=http://localhost:8223
DATABASE_PATH=./data/ghostwriter.db
CORS_ORIGINS=http://localhost:5173,http://localhost:8223
```

In `development` and `test`, DraftSpring uses `MockLLM` by default. That lets the app boot and tests run without spending model tokens.

## Production mode

`APP_ENV=production` switches provider creation to `LiveLLM`, which uses your configured model provider keys.

At minimum, configure the providers used by your pipeline:

```bash
OPENAI_API_KEY=...
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
```

You can use all three or adapt the provider code to standardize on one.

## Storage

Local storage:

```bash
STORAGE_PROVIDER=local
LOCAL_STORAGE_PATH=./data/storage
```

S3-compatible storage:

```bash
STORAGE_PROVIDER=s3
S3_ENDPOINT_URL=https://...
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=draftspring
S3_PUBLIC_URL_PREFIX=https://cdn.example.com/draftspring
```

## Email

Magic links and review links use email.

```bash
EMAIL_PROVIDER=resend
RESEND_API_KEY=...
EMAIL_FROM_ADDRESS=content@example.com
EMAIL_FROM_NAME=DraftSpring
```

In development, login routes can return a `dev_verify_url` directly.

## Cookies and CORS

Local:

```bash
COOKIE_DOMAIN=localhost
COOKIE_SECURE=false
COOKIE_SAMESITE=lax
CORS_ORIGINS=http://localhost:5173,http://localhost:8223
```

Production:

```bash
COOKIE_DOMAIN=your-app.example
COOKIE_SECURE=true
COOKIE_SAMESITE=lax
CORS_ORIGINS=https://your-app.example
```

## Cost controls

```bash
PER_ARTICLE_COST_CEILING_CENTS=250
MAX_CONCURRENT_ARTICLES=10
DAILY_IMAGE_GENERATION_CAP=50
```

These are guardrails, not accounting-grade billing controls. Review them before public SaaS use.

## Never commit

- `.env`
- SQLite databases
- generated media/storage
- Ghost production config
- API keys
- logs
