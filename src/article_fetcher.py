from __future__ import annotations

import re
from urllib import error, request

import trafilatura


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def fetch_article_text(url: str, timeout_seconds: int = 20, max_chars: int = 12000) -> str:
    req = request.Request(
        url=url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            raw_html = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Connection error: {exc.reason}") from exc

    extracted = trafilatura.extract(
        raw_html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )
    if not extracted:
        return ""

    cleaned = re.sub(r"\s+", " ", extracted).strip()
    if not cleaned:
        return ""
    if len(cleaned) > max_chars:
        return cleaned[:max_chars].rstrip() + "..."
    return cleaned
