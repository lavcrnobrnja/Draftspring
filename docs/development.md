# Development

## Backend setup

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8223
```

Run tests:

```bash
cd backend
source .venv/bin/activate
pytest
```

Run only fast/unit-ish tests while iterating:

```bash
pytest tests/test_llm_mock.py tests/test_schema.py tests/test_utilities.py
```

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Build:

```bash
npm run build
```

Test/lint if you keep the existing tooling:

```bash
npm run lint
npx vitest run
```

## Full local app

Terminal 1:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8223
```

Terminal 2:

```bash
cd backend
source .venv/bin/activate
python -m app.worker_main
```

Terminal 3:

```bash
cd frontend
npm run dev
```

## Production-ish build

Build the frontend:

```bash
cd frontend
npm run build
```

Then start the backend. `backend/app/main.py` serves `frontend/dist` automatically when it exists beside the backend directory.

## Common customizations

- Use a single model provider: edit `backend/app/llm/live.py` and keep `backend/app/llm/base.py` method contracts stable.
- Disable Stripe: see `docs/stripe.md`.
- Change review behavior: inspect `backend/app/pipeline/transitions/` and review routes.
- Change publishing rules: inspect `backend/app/services/ghost.py` and `backend/app/pipeline/transitions/t11_publishing.py`.
- Change UI gates/routes: inspect `frontend/src/App.jsx` and `frontend/src/components/RequireSubscription.jsx`.

## Open-source hygiene

Before publishing a fork:

```bash
git status --short
find . -name '.env' -o -name '*.sqlite' -o -name '*.db' -o -name 'config.production.json'
```

No secrets, no databases, no production configs. Boring rule. Saves lives.
