# AI models and provider keys

DraftSpring supports multiple model providers because different parts of the publishing pipeline benefit from different models.

## Defaults

Defaults are defined in `backend/app/config.py`:

```bash
OPENAI_MODEL_ID=gpt-5.4
GEMINI_MODEL_ID=gemini-2.5-pro
ANTHROPIC_MODEL_ID=claude-sonnet-4-6
```

Image generation currently uses:

```text
gemini-3-pro-image-preview
```

inside `backend/app/llm/live.py` through the Google GenAI SDK.

## Development vs production behavior

Provider selection is in `backend/app/providers.py`:

- `APP_ENV=development` or `APP_ENV=test` → `MockLLM`
- anything else, normally `APP_ENV=production` → `LiveLLM`

So you can run the app locally without API keys first. Switch to production mode only when you are ready to use live providers.

## API keys

Set your own keys in `backend/.env`:

```bash
OPENAI_API_KEY=your-openai-key
GEMINI_API_KEY=your-google-ai-key
ANTHROPIC_API_KEY=your-anthropic-key
```

Do not put keys in source code. Do not commit `.env`.

## OpenAI-compatible endpoints

OpenAI calls use:

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
```

You can point this at an OpenAI-compatible gateway if your models are served elsewhere:

```bash
OPENAI_BASE_URL=https://your-compatible-endpoint/v1
OPENAI_MODEL_ID=your-model-name
```

## Anthropic proxy support

By default Anthropic uses the native API:

```bash
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=
```

If `ANTHROPIC_BASE_URL` is set, DraftSpring treats it as an OpenAI-compatible proxy and sends chat-completions-style requests to `/v1/chat/completions` using `ANTHROPIC_MODEL_ID`.

## How to change models

For config-only changes, edit `.env`:

```bash
OPENAI_MODEL_ID=gpt-4.1
GEMINI_MODEL_ID=gemini-2.5-flash
ANTHROPIC_MODEL_ID=claude-sonnet-4-6
```

For routing changes, edit `backend/app/llm/live.py`. The public methods in `LiveLLM` decide which provider handles ideation, outlining, drafting, critique, humanization, image prompting, and image generation.

## How to standardize on one provider

If you want a simpler internal setup:

1. Keep one API key/model family.
2. Replace provider-specific calls in `LiveLLM` with wrappers around your chosen provider.
3. Keep method signatures from `backend/app/llm/base.py` unchanged so the pipeline does not care.
4. Run tests after changing provider behavior.

## Cost warning

The defaults were chosen for quality, not cheapest possible throughput. If you run this on a busy blog or open it to users, set realistic cost ceilings and monitor provider usage directly from each provider dashboard.
