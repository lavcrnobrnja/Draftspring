# Ghost setup

DraftSpring publishes to Ghost through the Ghost Admin API.

## Create a Ghost Admin API key

In Ghost Admin:

1. Go to **Settings → Integrations**.
2. Create a custom integration.
3. Copy:
   - Admin API URL / site URL
   - Admin API key

Then set:

```bash
GHOST_URL=https://your-ghost-site.example
GHOST_ADMIN_API_KEY=your_ghost_admin_api_key
```

## Per-user Ghost settings

The app can store Ghost connection details per user. The global `GHOST_URL` and `GHOST_ADMIN_API_KEY` values are fallbacks when a user row has no stored Ghost URL/key.

Stored Ghost Admin keys are encrypted. Set `ENCRYPTION_KEY` before real use.

## What gets published

DraftSpring assembles approved article content, metadata, image references, and publishing schedule into Ghost posts through the Admin API.

The exact publishing behavior lives in:

```text
backend/app/services/ghost.py
backend/app/pipeline/transitions/t11_publishing.py
```

## What is not included

This repo does not include a Ghost theme. Use your existing Ghost theme. DraftSpring is the publishing automation layer, not the public blog design.

## Safety tips

- Use a staging Ghost site first.
- Use a dedicated Ghost integration key.
- Keep the Ghost Admin key out of git.
- Verify generated posts as drafts before enabling automatic publish.
- Add your own editorial policy and brand review gates if publishing for a real business.
