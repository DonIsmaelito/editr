"""
Editr Mock Profile Scraper

Returns fake but realistic TikTok profile data for local testing.
Lets you test the full pipeline (scoring → sandbox → gemini → render → upload)
without needing Browser Use or a real TikTok account.

Toggle on/off via USE_MOCK_SCRAPER=true in .env (default: true).
"""

import logging
import random
import time

from src_2.scrape.profile_models import ProfileData, VideoMetric

logger = logging.getLogger(__name__)


def generate_mock_tiktok_profile(username: str) -> ProfileData:
    """
    Generate a fake TikTok profile with 8 videos for testing.
    Some videos are "underperformers" (low views, simple edits) to
    trigger the scoring/selection logic.
    """
    logger.info(f"[MockScrape] Generating fake profile for @{username}")
    t0 = time.perf_counter()

    # Fake profile stats
    follower_count = random.randint(5000, 500000)
    total_likes = follower_count * random.randint(3, 15)

    # Generate 8 fake videos with varied performance
    # First 3 are "hits" (high views), rest are "underperformers" (low views)
    videos = []
    base_time = int(time.time()) - 86400 * 30  # 30 days ago

    video_templates = [
        # (desc, duration, views_multiplier, likes_ratio, shares_ratio)
        ("Day in my life vlog #fyp", 45, 5.0, 0.08, 0.02),        # hit
        ("POV: you finally get it #relatable", 30, 4.0, 0.10, 0.03), # hit
        ("This hack changed everything", 60, 3.0, 0.07, 0.015),    # hit
        ("Trying this trend #viral", 25, 0.3, 0.04, 0.005),        # underperformer - good topic, bad edit
        ("Storytime: what happened next", 90, 0.2, 0.03, 0.008),   # underperformer - raw footage
        ("Behind the scenes of my shoot", 55, 0.4, 0.05, 0.01),    # underperformer - no edits
        ("Replying to comments pt 3", 40, 0.15, 0.02, 0.003),      # underperformer - low effort
        ("New recipe I tried today", 70, 0.5, 0.06, 0.012),        # underperformer - decent content bad packaging
    ]

    median_views = follower_count * 2  # typical median for the channel

    for i, (desc, duration, views_mult, likes_ratio, shares_ratio) in enumerate(video_templates):
        views = int(median_views * views_mult * random.uniform(0.8, 1.2))
        likes = int(views * likes_ratio * random.uniform(0.7, 1.3))
        comments = int(likes * 0.1 * random.uniform(0.5, 2.0))
        shares = int(views * shares_ratio * random.uniform(0.5, 1.5))

        videos.append(VideoMetric(
            video_id=f"mock_{username}_{i}_{random.randint(1000, 9999)}",
            desc=desc,
            play_count=views,
            digg_count=likes,
            comment_count=comments,
            share_count=shares,
            collect_count=int(shares * 0.3),
            duration=duration,
            create_time=base_time + (i * 86400 * 3),  # every 3 days
            play_addr="",   # no real CDN URL — sandbox download will use yt-dlp fallback
            cover_url="",
            music_title=random.choice(["original sound", "trending audio", "Cupid - FIFTY FIFTY", ""]),
            challenges=random.sample(["fyp", "viral", "trending", "relatable", "storytime", "hack"], k=2),
        ))

    profile = ProfileData(
        username=username,
        follower_count=follower_count,
        following_count=random.randint(100, 2000),
        total_likes=total_likes,
        bio=f"Just a creator doing things | {random.randint(18, 35)}yo",
        verified=random.random() < 0.1,
        avatar_url="",
        videos=videos,
    )

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[MockScrape] Generated profile for @{username} in {elapsed:.3f}s | "
        f"{len(videos)} videos | {follower_count} followers | "
        f"median_views={median_views}"
    )

    return profile
