"""
Editr TikTok Profile Scraper

Uses Browser Use Skills to navigate to a TikTok profile page and extract
video metrics via __NEXT_DATA__ JSON interception.

Reuses the skill ensure/execute pattern from src/agents/browser_skills.py.
"""

import asyncio
import json
import logging
import time
from typing import Optional

from src_2.config import (
    BROWSER_USE_API_KEY,
    BROWSER_USE_PROFILE_ID,
    TIKTOK_PROFILE_SKILL_ID,
)
from src_2.scrape.profile_models import ProfileData, VideoMetric

logger = logging.getLogger(__name__)

_skill_id: Optional[str] = TIKTOK_PROFILE_SKILL_ID or None

# ---------------------------------------------------------------------------
# Skill definition for TikTok profile scraping
# ---------------------------------------------------------------------------
_TIKTOK_PROFILE_SKILL_DEFINITION = {
    "name": "TikTok Profile Scraper",
    "description": (
        "Navigate to a TikTok user's profile page, scroll to load videos, "
        "and extract the __NEXT_DATA__ JSON containing video metrics "
        "(views, likes, comments, shares, duration, CDN URLs). "
        "Return the full JSON data."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "username": {
                "type": "string",
                "description": "TikTok username without @",
            },
        },
        "required": ["username"],
    },
}


async def _ensure_skill() -> str:
    """Ensure the TikTok profile scraping skill exists. Create if needed."""
    global _skill_id

    if _skill_id:
        return _skill_id

    try:
        from browser_use_sdk import BrowserUseSDK
    except ImportError:
        raise RuntimeError("browser-use-sdk not installed. Run: pip install browser-use-sdk")

    if not BROWSER_USE_API_KEY:
        raise RuntimeError("BROWSER_USE_API_KEY not configured")

    sdk = BrowserUseSDK(api_key=BROWSER_USE_API_KEY)

    logger.info("[TikTokScrape] Creating profile scraping skill ($2)...")
    t0 = time.perf_counter()

    result = await asyncio.to_thread(
        sdk.skills.create,
        **_TIKTOK_PROFILE_SKILL_DEFINITION,
    )

    # Browser Use returns 202 immediately, skill takes ~30s to create
    skill_id = str(result.id)
    logger.info(
        f"[TikTokScrape] Skill creation initiated: {skill_id} | "
        f"Waiting for readiness..."
    )

    # Poll for skill readiness
    for _ in range(12):
        await asyncio.sleep(5)
        try:
            skill = await asyncio.to_thread(sdk.skills.get, skill_id)
            if hasattr(skill, "status") and skill.status == "ready":
                break
        except Exception:
            pass

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[TikTokScrape] Skill ready in {elapsed:.1f}s | "
        f"ID: {skill_id} — save as TIKTOK_PROFILE_SKILL_ID in .env"
    )

    _skill_id = skill_id
    return skill_id


async def _execute_skill(username: str) -> dict:
    """Execute the profile scraping skill and return raw JSON."""
    try:
        from browser_use_sdk import BrowserUseSDK
    except ImportError:
        raise RuntimeError("browser-use-sdk not installed")

    sdk = BrowserUseSDK(api_key=BROWSER_USE_API_KEY)
    skill_id = await _ensure_skill()

    logger.info(f"[TikTokScrape] Executing skill for @{username}...")
    t0 = time.perf_counter()

    params = {"username": username}
    if BROWSER_USE_PROFILE_ID:
        params["profile_id"] = BROWSER_USE_PROFILE_ID

    result = await asyncio.to_thread(
        sdk.skills.execute,
        skill_id,
        input={"username": username},
    )

    elapsed = time.perf_counter() - t0
    logger.info(f"[TikTokScrape] Skill executed in {elapsed:.1f}s")

    if hasattr(result, "output"):
        return json.loads(result.output) if isinstance(result.output, str) else result.output

    return result if isinstance(result, dict) else {}


def _parse_profile_data(raw: dict) -> ProfileData:
    """Parse raw __NEXT_DATA__ structure into ProfileData."""
    user_info = raw.get("userInfo", raw.get("user", {}))
    user_stats = raw.get("stats", user_info.get("stats", {}))

    videos_raw = raw.get("items", raw.get("videos", []))

    videos = []
    for item in videos_raw:
        stats = item.get("stats", {})
        video_data = item.get("video", {})
        music_data = item.get("music", {})
        challenges = [c.get("title", "") for c in item.get("challenges", [])]

        videos.append(VideoMetric(
            video_id=str(item.get("id", "")),
            desc=item.get("desc", ""),
            play_count=stats.get("playCount", 0),
            digg_count=stats.get("diggCount", 0),
            comment_count=stats.get("commentCount", 0),
            share_count=stats.get("shareCount", 0),
            collect_count=stats.get("collectCount", 0),
            duration=item.get("duration", video_data.get("duration", 0)),
            create_time=item.get("createTime", 0),
            play_addr=video_data.get("playAddr", video_data.get("downloadAddr", "")),
            cover_url=video_data.get("cover", ""),
            music_title=music_data.get("title", ""),
            challenges=challenges,
        ))

    return ProfileData(
        username=user_info.get("uniqueId", user_info.get("username", "")),
        follower_count=user_stats.get("followerCount", 0),
        following_count=user_stats.get("followingCount", 0),
        total_likes=user_stats.get("heartCount", user_stats.get("heart", 0)),
        bio=user_info.get("signature", ""),
        verified=user_info.get("verified", False),
        avatar_url=user_info.get("avatarLarger", user_info.get("avatarMedium", "")),
        videos=videos,
    )


async def scrape_tiktok_profile(username: str) -> ProfileData:
    """
    Scrape a TikTok profile and return structured ProfileData.

    Uses Browser Use skill for profile + network interception to get
    __NEXT_DATA__ JSON with video metrics and CDN URLs.
    """
    username = username.lstrip("@").strip()

    if not BROWSER_USE_API_KEY:
        raise RuntimeError(
            "BROWSER_USE_API_KEY not configured — cannot scrape TikTok profiles"
        )

    raw = await _execute_skill(username)
    profile = _parse_profile_data(raw)

    if not profile.username:
        profile.username = username

    logger.info(
        f"[TikTokScrape] Parsed profile: @{profile.username} | "
        f"{len(profile.videos)} videos | "
        f"{profile.follower_count} followers"
    )

    return profile
