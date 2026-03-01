"""
Findr API Response Transformations

Helpers for shaping pipeline output into SSE event payloads.
Used by server.py to build each moment event and the final done event.
"""

from typing import Dict, List

from src.models.schemas import FoundMoment


def format_timestamp(seconds: float) -> str:
    """
    Convert seconds to a human-readable timestamp string.

    Examples:
        125.5 → "2:05"
        3661  → "1:01:01"
        0     → "0:00"
    """
    total = int(seconds)
    if total < 0:
        return "0:00"

    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def group_moments_by_video(moments: List[FoundMoment]) -> List[Dict]:
    """
    Group a flat list of FoundMoments by video_id.

    Returns a list of dicts shaped for the frontend:
        [
            {
                "videoName": "How to use React Hooks",
                "videoId": "abc123",
                "embedUrl": "https://youtube.com/embed/abc123?start=60&end=120",
                "moments": [
                    { "timestamp": "1:00", "description": "Introduction to useState" },
                    { "timestamp": "3:45", "description": "useEffect explained" },
                ]
            }
        ]

    Ordering: videos appear in sub_query_order, moments within a video
    appear in start-time order.
    """
    # Group by video_id preserving insertion order
    by_video: Dict[str, Dict] = {}

    for m in sorted(moments, key=lambda x: (x.sub_query_order, x.start)):
        vid = m.video_id
        if vid not in by_video:
            by_video[vid] = {
                "videoName": m.video_title or m.sub_query_title or m.title,
                "videoId": m.video_id,
                "embedUrl": m.embed_url,
                "moments": [],
            }
        by_video[vid]["moments"].append({
            "timestamp": format_timestamp(m.start),
            "description": m.description or m.title,
        })

    return list(by_video.values())
