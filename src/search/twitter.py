"""
Findr X/Twitter Search Service

Searches X (formerly Twitter) for posts matching a query using
Browser Use Skills. Returns normalized VideoSearchResult objects.

HOW X/TWITTER DIFFERS FROM YOUTUBE AND TIKTOK:
---------------------------------------------------------------------------
1. TEXT-FIRST PLATFORM:
   Unlike YouTube and TikTok which are video-first, X posts are
   primarily text. Video content exists but is secondary. For Findr,
   we search X when the query is about public discourse, reactions,
   commentary, or news — "what did Elon say about AI today" type queries.

2. NO TRANSCRIPTS, NO TIMESTAMPS:
   X video posts don't have transcripts or URL-based timestamps.
   Moment finding within an X video is not possible via URL params.
   The "moment" IS the entire post — text + media combined.

3. EMBED MECHANISM:
   X posts are NOT embedded via iframes. The frontend uses the
   react-tweet component which fetches post data via X's oEmbed API
   and renders it natively. The embed "URL" (x.com/i/status/{id})
   is a reference for the react-tweet component, not a real iframe src.

   NUANCE: This means the visual_verify agent can't screenshot an X
   embed the same way it screenshots a YouTube embed. For X, visual
   verification would need to navigate to the actual post URL
   (x.com/user/status/id) and screenshot the full post card.

4. RELEVANCE FILTERING:
   Since X posts are text-heavy, we can do relevance filtering
   purely on the post text without any transcript/video analysis.
   The classifier's reasoning trace can be compared against the
   post text to determine relevance. This is faster and cheaper
   than video-based verification.

5. RATE LIMITS AND AUTH:
   X's official API requires authentication and has strict rate
   limits. Browser Use skill-based search bypasses this by
   scraping the search page directly. However, X is aggressive
   about rate-limiting unauthenticated access. The skill handles
   this, but searches may occasionally fail or return fewer results.

6. POST ID FORMAT:
   X post IDs are numeric strings (e.g., "1762345678901234567").
   They're extracted from the post URL: x.com/{user}/status/{id}

NUANCE ON "VIDEO" RESULTS:
   Not all X posts have video. For Findr, we return ALL relevant
   posts — text, image, and video alike. The frontend should render
   each appropriately:
   - Video posts: show the embedded player
   - Image posts: show the image card
   - Text posts: show the tweet card
   The has_video/has_image fields in the skill output help the
   frontend decide which rendering to use.
---------------------------------------------------------------------------
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from src.agents.browser_skills import search_twitter
from src.config import BROWSER_USE_API_KEY, TWITTER_SEARCH_RESULTS
from src.models.schemas import Platform, VideoSearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL / ID extraction helpers
# ---------------------------------------------------------------------------

def _extract_post_id(url: str) -> Optional[str]:
    """
    Extract the numeric post ID from an X/Twitter URL.

    Supported formats:
      - https://x.com/user/status/1762345678901234567
      - https://twitter.com/user/status/1762345678901234567
      - https://x.com/i/status/1762345678901234567

    NUANCE: X rebranded from Twitter but both domains still work.
    The skill might return either format depending on redirects.

    NUANCE: Post IDs are int64 (up to 19 digits). They are
    chronologically ordered — higher IDs are newer posts. This is
    useful if we ever want to sort by recency.
    """
    if not url:
        return None

    match = re.search(r"(?:x\.com|twitter\.com)/\S+/status/(\d+)", url)
    if match:
        return match.group(1)

    # NUANCE: Sometimes the skill returns bare IDs or partial URLs
    # Try extracting any long numeric sequence as a fallback
    match = re.search(r"\b(\d{15,20})\b", url)
    if match:
        return match.group(1)

    logger.debug(f"[Twitter] Could not extract post ID from: {url[:80]}")
    return None


def _parse_engagement_count(count_str: str) -> int:
    """
    Parse X engagement count strings like "1.2K", "45", "2.3M" into integers.

    NUANCE: Same abbreviated format as TikTok. X uses K/M/B suffixes.
    Some counts might show as "—" or empty for restricted posts.
    """
    if not count_str:
        return 0

    count_str = str(count_str).strip().upper().replace(",", "")

    # Handle special values
    if count_str in ("", "—", "-", "N/A", "NONE"):
        return 0

    try:
        if count_str.endswith("B"):
            return int(float(count_str[:-1]) * 1_000_000_000)
        elif count_str.endswith("M"):
            return int(float(count_str[:-1]) * 1_000_000)
        elif count_str.endswith("K"):
            return int(float(count_str[:-1]) * 1_000)
        else:
            return int(float(count_str))
    except (ValueError, IndexError):
        return 0


# ---------------------------------------------------------------------------
# Normalization: raw skill output → VideoSearchResult
# ---------------------------------------------------------------------------

def _normalize_twitter_result(raw: Dict[str, Any]) -> Optional[VideoSearchResult]:
    """
    Convert a single raw skill output dict into a VideoSearchResult.

    NUANCE: We reuse VideoSearchResult even though X posts aren't
    "videos" in the traditional sense. The schema is flexible enough:
    - video_id → post ID
    - url → post URL
    - title → post text (truncated)
    - channel → @handle
    - view_count → likes (as a proxy for engagement)
    - description → full post text
    - has_transcript → always False

    NUANCE: The embed_url for X posts will be set by the moment finder
    as https://x.com/i/status/{post_id}. The frontend's react-tweet
    component uses this to render the full post card.

    NUANCE: We include both has_video and has_image information in the
    description so the frontend can make rendering decisions. The
    VideoSearchResult schema doesn't have dedicated fields for these,
    so we encode them in the description.
    """
    url = raw.get("url") or raw.get("post_url") or raw.get("link") or ""
    post_id = _extract_post_id(url)

    if not url and not post_id:
        logger.debug(f"[Twitter] Skipping result with no URL: {raw}")
        return None

    # Extract text content
    text = (
        raw.get("text")
        or raw.get("content")
        or raw.get("body")
        or raw.get("tweet")
        or ""
    )

    # Extract author info
    author = (
        raw.get("author")
        or raw.get("handle")
        or raw.get("username")
        or raw.get("user")
        or "Unknown"
    )
    display_name = (
        raw.get("display_name")
        or raw.get("name")
        or raw.get("displayName")
        or author
    )

    # Extract engagement metrics
    likes = raw.get("likes") or raw.get("like_count") or "0"
    retweets = raw.get("retweets") or raw.get("repost_count") or raw.get("reposts") or "0"

    # Extract media flags
    has_video = raw.get("has_video", False)
    has_image = raw.get("has_image", False)

    # Build description
    # NUANCE: We encode media type and engagement in the description
    # so the pipeline and frontend have this context without needing
    # schema changes. Format: "text | [VIDEO] | Likes: X, RTs: Y"
    description_parts = [text[:250]]
    media_flags = []
    if has_video:
        media_flags.append("VIDEO")
    if has_image:
        media_flags.append("IMAGE")
    if media_flags:
        description_parts.append(f"[{'+'.join(media_flags)}]")
    description_parts.append(
        f"Likes: {likes}, RTs: {retweets}"
    )

    # Reconstruct URL if we only have the post ID
    if not url and post_id:
        url = f"https://x.com/i/status/{post_id}"

    return VideoSearchResult(
        video_id=post_id or url,
        url=url,
        title=text[:100] if text else f"Post by {display_name}",
        duration=0,  # X posts don't have duration
        thumbnail="",
        channel=f"@{author.lstrip('@')}" if author else "Unknown",
        view_count=_parse_engagement_count(str(likes)),
        description=" | ".join(description_parts)[:300],
        platform=Platform.X,
        has_transcript=False,  # Critical: no transcripts for X posts
    )


# ---------------------------------------------------------------------------
# Main search service
# ---------------------------------------------------------------------------

class TwitterSearchService:
    """X/Twitter search via Browser Use Skills."""

    async def search_videos(
        self,
        query: str,
        max_results: int = TWITTER_SEARCH_RESULTS,
    ) -> List[VideoSearchResult]:
        """
        Search X/Twitter for posts matching the query.

        Args:
            query: Search query (from classifier's proposed_video_query).
            max_results: Maximum number of results to return.

        Returns:
            List of VideoSearchResult objects with platform=X.
            Returns empty list if Browser Use is not configured or search fails.

        NUANCE: X search quality depends heavily on query phrasing.
        The classifier's proposed_video_query is optimized for YouTube.
        For X, queries with specific names, hashtags, or quoted phrases
        work best. The classifier should be aware of this when generating
        sub-queries for X platform.

        NUANCE: We request the "Latest" tab in the skill to get recent
        posts. For time-sensitive queries ("what happened today"), this
        is crucial. For evergreen queries ("best programming tips"),
        the "Top" tab would be better but we default to Latest for now.
        """
        if not BROWSER_USE_API_KEY:
            logger.warning(
                "[Twitter] BROWSER_USE_API_KEY not configured — "
                "skipping X/Twitter search"
            )
            return []

        t0 = time.perf_counter()
        logger.info(
            f"[Twitter] Searching: '{query}' (max {max_results})"
        )

        # Over-request to compensate for normalization failures
        raw_results = await search_twitter(
            query=query,
            max_results=max_results * 2,
        )

        if not raw_results:
            elapsed = time.perf_counter() - t0
            logger.warning(
                f"[Twitter] Search returned no results after {elapsed:.2f}s"
            )
            return []

        # Normalize and filter
        results: List[VideoSearchResult] = []
        skipped = 0
        for raw in raw_results:
            post = _normalize_twitter_result(raw)
            if post:
                results.append(post)
                if len(results) >= max_results:
                    break
            else:
                skipped += 1

        elapsed = time.perf_counter() - t0
        logger.info(
            f"[Twitter] Search complete in {elapsed:.2f}s | "
            f"raw={len(raw_results)}, accepted={len(results)}, "
            f"skipped={skipped} | query={query[:60]!r}"
        )

        for i, p in enumerate(results):
            logger.info(
                f"[Twitter]   [{i}] @{p.channel} | "
                f"{p.title[:50]} | "
                f"{p.view_count:,} likes"
            )

        return results

    async def search_with_relevance_filter(
        self,
        query: str,
        reasoning: str,
        max_results: int = TWITTER_SEARCH_RESULTS,
    ) -> List[VideoSearchResult]:
        """
        Search X/Twitter and filter results by text relevance.

        Since X posts are text-heavy, we can do a simple relevance
        check on the post text against the query and reasoning trace
        without needing any LLM call. This is a fast pre-filter.

        Args:
            query: Search query.
            reasoning: Classifier's reasoning trace for this sub-query.
            max_results: Max results after filtering.

        NUANCE: This is a heuristic filter, not semantic similarity.
        It checks if key terms from the query/reasoning appear in the
        post text. For proper semantic filtering, we'd need embeddings
        (overkill for short text posts).

        NUANCE: We lowercase everything for case-insensitive matching.
        We split the query into terms and check if at least some appear
        in the post. This is intentionally lenient — false positives
        are better than false negatives for a discovery engine.
        """
        all_results = await self.search_videos(query, max_results=max_results * 2)

        if not all_results:
            return []

        # Build keyword set from query + reasoning
        keywords = set()
        for text in [query, reasoning]:
            for word in text.lower().split():
                # Skip common stop words
                if len(word) > 3 and word not in {
                    "the", "and", "for", "that", "this", "with",
                    "from", "what", "about", "show", "find",
                }:
                    keywords.add(word)

        logger.info(
            f"[Twitter] Relevance filtering with {len(keywords)} keywords: "
            f"{list(keywords)[:10]}..."
        )

        # Score each result by keyword overlap
        scored = []
        for result in all_results:
            result_text = f"{result.title} {result.description}".lower()
            matches = sum(1 for kw in keywords if kw in result_text)
            score = matches / max(len(keywords), 1)
            scored.append((score, result))

        # Sort by relevance score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Take top results that have any keyword match
        filtered = []
        for score, result in scored:
            if score > 0:
                filtered.append(result)
                logger.debug(
                    f"[Twitter] Relevance score {score:.2f}: "
                    f"{result.title[:50]}"
                )
                if len(filtered) >= max_results:
                    break

        logger.info(
            f"[Twitter] Relevance filter: {len(all_results)} → "
            f"{len(filtered)} results"
        )

        return filtered
