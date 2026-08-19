"""Fetch URL content and extract readable text for seed processing."""

import httpx
from bs4 import BeautifulSoup

MAX_CHARS = 3000
TIMEOUT_SECONDS = 10


async def fetch_url_content(url: str) -> dict:
    """Fetch a URL and extract readable text.
    
    Returns:
        {"url": str, "extracted_content": str, "error": str|None}
    """
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": "DraftSpring/1.0 (Content Research Bot)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove non-content elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"]):
            tag.decompose()

        # Extract text
        text = soup.get_text(separator="\n", strip=True)

        # Collapse multiple newlines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)

        # Truncate to MAX_CHARS
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + "…"

        if not text:
            return {"url": url, "extracted_content": None, "error": "No readable content found"}

        return {"url": url, "extracted_content": text, "error": None}

    except httpx.TimeoutException:
        return {"url": url, "extracted_content": None, "error": "Timeout fetching URL"}
    except httpx.HTTPStatusError as e:
        return {"url": url, "extracted_content": None, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"url": url, "extracted_content": None, "error": str(e)[:200]}
