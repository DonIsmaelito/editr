"""
YouTube Transcript Service

Fast transcript fetching using youtube-transcript-api.
Duplicated from Clippa (src/backend/services/youtube_service.py) so
Findr is fully self-contained with zero external project imports.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from youtube_transcript_api import YouTubeTranscriptApi

logger = logging.getLogger(__name__)


class YouTubeTranscriptService:
    """
    Service to fetch transcripts directly from YouTube.
    Bypasses audio download + ASR when a transcript is available.
    """

    @staticmethod
    def extract_video_id(video_link_or_id: str) -> Optional[str]:
        """
        Extract video ID from various YouTube URL formats.

        Supported formats:
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://www.youtube.com/embed/VIDEO_ID
        - VIDEO_ID (raw 11-char)
        """
        if not video_link_or_id:
            return None

        # Already a video ID (11 characters)
        if re.match(r'^[a-zA-Z0-9_-]{11}$', video_link_or_id):
            return video_link_or_id

        # youtube.com/watch?v= | youtube.com/embed/ | youtu.be/
        match = re.search(
            r'(?:youtube\.com/watch\?v=|youtube\.com/embed/|youtu\.be/)'
            r'([a-zA-Z0-9_-]{11})',
            video_link_or_id,
        )
        if match:
            return match.group(1)

        # Fallback: try splitting
        if "v=" in video_link_or_id:
            return video_link_or_id.split("v=")[-1].split("&")[0][:11]
        if "youtu.be/" in video_link_or_id:
            return video_link_or_id.split("/")[-1].split("?")[0][:11]

        return None

    @staticmethod
    def is_valid_youtube_url(url: str) -> bool:
        """Check if a URL is a valid YouTube URL."""
        patterns = [
            r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=',
            r'(?:https?://)?(?:www\.)?youtu\.be/',
            r'(?:https?://)?(?:www\.)?youtube\.com/embed/',
        ]
        return any(re.match(pattern, url) for pattern in patterns)

    @staticmethod
    def get_transcript(
        video_link_or_id: str,
        languages: List[str] = [
            "en", "en-US", "en-CA", "en-GB", "en-AU", "en-NZ", "en-IE", "en-IN",
        ],
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch transcript for a YouTube video.

        Args:
            video_link_or_id: YouTube video URL or 11-char ID.
            languages: Language codes to try. Includes regional English
                       variants (en-CA, en-GB, etc.) so we don't miss
                       manually-created transcripts tagged with a locale.

        Returns:
            List of segment dicts {text, start, end, duration}
            or None if unavailable.
        """
        try:
            video_id = YouTubeTranscriptService.extract_video_id(video_link_or_id)
            if not video_id:
                logger.debug(f"[YouTube] Cannot extract video ID from: {video_link_or_id}")
                return None

            logger.info(f"[YouTube] Fetching transcript for {video_id}")

            ytt_api = YouTubeTranscriptApi()
            transcript = ytt_api.fetch(video_id, languages=languages).to_raw_data()

            # Add 'end' field for pipeline compatibility
            for tr in transcript:
                tr["end"] = round(tr["start"] + tr["duration"], 3)

            total_duration = transcript[-1]["end"] if transcript else 0
            logger.info(f"[YouTube] Fetched {len(transcript)} segments ({total_duration:.1f}s)")
            return transcript

        except Exception as e:
            logger.warning(f"[YouTube] Transcript fetch failed: {e}")
            return None

    @staticmethod
    def get_video_metadata(video_id: str) -> Dict[str, Any]:
        """Get basic video metadata from video ID."""
        return {
            "video_id": video_id,
            "source": "youtube",
            "title": f"YouTube Video ({video_id})",
        }
