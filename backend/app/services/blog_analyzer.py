"""Blog Analyzer — scan any blog and build a reusable writing profile.

Works with Ghost, WordPress, Webflow, Hugo, Docusaurus, Substack, and any
site with a discoverable RSS/Atom feed. BlogProfile is cached in the
blog_profiles table for 7 days.
"""

import json
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
import structlog

from app.config import Config
from app.database import get_connection
from app.utils.url_validation import UA, validate_url, extract_base_url, is_private_ip, check_ghost, discover_rss_url

logger = structlog.get_logger()


# ── Data models ───────────────────────────────────────────────────────

@dataclass
class BlogProfile:
    id: str
    url: str
    site_name: str
    is_ghost: bool

    # What they write about
    topics: list[str] = field(default_factory=list)
    content_gaps: list[str] = field(default_factory=list)

    # How they write
    style_guide: str = ""
    example_sentences: list[str] = field(default_factory=list)

    # Enhanced fields (backward-compatible — default to empty)
    audience_description: str = ""
    tone_keywords: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)

    # Metadata
    avg_word_count: int = 0
    total_posts: int = 0
    latest_post_date: str = ""
    publishing_frequency: str = ""
    post_summaries: list[dict] = field(default_factory=list)

    analyzed_at: str = ""


@dataclass
class ArticleIdea:
    title: str
    angle: str
    article_type: str
    reasoning: str


# ── Errors ────────────────────────────────────────────────────────────

class BlogAnalyzerError(Exception):
    """User-facing error from the analyzer."""
    pass


# ── Analyzer ──────────────────────────────────────────────────────────

