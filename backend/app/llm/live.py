"""Live LLM provider — real API calls to OpenAI, Gemini, Anthropic, and Gemini Image Gen."""

import asyncio
import json
import logging
from io import BytesIO

import httpx

from app.config import Config
from app.llm.base import LLMProvider
from app.llm.prompts import (
    ideation_system_prompt,
    outline_system_prompt,
    drafting_system_prompt,
    humanizer_system_prompt,
    critique_system_prompt,
    critique_user_message,
    image_prompter_system_prompt,
    image_prompter_user_message,
    alt_text_system_prompt,
)
from app.llm.rate_limiter import RateLimiterRegistry

logger = logging.getLogger(__name__)

# Backoff schedule for 5xx retries (seconds)
BACKOFF_SCHEDULE = [2, 8, 32]


class LLMError(Exception):
    """Raised on non-retryable LLM errors (4xx except 429)."""
    pass


class LiveLLM(LLMProvider):
    """Real LLM provider using OpenAI, Gemini, and Anthropic APIs."""

    def __init__(self, config: Config):
        self._config = config
        self._rate_limiters = RateLimiterRegistry()
        self._anthropic_via_proxy = bool(config.ANTHROPIC_BASE_URL)

        # Persistent HTTP clients.
        # Timeouts set to 100s — provider latency spikes observed Apr 18 2026
        # pushed humanize/draft calls (max_tokens=8000) from ~40s to 70s. 100s
        # gives headroom for occasional spikes without masking real hangs.
        # If timeouts recur, investigate streaming rather than raising further.
        self._openai_client = httpx.AsyncClient(
            base_url=config.OPENAI_BASE_URL,
            headers={
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=100.0,
        )

        if self._anthropic_via_proxy:
            # Route through OpenAI-compatible proxy (e.g. claude-max-api-proxy)
            self._anthropic_client = httpx.AsyncClient(
                base_url=config.ANTHROPIC_BASE_URL,
                headers={"Content-Type": "application/json"},
                timeout=100.0,
            )
        else:
            self._anthropic_client = httpx.AsyncClient(
                base_url="https://api.anthropic.com",
                headers={
                    "x-api-key": config.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                timeout=100.0,
            )
        self._gemini_client = httpx.AsyncClient(
            timeout=100.0,
        )
        # Image gen uses google-genai SDK (Gemini 3 Pro Image)
        self._gemini_image_client = None
        if config.GEMINI_API_KEY:
            try:
                from google import genai
                self._gemini_image_client = genai.Client(api_key=config.GEMINI_API_KEY)
            except ImportError:
                logger.warning("google-genai not installed — image generation disabled")

    async def close(self):
        """Close all HTTP clients."""
        await self._openai_client.aclose()
        await self._anthropic_client.aclose()
        await self._gemini_client.aclose()

    # ─── Internal API call methods ───

    async def _openai_chat(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        json_mode: bool = False,
    ) -> tuple[str, dict]:
        """Call OpenAI chat completions. Returns (content, usage_dict)."""
        await self._rate_limiters.get("openai").acquire()

        payload = {
            "model": self._config.OPENAI_MODEL_ID,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        request = self._openai_client.build_request("POST", "/chat/completions", json=payload)
        resp = await self._request_with_retry(self._openai_client, request, "openai")
        data = resp.json()

        content = data["choices"][0]["message"]["content"]
        usage = {
            "input_tokens": data.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": data.get("usage", {}).get("completion_tokens", 0),
        }
        return content, usage

    async def _anthropic_message(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.4,
        max_tokens: int = 8000,
    ) -> tuple[str, dict]:
        """Call Anthropic Messages API. Returns (content, usage_dict).

        Supports both native Anthropic API and OpenAI-compatible proxy.
        """
        await self._rate_limiters.get("anthropic").acquire()

        if self._anthropic_via_proxy:
            # OpenAI-compatible format (for claude-max-api-proxy etc.)
            payload = {
                "model": self._config.ANTHROPIC_MODEL_ID,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": temperature,
            }
            request = self._anthropic_client.build_request("POST", "/v1/chat/completions", json=payload)
            resp = await self._request_with_retry(self._anthropic_client, request, "anthropic")
            data = resp.json()

            content = data["choices"][0]["message"]["content"]
            usage = {
                "input_tokens": data.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": data.get("usage", {}).get("completion_tokens", 0),
            }
        else:
            # Native Anthropic Messages API
            payload = {
                "model": self._config.ANTHROPIC_MODEL_ID,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_content}],
                "temperature": temperature,
            }
            request = self._anthropic_client.build_request("POST", "/v1/messages", json=payload)
            resp = await self._request_with_retry(self._anthropic_client, request, "anthropic")
            data = resp.json()

            content = data["content"][0]["text"]
            usage = {
                "input_tokens": data.get("usage", {}).get("input_tokens", 0),
                "output_tokens": data.get("usage", {}).get("output_tokens", 0),
            }

        return content, usage

    async def _gemini_generate(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.3,
        max_tokens: int = 8000,
    ) -> tuple[str, dict]:
        """Call Gemini generateContent API. Returns (content, usage_dict)."""
        await self._rate_limiters.get("gemini").acquire()

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._config.GEMINI_MODEL_ID}:generateContent"
            f"?key={self._config.GEMINI_API_KEY}"
        )
        payload = {
            "contents": [{"parts": [{"text": user_content}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        request = self._gemini_client.build_request("POST", url, json=payload)
        resp = await self._request_with_retry(self._gemini_client, request, "gemini")
        data = resp.json()

        content = data["candidates"][0]["content"]["parts"][0]["text"]
        meta = data.get("usageMetadata", {})
        usage = {
            "input_tokens": meta.get("promptTokenCount", 0),
            "output_tokens": meta.get("candidatesTokenCount", 0),
        }
        return content, usage

    async def _generate_image_raw(self, prompt: str) -> bytes:
        """Generate image via Gemini 3 Pro Image API. Returns PNG bytes."""
        if not self._gemini_image_client:
            raise LLMError("Image generation not available — google-genai not configured")

        await self._rate_limiters.get("nano_banana").acquire()

        from google.genai import types
        from PIL import Image as PILImage

        # Run sync SDK call in executor to avoid blocking event loop
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._gemini_image_client.models.generate_content(
                model="gemini-3-pro-image-preview",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=types.ImageConfig(
                        image_size="1K",
                        aspect_ratio="16:9",
                    ),
                ),
            ),
        )

        # Extract image bytes from response
        for part in response.parts:
            if part.inline_data is not None:
                image_data = part.inline_data.data
                if isinstance(image_data, str):
                    import base64
                    image_data = base64.b64decode(image_data)
                # Convert to PNG bytes
                image = PILImage.open(BytesIO(image_data))
                buf = BytesIO()
                image.convert("RGB").save(buf, "PNG")
                return buf.getvalue()

        raise LLMError("Gemini image generation returned no image")

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        request: httpx.Request,
        provider: str,
    ) -> httpx.Response:
        """Execute request with retry logic.

        - 429: wait Retry-After header, retry up to 3 times
        - 5xx: exponential backoff (2s, 8s, 32s)
        - 4xx: raise immediately
        - Timeout: retry once
        """
        max_retries = 3
        timeout_retried = False

        for attempt in range(max_retries + 1):
            try:
                resp = await client.send(request)

                if resp.status_code == 429:
                    if attempt >= max_retries:
                        raise LLMError(f"{provider}: Rate limited after {max_retries} retries")
                    retry_after = float(resp.headers.get("Retry-After", "1"))
                    logger.warning(f"{provider}: 429 rate limited, waiting {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue

                if 500 <= resp.status_code < 600:
                    if attempt >= max_retries:
                        raise LLMError(f"{provider}: Server error {resp.status_code} after {max_retries} retries")
                    backoff = BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)]
                    logger.warning(f"{provider}: {resp.status_code}, backing off {backoff}s")
                    await asyncio.sleep(backoff)
                    continue

                if 400 <= resp.status_code < 500:
                    error_detail = resp.text[:500]
                    raise LLMError(f"{provider}: Client error {resp.status_code}: {error_detail}")

                return resp

            except httpx.TimeoutException:
                if not timeout_retried:
                    timeout_retried = True
                    logger.warning(f"{provider}: Timeout, retrying once")
                    continue
                raise LLMError(f"{provider}: Timeout after retry")

        raise LLMError(f"{provider}: Exhausted retries")

    # ─── Public LLM methods ───

    async def describe_image(self, image_bytes: bytes) -> str:
        """Describe an image using Claude Sonnet 4.6 with vision."""
        import base64

        await self._rate_limiters.get("anthropic").acquire()

        prompt = (
            "Describe this image in 2-3 sentences. State what is depicted (subjects, setting, "
            "objects), the overall mood or feel, and any notable visual details like lighting, "
            "color palette, or composition. Be specific and concrete. This description will be "
            "used to inform writers and image generators working on a blog article, so focus on "
            "what would be useful for editorial context."
        )

        img_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # Detect mime type from bytes
        mime_type = "image/jpeg"
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            mime_type = "image/png"
        elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            mime_type = "image/webp"
        elif image_bytes[:3] == b"GIF":
            mime_type = "image/gif"

        if self._anthropic_via_proxy:
            payload = {
                "model": self._config.ANTHROPIC_MODEL_ID,
                "max_tokens": 500,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{img_b64}",
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                "temperature": 0.3,
            }
            request = self._anthropic_client.build_request("POST", "/v1/chat/completions", json=payload)
            resp = await self._request_with_retry(self._anthropic_client, request, "anthropic")
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        else:
            payload = {
                "model": self._config.ANTHROPIC_MODEL_ID,
                "max_tokens": 500,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": img_b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                "temperature": 0.3,
            }
            request = self._anthropic_client.build_request("POST", "/v1/messages", json=payload)
            resp = await self._request_with_retry(self._anthropic_client, request, "anthropic")
            data = resp.json()
            return data["content"][0]["text"]

    async def generate_ideas(
        self, seeds: list[dict], ideas_per_seed: int, existing_titles: list[str],
        feedback: str | None = None, rejected_titles: list[str] | None = None,
        ghost_url: str = "blog", brand_voice: str | None = None,
        user_images: list[dict] | None = None,
    ) -> dict:
        system = ideation_system_prompt(
            ghost_url=ghost_url,
            ideas_per_seed=ideas_per_seed,
            brand_voice=brand_voice,
            existing_titles=existing_titles,
            feedback=feedback,
            rejected_titles=rejected_titles,
        )

        # Build brief-format user message
        # Extract description from first topic seed
        description = ""
        user_keywords = ""
        reference_urls = []
        for seed in seeds:
            if seed.get("seed_type") == "topic":
                content = seed.get("content", "")
                # Split out keywords if appended
                if "\n\nKeywords:" in content:
                    parts = content.split("\n\nKeywords:", 1)
                    description = parts[0]
                    user_keywords = parts[1].strip()
                else:
                    description = content
            elif seed.get("seed_type") == "url":
                url_entry = {"url": seed.get("content", "")}
                if seed.get("extracted_content"):
                    url_entry["extracted_content"] = seed["extracted_content"]
                reference_urls.append(url_entry)

        brief = {"description": description}
        if user_keywords:
            brief["user_keywords"] = user_keywords
        if user_images:
            brief["user_images"] = user_images

        msg_data = {
            "brief": brief,
            "ideas_requested": ideas_per_seed,
        }
        if reference_urls:
            msg_data["reference_urls"] = reference_urls
        user_msg = json.dumps(msg_data)

        content, usage = await self._openai_chat(
            system_prompt=system,
            user_content=user_msg,
            temperature=0.8,
            max_tokens=4000,
            json_mode=True,
        )

        raw = json.loads(content)

        # Normalize response: flatten per-seed results and add seed_index
        ideas = []
        if "ideas" in raw:
            # Already flat format
            for i, idea in enumerate(raw["ideas"]):
                idea.setdefault("seed_index", 0)
                idea.setdefault("target_keyword", idea.pop("seo_keyword", ""))
                ideas.append(idea)
        elif "results" in raw:
            # Per-seed results format: results[].ideas[]
            for seed_idx, group in enumerate(raw["results"]):
                for idea in group.get("ideas", []):
                    idea["seed_index"] = seed_idx
                    idea.setdefault("target_keyword", idea.pop("seo_keyword", ""))
                    ideas.append(idea)

        return {"ideas": ideas, "_usage": usage}

    async def generate_outline(
        self,
        idea: dict,
        blog_context: dict,
        target_word_count: int,
        previous_feedback: str | None = None,
        content_brief: dict | None = None,
    ) -> dict:
        brand_voice = blog_context.get("brand_voice", "Professional but conversational.")
        system = outline_system_prompt(
            target_word_count=target_word_count,
            brand_voice=brand_voice,
        )
        msg_payload = {
            "idea": idea,
            "blog_context": blog_context,
            "target_word_count": target_word_count,
        }
        if content_brief:
            msg_payload["content_brief"] = content_brief
        user_msg = json.dumps(msg_payload)
        if previous_feedback:
            user_msg += f"\n\nPrevious feedback: {previous_feedback}"

        content, usage = await self._gemini_generate(
            system_prompt=system,
            user_content=user_msg,
            temperature=0.3,
            max_tokens=8000,
        )

        # Strip markdown fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        result = json.loads(content)
        result["_usage"] = usage
        return result

    async def draft_article(
        self,
        outline: dict,
        seo_meta: dict,
        brand_voice: str | None = None,
        focus_keyword: str = "",
        article_title: str = "",
        previous_critique: dict | None = None,
        previous_score: int | None = None,
        user_revision_notes: str | None = None,
        target_word_count: int = 1500,
        iteration_number: int = 1,
        content_brief: dict | None = None,
    ) -> str:
        from datetime import datetime, timezone
        critique_json = json.dumps(previous_critique) if previous_critique else None
        current_year = datetime.now(timezone.utc).year
        system = drafting_system_prompt(
            target_word_count=target_word_count,
            focus_keyword=focus_keyword,
            brand_voice=brand_voice,
            current_year=current_year,
            previous_critique_json=critique_json,
            previous_score=previous_score,
            iteration_number=iteration_number,
            user_revision_notes=user_revision_notes,
        )
        user_msg = f"""Write the article from this outline.

Article title: {article_title}
Focus keyword: {focus_keyword}
Target word count: {target_word_count}

---

{json.dumps(outline, indent=2)}"""

        if content_brief:
            brief_parts = ["\n\n---\n\nCONTENT BRIEF (the user's original input for this article):"]
            if content_brief.get("user_description"):
                brief_parts.append(f"\nDescription: {content_brief['user_description']}")
            if content_brief.get("reference_materials"):
                brief_parts.append("\nReference materials:")
                for ref in content_brief["reference_materials"]:
                    url = ref.get("url", "")
                    extracted = ref.get("extracted_content", "")
                    if extracted:
                        # Truncate to keep prompt manageable
                        brief_parts.append(f"- {url}: {extracted[:3000]}")
                    else:
                        brief_parts.append(f"- {url}")
            if content_brief.get("user_keywords"):
                kw = content_brief["user_keywords"]
                if isinstance(kw, list):
                    kw = ", ".join(kw)
                brief_parts.append(f"\nUser's suggested keywords: {kw}")
            if content_brief.get("user_images"):
                brief_parts.append("\nUser-provided photos:")
                for img in content_brief["user_images"]:
                    role = img.get("role", "body")
                    desc = img.get("description", "No description")
                    brief_parts.append(f"- {role}: {desc}")
            user_msg += "\n".join(brief_parts)

        content, usage = await self._openai_chat(
            system_prompt=system,
            user_content=user_msg,
            temperature=0.6,
            max_tokens=8000,
        )
        return content

    async def humanize(self, draft_md: str, brand_voice: str = "", focus_keyword: str = "", article_title: str = "") -> str:
        system = humanizer_system_prompt(brand_voice=brand_voice, focus_keyword=focus_keyword)

        user_msg = f"""Edit this article for voice and naturalness. Do not change the structure, arguments, or facts.

Article title: {article_title}
Focus keyword: {focus_keyword}

---

{draft_md}"""

        content, usage = await self._anthropic_message(
            system_prompt=system,
            user_content=user_msg,
            temperature=0.4,
            max_tokens=8000,
        )
        return content

    async def critique_draft(
        self,
        humanized_md: str,
        outline: dict,
        seo_meta: dict,
        iteration_number: int,
        max_iterations: int,
        previous_critique: dict | None = None,
        article_title: str = "",
        article_angle: str = "",
        search_intent: str = "",
        focus_keyword: str = "",
        brand_voice: str | None = None,
        target_word_count: int | None = None,
        meta_description: str | None = None,
        user_description: str | None = None,
        user_keywords: str | None = None,
    ) -> dict:
        # Extract previous score and issues for the prompt (iteration 2+)
        previous_score = None
        previous_issues_json = None
        if previous_critique:
            previous_score = previous_critique.get("overall_score", previous_critique.get("score"))
            previous_issues = previous_critique.get("issues", [])
            if previous_issues:
                previous_issues_json = json.dumps(previous_issues, indent=2)

        system = critique_system_prompt(
            iteration_number=iteration_number,
            max_iterations=max_iterations,
            article_title=article_title,
            article_angle=article_angle,
            search_intent=search_intent,
            focus_keyword=focus_keyword,
            brand_voice=brand_voice,
            previous_score=previous_score,
            previous_issues_json=previous_issues_json,
        )
        user_msg = critique_user_message(
            article_title=article_title,
            focus_keyword=focus_keyword,
            target_word_count=target_word_count or 1500,
            meta_description=meta_description or seo_meta.get("meta_description", ""),
            humanized_draft_text=humanized_md,
            user_description=user_description,
            user_keywords=user_keywords,
        )

        content, usage = await self._anthropic_message(
            system_prompt=system,
            user_content=user_msg,
            temperature=0.2,
            max_tokens=3000,
        )

        # Strip markdown fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # Retry once asking Claude to fix its output
            fix_content, _ = await self._anthropic_message(
                system_prompt="Fix this JSON so it is valid. Output ONLY the corrected JSON.",
                user_content=content,
                temperature=0.0,
                max_tokens=3000,
            )
            fix_content = fix_content.strip()
            if fix_content.startswith("```"):
                fix_content = fix_content.split("\n", 1)[1]
                if fix_content.endswith("```"):
                    fix_content = fix_content[:-3]
            result = json.loads(fix_content.strip())

        result["_usage"] = usage
        return result

    async def generate_image_prompts(
        self, article_title: str, focus_keyword: str, article_text: str,
        image_slots: list[dict] | None = None,
        user_photo_descriptions: list[dict] | None = None,
        user_revision_notes: str | None = None,
        image_style_direction: str | None = None,
    ) -> dict:
        system = image_prompter_system_prompt()
        user_msg = image_prompter_user_message(
            article_title, focus_keyword, article_text,
            image_slots=image_slots,
            user_photo_descriptions=user_photo_descriptions,
            user_revision_notes=user_revision_notes,
            image_style_direction=image_style_direction,
        )

        content, usage = await self._anthropic_message(
            system_prompt=system,
            user_content=user_msg,
            temperature=0.5,
            max_tokens=4000,
        )

        # Strip markdown fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # Retry once asking Claude to fix its output
            fix_content, _ = await self._anthropic_message(
                system_prompt="Fix this JSON so it is valid. Output ONLY the corrected JSON.",
                user_content=content,
                temperature=0.0,
                max_tokens=4000,
            )
            fix_content = fix_content.strip()
            if fix_content.startswith("```"):
                fix_content = fix_content.split("\n", 1)[1]
                if fix_content.endswith("```"):
                    fix_content = fix_content[:-3]
            result = json.loads(fix_content.strip())

        result["_usage"] = usage
        return result

    async def generate_alt_texts(
        self, focus_keyword: str, images: list[dict]
    ) -> dict:
        system = alt_text_system_prompt()
        user_msg = json.dumps({
            "focus_keyword": focus_keyword,
            "images": [
                {
                    "section_heading": img.get("section_heading", ""),
                    "image_guidance": img.get("image_guidance", ""),
                }
                for img in images
            ],
        })

        content, usage = await self._openai_chat(
            system_prompt=system,
            user_content=user_msg,
            temperature=0.3,
            max_tokens=1000,
            json_mode=True,
        )

        result = json.loads(content)
        result["_usage"] = usage
        return result

    async def generate_image(self, prompt: str) -> bytes:
        return await self._generate_image_raw(prompt)
