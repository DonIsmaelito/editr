"""
Findr TikTok Search Service

Searches TikTok for videos matching a query using Browser Use Skills.
Returns normalized VideoSearchResult objects that plug into the pipeline.

ARCHITECTURE:
  This service wraps the Browser Use skill execution and normalizes
  the raw skill output into Findr's VideoSearchResult schema. The
  actual browser interaction happens on Browser Use Cloud infrastructure.

HOW TIKTOK DIFFERS FROM YOUTUBE:
---------------------------------------------------------------------------
1. NO TRANSCRIPTS:
   TikTok videos don't have freely accessible transcripts like YouTube.
   Some videos have auto-captions, but these aren't exposed via any
   free API. This means the downstream transcript-based pipeline
   (embed segments → vector search → moment finder) does NOT apply
   to TikTok. Instead, TikTok results rely on:
   - Metadata matching (caption text, hashtags vs. query)
   - Visual verification (screenshot of the video → GPT vision)

2. NO URL TIMESTAMPS:
   TikTok embeds (tiktok.com/player/v1/{id}) don't support ?start=X
   parameters. You can programmatically seek via postMessage after
   iframe load, but that requires frontend JS — not a URL param.
   For Findr, TikTok results always embed the FULL video.

3. SHORT-FORM:
   TikTok videos are typically 15s-3min. There's no need for moment
   finding within a TikTok video — the whole video IS the moment.
   This simplifies the pipeline significantly.

4. ALWAYS DIRECT FORMAT:
   TikTok results are almost always "direct" output format.
   You don't need collapsable step-by-step sections for 30-second
   videos. The classifier should route TikTok to direct format.

5. METADATA STRUCTURE:
   TikTok metadata differs from YouTube:
   - "views" are strings like "1.2M" not integers
   - "duration" is often not available from search results
   - "hashtags" are a primary discovery mechanism
   - "creator" is the @handle, not a channel name

NUANCE ON SEARCH QUALITY:
   Browser Use skill-based search returns what's visible on TikTok's
   search page. TikTok's search algorithm is heavily personalized and
   influenced by trending content. The results may differ from what a
   logged-in user sees. For Findr's use case (finding specific content
   types), this is acceptable — we care about relevance to the query,
   not personalization.
---------------------------------------------------------------------------
"""

import logging
import time
from typing import Any, Dict, List, Optional

from src.agents.browser_skills import search_tiktok
from src.config import BROWSER_USE_API_KEY, TIKTOK_SEARCH_RESULTS
from src.models.schemas import Platform, VideoSearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL / ID extraction helpers
# ---------------------------------------------------------------------------

def _extract_tiktok_video_id(url: str) -> Optional[str]:
    """
    Extract the numeric video ID from a TikTok URL.

    TikTok URLs come in several formats:
      - https://www.tiktok.com/@user/video/7123456789012345678
      - https://vm.tiktok.com/ZMxxxxxx/ (short URL, resolves to above)
      - https://www.tiktok.com/t/ZMxxxxxx/ (another short format)

    We only handle the long format here. Short URLs would need to be
    resolved (follow redirect), which we skip for now.

    NUANCE: TikTok video IDs are 19-digit numbers (int64). They are
    unique identifiers used in the embed player URL.

    NUANCE: The skill should return full URLs, not short URLs, because
    it scrapes from the search results page which shows full links.
    """
    if not url:
        return None

    # Pattern: /video/DIGITS
    if "/video/" in url:
        parts = url.split("/video/")
        if len(parts) > 1:
            # Take digits before any query params
            video_id = parts[1].split("?")[0].split("/")[0].strip()
            if video_id.isdigit():
                return video_id

    # NUANCE: If the URL is a short link (vm.tiktok.com), we can't
    # extract the ID without following the redirect. For now, return
    # the whole URL as a fallback identifier. The embed URL won't
    # work with this, but we can still display the result with a
    # link to the original.
    logger.debug(f"[TikTok] Could not extract video ID from URL: {url[:80]}")
    return None


def _parse_view_count(views_str: str) -> int:
    """
    Parse TikTok view count strings like "1.2M", "45.3K", "890" into integers.

    NUANCE: TikTok displays counts in abbreviated form. The skill
    returns these as strings. We parse them for sorting/filtering.
    This parsing is best-effort — if it fails, we return 0.
    """
    if not views_str:
        return 0

    views_str = views_str.strip().upper().replace(",", "")

    try:
        if views_str.endswith("B"):
            return int(float(views_str[:-1]) * 1_000_000_000)
        elif views_str.endswith("M"):
            return int(float(views_str[:-1]) * 1_000_000)
        elif views_str.endswith("K"):
            return int(float(views_str[:-1]) * 1_000)
        else:
            return int(float(views_str))
    except (ValueError, IndexError):
        return 0