class BlogAnalyzer:
    """Scan a blog, build a writing profile, generate ideas."""

    def __init__(self, config: Config):
        self._config = config

    # ── Public API ────────────────────────────────────────────────────

    async def analyze(self, url: str) -> BlogProfile:
        """Scan a blog and build a reusable profile.

        1. Discover RSS (3-tier: direct check, HTML link tags, path guessing)
        2. Fetch last 10-20 posts via RSS
        3. Single Gemini call to extract structured profile
        4. Cache in blog_profiles table (keyed on site root)
        """
        clean_url = validate_url(url)
        if not clean_url:
            raise BlogAnalyzerError("Invalid URL. Please provide a valid HTTP(S) URL.")

        # Site root for cache key + path-based discovery
        base_url = extract_base_url(clean_url)

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # Discover RSS — pass both the original URL and the site root
            rss_text, rss_url = await discover_rss_url(client, base_url, original_url=clean_url)
            if not rss_text:
                raise BlogAnalyzerError(
                    "We couldn't find an RSS feed for this site. "
                    "Try entering your blog's homepage URL instead."
                )

            # Parse RSS to get posts
            posts = self._parse_rss_posts(rss_text)
            if len(posts) < 3:
                raise BlogAnalyzerError(
                    "We need at least 3 published posts to analyze your writing style."
                )

            # Extract site name from RSS
            site_name = self._extract_site_name(rss_text)

            # Fetch full content for up to 20 posts (enhanced from 15)
            posts_with_content = posts[:20]

            # Build the post data for Gemini
            posts_text = self._format_posts_for_llm(posts_with_content)

        # Single Gemini call to extract profile
        profile_data = await self._extract_profile_via_gemini(
            site_name=site_name,
            url=base_url,
            posts_text=posts_text,
            post_count=len(posts),
        )

        now = datetime.now(timezone.utc).isoformat()
        profile = BlogProfile(
            id=str(uuid.uuid4()),
            url=base_url,
            site_name=site_name,
            is_ghost=True,  # Validated at endpoint level
            topics=profile_data.get("topics", []),
            content_gaps=profile_data.get("content_gaps", []),
            style_guide=profile_data.get("style_guide", ""),
            example_sentences=profile_data.get("example_sentences", []),
            audience_description=profile_data.get("audience_description", ""),
            tone_keywords=profile_data.get("tone_keywords", []),
            strengths=profile_data.get("strengths", []),
            avg_word_count=profile_data.get("avg_word_count", 0),
            total_posts=len(posts),
            latest_post_date=posts[0].get("date", "") if posts else "",
            publishing_frequency=profile_data.get("publishing_frequency", "unknown"),
            post_summaries=[
                {"title": p.get("title", ""), "url": p.get("link", ""), "date": p.get("date", "")}
                for p in posts[:20]
            ],
            analyzed_at=now,
        )

        # Cache in DB
        await self._cache_profile(profile)

        logger.info(
            "blog_analyzed",
            url=clean_url,
            site_name=site_name,
            topics=profile.topics,
            post_count=profile.total_posts,
        )

        return profile

    async def generate_ideas(
        self, profile: BlogProfile, count: int = 10
    ) -> list[ArticleIdea]:
        """Generate article ideas from an existing profile."""
        system_prompt = """You are a content strategist for blogs. Given a blog profile,
generate article ideas that fit the blog's voice, topics, and audience.

IMPORTANT:
- Do NOT suggest articles that duplicate or closely mirror existing post titles.
- Each idea should fill a genuine gap or offer a fresh angle.
- Reasoning should reference the specific audience and why they'd care.

Return ONLY a JSON array of objects, each with:
- title: compelling article title
- angle: one sentence hook/angle
- article_type: one of "how-to", "listicle", "opinion", "tutorial", "deep-dive", "case-study"
- reasoning: why this fits their blog and audience (one sentence, specific)

Return valid JSON only — no markdown fences, no commentary."""

        # Build audience context if available
        audience_block = ""
        if profile.audience_description:
            audience_block = f"\nTarget audience: {profile.audience_description}\n"

        # Include existing titles to avoid duplicates
        existing_titles = [s.get('title', '') for s in profile.post_summaries if s.get('title')]
        existing_block = chr(10).join(f"- {t}" for t in existing_titles[:20])

        user_msg = f"""Blog: {profile.site_name} ({profile.url})
{audience_block}
Topics they cover: {', '.join(profile.topics)}

Content gaps (topics they should cover but haven't): {', '.join(profile.content_gaps)}

Their writing style:
{profile.style_guide}

Existing post titles (DO NOT duplicate these):
{existing_block}

Generate {count} article ideas that would resonate with their audience and fill content gaps."""

        raw, _ = await self._gemini_call(system_prompt, user_msg, temperature=0.7)
        ideas = self._parse_json_response(raw)

        if not isinstance(ideas, list):
            raise BlogAnalyzerError("Failed to generate article ideas.")

        return [
            ArticleIdea(
                title=idea.get("title", "Untitled"),
                angle=idea.get("angle", ""),
                article_type=idea.get("article_type", "how-to"),
                reasoning=idea.get("reasoning", ""),
            )
            for idea in ideas[:count]
        ]

    async def get_or_analyze(
        self, url: str, max_age_hours: int = 168
    ) -> BlogProfile:
        """Return cached profile if fresh enough (default 7 days), else re-analyze."""
        clean_url = validate_url(url)
        if not clean_url:
            raise BlogAnalyzerError("Invalid URL.")

        # Cache is keyed on site root, not the raw input URL
        base_url = extract_base_url(clean_url)

        cached = await self._get_cached_profile(base_url, max_age_hours)
        if cached:
            logger.info("blog_profile_cache_hit", url=base_url)
            return cached

        return await self.analyze(url)

    # ── RSS Parsing ───────────────────────────────────────────────────

    def _parse_rss_posts(self, rss_xml: str) -> list[dict]:
        """Parse RSS XML and extract post data."""
        posts = []
        try:
            root = ET.fromstring(rss_xml)
        except ET.ParseError:
            return posts

        # Handle namespaces for content:encoded
        namespaces = {"content": "http://purl.org/rss/1.0/modules/content/"}

        for item in root.iter("item"):
            post = {}
            post["title"] = (item.findtext("title") or "").strip()
            post["link"] = (item.findtext("link") or "").strip()
            post["date"] = ""

            pub_date = item.findtext("pubDate")
            if pub_date:
                try:
                    dt = parsedate_to_datetime(pub_date)
                    post["date"] = dt.isoformat()
                except Exception:
                    post["date"] = pub_date

            # Get full content (content:encoded) or description
            content_encoded = item.findtext("content:encoded", namespaces=namespaces)
            description = item.findtext("description")
            post["content"] = content_encoded or description or ""

            # Categories/tags
            categories = item.findall("category")
            post["tags"] = [c.text for c in categories if c.text]

            posts.append(post)

        return posts

    def _extract_site_name(self, rss_xml: str) -> str:
        """Extract site name from RSS channel title."""
        try:
            root = ET.fromstring(rss_xml)
            channel = root.find("channel")
            if channel is not None:
                title = channel.findtext("title")
                if title:
                    return title.strip()
        except ET.ParseError:
            pass
        return "Unknown Blog"

    def _format_posts_for_llm(self, posts: list[dict]) -> str:
        """Format posts for the Gemini analysis call.

        Strips HTML tags, truncates content to keep prompt manageable.
        """
        from bs4 import BeautifulSoup
        import re

        formatted = []
        for i, post in enumerate(posts, 1):
            title = post.get("title", "Untitled")
            date = post.get("date", "")
            tags = ", ".join(post.get("tags", []))
            content = post.get("content", "")

            # Strip HTML
            if content:
                soup = BeautifulSoup(content, "html.parser")
                text = soup.get_text(separator=" ", strip=True)
                # Truncate to ~2500 chars per post for thorough analysis
                text = text[:2500]
            else:
                text = "(no content)"

            entry = f"### Post {i}: {title}\n"
            if date:
                entry += f"Date: {date}\n"
            if tags:
                entry += f"Tags: {tags}\n"
            entry += f"Word count: ~{len(text.split())}\n\n{text}\n"
            formatted.append(entry)

        return "\n---\n".join(formatted)

    # ── Gemini API ────────────────────────────────────────────────────

    async def _gemini_call(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.3,
        max_tokens: int = 8000,
    ) -> tuple[str, dict]:
        """Call Gemini generateContent API. Same pattern as LiveLLM._gemini_generate()."""
        api_key = self._config.GEMINI_API_KEY
        if not api_key:
            raise BlogAnalyzerError("Gemini API key not configured.")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-pro:generateContent"
            f"?key={api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": user_content}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.error(
                    "gemini_api_error",
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                raise BlogAnalyzerError("Failed to analyze blog — AI service error.")

            data = resp.json()

        content = data["candidates"][0]["content"]["parts"][0]["text"]
        meta = data.get("usageMetadata", {})
        usage = {
            "input_tokens": meta.get("promptTokenCount", 0),
            "output_tokens": meta.get("candidatesTokenCount", 0),
        }
        return content, usage

    async def _extract_profile_via_gemini(
        self,
        site_name: str,
        url: str,
        posts_text: str,
        post_count: int,
    ) -> dict:
        """Single Gemini call to extract a structured blog profile."""
        system_prompt = """You are an expert content analyst. Analyze the blog posts provided
and extract a structured writing profile.

Return ONLY valid JSON with these fields:
- topics: list of 3-8 main topics the blog covers
- content_gaps: list of 3-5 topic areas adjacent to their coverage that they haven't explored
- style_guide: 2-3 paragraphs describing their writing style (tone, sentence structure, vocabulary level, quirks, formatting preferences, typical intro/conclusion patterns). This should be directly usable as instructions for an AI writer to mimic their voice.
- example_sentences: list of 5 representative sentences pulled verbatim from their posts that capture their voice
- audience_description: 1-2 sentences describing who reads this blog (their role, interests, expertise level)
- tone_keywords: list of 3-5 tone descriptors (e.g. "conversational", "technical", "witty", "authoritative")
- strengths: list of 3-5 things this blog does well (e.g. "clear code examples", "engaging storytelling", "practical takeaways")
- avg_word_count: estimated average word count per post (integer)
- publishing_frequency: string like "2x/week", "weekly", "biweekly", "monthly", "sporadic"

Return valid JSON only — no markdown fences, no commentary."""

        user_msg = f"""Blog: {site_name} ({url})
Total posts found: {post_count}

Here are their recent posts:

{posts_text}"""

        raw, _ = await self._gemini_call(
            system_prompt, user_msg, temperature=0.3, max_tokens=4000
        )
        return self._parse_json_response(raw)

    # ── JSON Parsing ──────────────────────────────────────────────────

    def _parse_json_response(self, raw: str) -> dict | list:
        """Parse JSON from Gemini response, stripping markdown fences if present."""
        text = raw.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            # Remove opening fence (possibly with language hint)
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("gemini_json_parse_error", raw=text[:500], error=str(e))
            raise BlogAnalyzerError("Failed to parse AI response.")

    # ── Database ──────────────────────────────────────────────────────

    async def _cache_profile(self, profile: BlogProfile) -> None:
        """Store profile in blog_profiles table."""
        profile_dict = asdict(profile)
        # Remove fields that are top-level columns
        profile_data = {
            k: v
            for k, v in profile_dict.items()
            if k not in ("id", "url", "site_name", "is_ghost", "analyzed_at")
        }

        async with get_connection(self._config.DATABASE_PATH) as db:
            await db.execute(
                """INSERT INTO blog_profiles (id, url, site_name, is_ghost, profile_data, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET
                     site_name = excluded.site_name,
                     is_ghost = excluded.is_ghost,
                     profile_data = excluded.profile_data,
                     analyzed_at = excluded.analyzed_at""",
                (
                    profile.id,
                    profile.url,
                    profile.site_name,
                    profile.is_ghost,
                    json.dumps(profile_data),
                    profile.analyzed_at,
                ),
            )
            await db.commit()

    async def _get_cached_profile(
        self, url: str, max_age_hours: int
    ) -> BlogProfile | None:
        """Get cached profile if within TTL."""
        async with get_connection(self._config.DATABASE_PATH) as db:
            row = await db.execute_fetchall(
                "SELECT * FROM blog_profiles WHERE url = ?", (url,)
            )
            if not row:
                return None

            row = row[0]
            analyzed_at_str = row["analyzed_at"]
            try:
                analyzed_at = datetime.fromisoformat(analyzed_at_str)
                if analyzed_at.tzinfo is None:
                    analyzed_at = analyzed_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return None

            now = datetime.now(timezone.utc)
            age_hours = (now - analyzed_at).total_seconds() / 3600
            if age_hours > max_age_hours:
                return None

            # Reconstruct profile from JSON
            profile_data = json.loads(row["profile_data"])
            return BlogProfile(
                id=row["id"],
                url=row["url"],
                site_name=row["site_name"] or "",
                is_ghost=bool(row["is_ghost"]),
                topics=profile_data.get("topics", []),
                content_gaps=profile_data.get("content_gaps", []),
                style_guide=profile_data.get("style_guide", ""),
                example_sentences=profile_data.get("example_sentences", []),
                audience_description=profile_data.get("audience_description", ""),
                tone_keywords=profile_data.get("tone_keywords", []),
                strengths=profile_data.get("strengths", []),
                avg_word_count=profile_data.get("avg_word_count", 0),
                total_posts=profile_data.get("total_posts", 0),
                latest_post_date=profile_data.get("latest_post_date", ""),
                publishing_frequency=profile_data.get("publishing_frequency", ""),
                post_summaries=profile_data.get("post_summaries", []),
                analyzed_at=analyzed_at_str,
            )
