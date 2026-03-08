"""
Editr Video Scorer

Computes relative performance metrics and ranks videos by fixability.
Videos with high content resonance but poor editing are prime candidates.
"""

import logging
import statistics
import time
from typing import List

from src_2.config import MIN_ENGAGEMENT_RATE
from src_2.scrape.profile_models import VideoMetric
from src_2.scorer.scorer_models import ScoredVideo

logger = logging.getLogger(__name__)


def score_videos(
    videos: List[VideoMetric],
    max_duration: int = 150,
    min_engagement: float = MIN_ENGAGEMENT_RATE,
    max_selected: int = 3,
) -> List[ScoredVideo]:
    """
    Score and rank videos by fixability.

    1. Filter: duration <= max_duration
    2. Compute engagement_rate, views_ratio, content_resonance
    3. Score: fixability = (1 - views_ratio) * 0.5 + content_resonance * 0.3 + 0.2
       (edit_level weight is deferred to post-download PySceneDetect)
    4. Guard: skip if engagement_rate < min_engagement
    5. Sort descending, pick top N
    """
    t0 = time.perf_counter()

    # Filter by duration
    eligible = [v for v in videos if v.duration <= max_duration and v.duration > 0]

    if not eligible:
        logger.warning("[Scorer] No eligible videos after duration filter")
        return []

    # Compute channel medians
    all_views = [v.play_count for v in eligible if v.play_count > 0]
    all_likes = [v.digg_count for v in eligible if v.digg_count > 0]

    median_views = statistics.median(all_views) if all_views else 1
    median_likes = statistics.median(all_likes) if all_likes else 1

    # Prevent division by zero
    median_views = max(median_views, 1)
    median_likes = max(median_likes, 1)

    scored: List[ScoredVideo] = []

    for v in eligible:
        views = max(v.play_count, 1)
        likes = v.digg_count
        comments = v.comment_count
        shares = v.share_count
        saves = v.collect_count

        engagement_rate = (likes + comments + shares) / views
        views_ratio = min(views / median_views, 3.0) / 3.0  # normalize to 0-1
        content_resonance = (shares + saves) / views if views > 0 else 0

        # Fixability score (pre-scenedetect, so edit_level weight = 0.2 default)
        fixability = (
            (1 - views_ratio) * 0.5
            + min(content_resonance * 10, 1.0) * 0.3  # scale up resonance
            + 0.2  # default edit level bonus (refined after scenedetect)
        )

        sv = ScoredVideo(
            video_id=v.video_id,
            desc=v.desc,
            play_count=v.play_count,
            digg_count=v.digg_count,
            comment_count=v.comment_count,
            share_count=v.share_count,
            collect_count=v.collect_count,
            duration=v.duration,
            create_time=v.create_time,
            play_addr=v.play_addr,
            cover_url=v.cover_url,
            music_title=v.music_title,
            challenges=v.challenges,
            engagement_rate=engagement_rate,
            views_ratio=views_ratio,
            content_resonance=content_resonance,
            fixability_score=fixability,
        )

        scored.append(sv)

    # Guard: skip very low engagement (bad topic, not bad editing)
    scored = [s for s in scored if s.engagement_rate >= min_engagement]

    # Sort by fixability descending
    scored.sort(key=lambda s: s.fixability_score, reverse=True)

    # Mark top N as selected
    for i, s in enumerate(scored):
        s.selected = i < max_selected

    elapsed = time.perf_counter() - t0
    selected_count = sum(1 for s in scored if s.selected)
    logger.info(
        f"[Scorer] Scored {len(scored)} videos in {elapsed:.3f}s | "
        f"selected {selected_count} | "
        f"median_views={median_views:.0f} median_likes={median_likes:.0f}"
    )

    return scored
