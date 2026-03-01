"""
Findr Transcript Fetcher

Thin wrapper around Clippa's YouTubeTranscriptService for direct use.
Also exposes the Clippa transcription_service for ASR fallback.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def fetch_youtube_transcript(
    video_id: str,
    languages: List[str] = ["en"],
) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch YouTube transcript via youtube-transcript-api.
    Returns list of {text, start, end, duration} or None.
    """
    try:
        from src.services.youtube_transcript import YouTubeTranscriptService
        transcript = await asyncio.to_thread(
            YouTubeTranscriptService.get_transcript,
            video_id,
            languages,
        )
        if transcript:
            logger.info(f"[Transcript] Fetched {len(transcript)} segments for {video_id}")
        return transcript
    except Exception as e:
        logger.warning(f"[Transcript] Failed for {video_id}: {e}")
        return None


def extract_video_id(url_or_id: str) -> Optional[str]:
    """Extract YouTube video ID from URL or raw ID."""
    try:
        from src.services.youtube_transcript import YouTubeTranscriptService
        return YouTubeTranscriptService.extract_video_id(url_or_id)
    except Exception:
        return None
