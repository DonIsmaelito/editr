"""
Findr YouTube Search

Wraps Clippa's yt-dlp search and transcript services.
Adds Findr-specific filtering (duration cap, transcript availability).
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from src.config import MAX_SEARCH_RESULTS, MAX_VIDEO_DURATION
from src.models.schemas import Platform, VideoSearchResult

logger = logging.getLogger(__name__)


def _require_yt_dlp():
    try:
        import yt_dlp
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "yt-dlp not installed. Run: pip install yt-dlp"
        ) from exc
    return yt_dlp


class YouTubeSearchService:
    """YouTube search via yt-dlp + transcript fetching via youtube-transcript-api."""

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    async def search_videos(
        self,
        query: str,
        max_results: int = MAX_SEARCH_RESULTS,
        max_duration: int = MAX_VIDEO_DURATION,
    ) -> List[VideoSearchResult]:
        """
        Search YouTube and return metadata for videos under the duration cap.
        Runs yt-dlp in a thread to avoid blocking the event loop.
        """
        yt_dlp = _require_yt_dlp()

        search_query = f"ytsearch{max_results * 2}:{query}"  # Over-fetch to compensate for filtering
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "ignoreerrors": True,
        }

        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(search_query, download=False)

        logger.info(f"[YouTube] Searching: '{query}' (max {max_results}, fetch {max_results * 2})")
        t0 = time.perf_counter()
        info = await asyncio.to_thread(_extract)
        logger.info(f"[YouTube] yt-dlp search took {time.perf_counter() - t0:.2f}s")

        if not info:
            logger.warning("[YouTube] yt-dlp returned no info")
            return []

        results: List[VideoSearchResult] = []
        for entry in (info.get("entries") or []):
            if not entry:
                continue

            duration = entry.get("duration", 0) or 0

            # Filter out long videos
            if duration > max_duration:
                logger.debug(f"[YouTube] Skipping {entry.get('id')} — {duration}s > {max_duration}s cap")
                continue

            video_id = entry.get("id", "")
            results.append(VideoSearchResult(
                video_id=video_id,
                url=entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
                title=entry.get("title", "Untitled"),
                duration=duration,
                thumbnail=entry.get("thumbnail") or (entry.get("thumbnails") or [{}])[-1].get("url", ""),
                channel=entry.get("channel") or entry.get("uploader", "Unknown"),
                view_count=entry.get("view_count", 0),
                description=(entry.get("description") or "")[:300],
                platform=Platform.YOUTUBE,
            ))

            if len(results) >= max_results:
                break

        logger.info(f"[YouTube] Found {len(results)} results (under {max_duration}s)")
        return results

    # ------------------------------------------------------------------
    # Transcript (tiered fallback — reuses Clippa's services)
    # ------------------------------------------------------------------
    async def get_transcript(
        self,
        video_id: str,
        video_url: str = "",
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch transcript with tiered fallback:
        1. youtube-transcript-api (fast, ~200ms)
        2. yt-dlp subtitle extraction (slower but wider coverage)

        Returns list of {text, start, end, duration} or None.
        """
        # Tier 1: youtube-transcript-api
        try:
            from src.services.youtube_transcript import YouTubeTranscriptService
            t0 = time.perf_counter()
            transcript = await asyncio.to_thread(
                YouTubeTranscriptService.get_transcript, video_id
            )
            elapsed = time.perf_counter() - t0
            if transcript:
                total_dur = transcript[-1].get("end", 0) if transcript else 0
                logger.info(
                    f"[YouTube] Transcript via API in {elapsed:.2f}s: "
                    f"{len(transcript)} segments, {total_dur:.0f}s duration"
                )
                return transcript
            logger.info(f"[YouTube] youtube-transcript-api returned empty in {elapsed:.2f}s")
        except Exception as e:
            logger.warning(f"[YouTube] youtube-transcript-api failed for {video_id}: {e}")

        # Tier 2: yt-dlp subtitles
        if video_url:
            try:
                t0 = time.perf_counter()
                transcript = await self._get_transcript_ytdlp(video_url)
                elapsed = time.perf_counter() - t0
                if transcript:
                    logger.info(
                        f"[YouTube] Transcript via yt-dlp in {elapsed:.2f}s: "
                        f"{len(transcript)} segments"
                    )
                    return transcript
                logger.info(f"[YouTube] yt-dlp subtitles returned empty in {elapsed:.2f}s")
            except Exception as e:
                logger.warning(f"[YouTube] yt-dlp subtitles failed for {video_id}: {e}")

        logger.warning(f"[YouTube] No transcript available for {video_id}")
        return None

    async def _get_transcript_ytdlp(self, video_url: str) -> Optional[List[Dict[str, Any]]]:
        """Extract subtitles via yt-dlp json3 format."""
        yt_dlp = _require_yt_dlp()

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "json3",
        }

        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(video_url, download=False)

        info = await asyncio.to_thread(_extract)
        if not info:
            return None

        subs = info.get("subtitles") or {}
        auto_subs = info.get("automatic_captions") or {}
        sub_data = subs.get("en") or auto_subs.get("en")
        if not sub_data:
            return None

        # Find json3 format URL
        json3_entry = next((f for f in sub_data if f.get("ext") == "json3"), None)
        if not json3_entry or not json3_entry.get("url"):
            return None

        import requests
        resp = await asyncio.to_thread(requests.get, json3_entry["url"], timeout=10)
        if resp.status_code != 200:
            return None

        sub_json = resp.json()
        segments = []
        for ev in (sub_json.get("events") or []):
            start_ms = ev.get("tStartMs", 0)
            duration_ms = ev.get("dDurationMs", 0)
            text = "".join(s.get("utf8", "") for s in (ev.get("segs") or [])).strip()
            if not text or text == "\n":
                continue
            start = start_ms / 1000.0
            duration = duration_ms / 1000.0
            segments.append({
                "text": text,
                "start": round(start, 3),
                "duration": round(duration, 3),
                "end": round(start + duration, 3),
            })

        return segments if segments else None

    # ------------------------------------------------------------------
    # Transcript-aware search: search + verify transcript exists
    # ------------------------------------------------------------------
    async def search_with_transcript(
        self,
        query: str,
        max_results: int = 1,
        exclude_video_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search YouTube, then verify each result has a transcript.
        Returns list of {video: VideoSearchResult, transcript: [...]} dicts.
        Only returns videos that have available transcripts.

        Args:
            exclude_video_ids: Video IDs to skip (already used by prior sub-queries).
        """
        t0 = time.perf_counter()
        excluded = set(exclude_video_ids or [])

        # Over-search to have fallbacks if some lack transcripts
        candidates = await self.search_videos(query, max_results=max_results * 3)
        logger.info(
            f"[YouTube] search_with_transcript: {len(candidates)} candidates, "
            f"verifying transcripts..."
        )

        results = []
        skipped = 0
        for video in candidates:
            if video.video_id in excluded:
                logger.debug(
                    f"[YouTube] Skipping duplicate: {video.video_id} "
                    f"({video.title[:40]})"
                )
                skipped += 1
                continue
            transcript = await self.get_transcript(video.video_id, video.url)
            if transcript:
                video.has_transcript = True
                results.append({"video": video, "transcript": transcript})
                logger.info(
                    f"[YouTube] Accepted: {video.video_id} "
                    f"({video.title[:40]}, {video.duration:.0f}s)"
                )
                if len(results) >= max_results:
                    break
            else:
                skipped += 1
                logger.debug(
                    f"[YouTube] Rejected: {video.video_id} — no transcript "
                    f"({video.title[:40]})"
                )

        elapsed = time.perf_counter() - t0
        logger.info(
            f"[YouTube] search_with_transcript complete in {elapsed:.2f}s | "
            f"accepted={len(results)}, rejected={skipped}, "
            f"candidates={len(candidates)}"
        )
        return results