# ---------------------------------------------------------------------------
# Normalization: raw skill output → VideoSearchResult
# ---------------------------------------------------------------------------

def _normalize_tiktok_result(raw: Dict[str, Any]) -> Optional[VideoSearchResult]:
    """
    Convert a single raw skill output dict into a VideoSearchResult.

    NUANCE: The skill output schema is defined in the skill's goal
    (see browser_skills.py TIKTOK_SKILL_DEFINITION). But LLM-generated
    output is never 100% consistent — field names might vary slightly
    (e.g., "creator" vs "author" vs "username"). We handle common
    variations defensively.

    NUANCE: We set has_transcript=False because TikTok videos don't
    have accessible transcripts. This tells the pipeline to skip the
    transcript-based flow and use visual verification instead.
    """
    # Extract URL and video ID
    url = raw.get("url") or raw.get("video_url") or raw.get("link") or ""
    video_id = _extract_tiktok_video_id(url)

    if not url:
        logger.debug(f"[TikTok] Skipping result with no URL: {raw}")
        return None

    # Extract metadata with fallback field names
    title = (
        raw.get("title")
        or raw.get("caption")
        or raw.get("description")
        or raw.get("text")
        or "TikTok Video"
    )
    creator = (
        raw.get("creator")
        or raw.get("author")
        or raw.get("username")
        or raw.get("handle")
        or "Unknown"
    )
    views = raw.get("views") or raw.get("view_count") or raw.get("plays") or "0"
    hashtags = raw.get("hashtags") or raw.get("tags") or []

    # Build description from caption + hashtags
    description = title[:200]
    if hashtags:
        tag_str = " ".join(
            f"#{t}" if not t.startswith("#") else t
            for t in hashtags[:10]
        )
        description = f"{description} | {tag_str}"

    return VideoSearchResult(
        video_id=video_id or url,  # Fall back to URL as ID if extraction fails
        url=url,
        title=title[:150],
        duration=0,  # TikTok doesn't reliably expose duration in search results
        thumbnail="",  # Could be extracted but skill doesn't return it yet
        channel=creator,
        view_count=_parse_view_count(str(views)),
        description=description[:300],
        platform=Platform.TIKTOK,
        has_transcript=False,  # Critical: signals to skip transcript pipeline
    )


# ---------------------------------------------------------------------------
# Main search function
# ---------------------------------------------------------------------------

class TikTokSearchService:
    """TikTok search via Browser Use Skills."""

    async def search_videos(
        self,
        query: str,
        max_results: int = TIKTOK_SEARCH_RESULTS,
    ) -> List[VideoSearchResult]:
        """
        Search TikTok for videos matching the query.

        Args:
            query: Search query (from classifier's proposed_video_query).
            max_results: Maximum number of results to return.

        Returns:
            List of VideoSearchResult objects with platform=TIKTOK.
            Returns empty list if Browser Use is not configured or search fails.

        NUANCE: Unlike YouTube search (which runs yt-dlp in a thread),
        TikTok search is a cloud API call to Browser Use. It's slower
        (~10-15s) but requires no local browser installation.

        NUANCE: We request more results than max_results from the skill
        because some results may fail normalization (missing URL, etc.).
        We filter and return up to max_results valid results.
        """
        if not BROWSER_USE_API_KEY:
            logger.warning(
                "[TikTok] BROWSER_USE_API_KEY not configured — "
                "skipping TikTok search"
            )
            return []

        t0 = time.perf_counter()
        logger.info(
            f"[TikTok] Searching: '{query}' (max {max_results})"
        )

        # Request extra results to compensate for normalization failures
        # NUANCE: We ask for 2x because the skill might return some
        # results without valid URLs (e.g., ads, promoted content).
        raw_results = await search_tiktok(
            query=query,
            max_results=max_results * 2,
        )

        if not raw_results:
            elapsed = time.perf_counter() - t0
            logger.warning(
                f"[TikTok] Search returned no results after {elapsed:.2f}s. "
                "Likely blocked by login/anti-bot or skill execution failure."
            )
            return []

        # Normalize and filter
        results: List[VideoSearchResult] = []
        skipped = 0
        for raw in raw_results:
            video = _normalize_tiktok_result(raw)
            if video:
                results.append(video)
                if len(results) >= max_results:
                    break
            else:
                skipped += 1

        elapsed = time.perf_counter() - t0
        logger.info(
            f"[TikTok] Search complete in {elapsed:.2f}s | "
            f"raw={len(raw_results)}, accepted={len(results)}, "
            f"skipped={skipped} | query={query[:60]!r}"
        )

        for i, v in enumerate(results):
            logger.info(
                f"[TikTok]   [{i}] {v.title[:50]} by {v.channel} "
                f"({v.view_count:,} views)"
            )

        return results
