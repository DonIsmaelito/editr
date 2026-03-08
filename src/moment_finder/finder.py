"""
Findr Moment Finder

Takes filtered transcript segments (1-2 macro-segments from vector search)
and uses an LLM to find the exact timestamp range that answers the user's
sub-query.

This is the final step: the vector search already narrowed us to the right
~5-minute window. Now the LLM reads the actual transcript text and pinpoints
the precise start/end for the embed URL.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from src.config import MOMENT_FINDER_MODEL, OPENAI_API_KEY
from src.models.schemas import FoundMoment, Platform

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

MOMENT_FINDER_PROMPT = """\
You are a precise video moment finder. You receive a transcript segment
from a video and a specific query. Your job is to find the BEST STARTING
POINT within this transcript where the viewer should begin watching to
get the content that matches the query.

The transcript text contains inline timestamps like [M:SS] at the start of
each sentence. Use these to determine the precise starting point.

RULES:
1. Return exactly 1 moment — the single best starting point in the video.
2. The "start" timestamp is where the viewer should begin watching.
   The video will play through from there — do NOT worry about an end time.
3. The [M:SS] timestamps in the transcript are GLOBAL (relative to the full video).
   Use them to set your start — do NOT default to 0:00 unless the very beginning
   is truly the most relevant starting point.
4. If nothing in the transcript matches the query, return an empty array.
5. Write a short title (3-8 words) for the moment.
6. Read the FULL transcript before choosing — the best starting point is
   often NOT at the beginning.
7. Pick the point where the most relevant explanation/content BEGINS.
8. Avoid generic intro/outro moments (greetings, sponsor reads, "welcome back")
   unless the query is explicitly about those sections.
9. Prefer the first timestamp where concrete query-related nouns/actions appear.

RESPONSE FORMAT (strict JSON):
{
  "moments": [
    {
      "start": 125.5,
      "title": "Short descriptive title"
    }
  ]
}

If nothing matches: {"moments": []}
"""


# ---------------------------------------------------------------------------
# Moment Finder
# ---------------------------------------------------------------------------

class MomentFinder:
    def __init__(self):
        self._client = None

    def _get_client(self) -> AsyncOpenAI:
        """Lazy client init — reads OPENAI_API_KEY at call time, not import time."""
        if self._client is None:
            key = OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
            if not key:
                raise RuntimeError("OPENAI_API_KEY not configured")
            self._client = AsyncOpenAI(api_key=key)
        return self._client

    async def find_moments(
        self,
        segments: List[Dict[str, Any]],
        sub_query: str,
        reasoning: str,
        video_id: str,
        video_title: str = "",
        platform: Platform = Platform.YOUTUBE,
        sub_query_order: int = 0,
        sub_query_title: str = "",
    ) -> List[FoundMoment]:
        """
        Scan filtered transcript segments to find exact moments.

        Args:
            segments: 1-2 macro-segments from vector similarity search.
                      Each has: text, startTime, endTime, segmentIndex.
            sub_query: The optimized search query for this sub-item.
            reasoning: The classifier's reasoning trace (provides intent context).
            video_id: YouTube video ID.
            video_title: Video title for context.
            platform: Source platform.
            sub_query_order: Order index for structured output sequencing.

        Returns:
            List of FoundMoment objects with embed URLs.
        """
        client = self._get_client()

        if not segments:
            return []

        # Build transcript text from segments
        transcript_parts = []
        for seg in segments:
            start = seg.get("startTime", seg.get("start_time", 0))
            end = seg.get("endTime", seg.get("end_time", 0))
            text = seg.get("text", "")
            transcript_parts.append(
                f"[{_fmt_time(start)} - {_fmt_time(end)}]\n{text}"
            )

        transcript_text = "\n\n".join(transcript_parts)

        user_message = (
            f"VIDEO: \"{video_title}\"\n"
            f"VIDEO ID: {video_id}\n\n"
            f"QUERY: {sub_query}\n"
            f"CONTEXT: {reasoning}\n\n"
            f"TRANSCRIPT SEGMENTS:\n{transcript_text}"
        )

        total_chars = sum(len(seg.get("text", "")) for seg in segments)
        logger.info(
            f"[MomentFinder] Scanning {len(segments)} segments "
            f"({total_chars:,} chars) for: {sub_query[:60]}..."
        )

        t0 = time.perf_counter()
        try:
            response = await client.chat.completions.create(
                model=MOMENT_FINDER_MODEL,
                messages=[
                    {"role": "system", "content": MOMENT_FINDER_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=1000,
            )

            elapsed = time.perf_counter() - t0
            raw = response.choices[0].message.content
            data = json.loads(raw)
            raw_moments = data.get("moments", [])[:1]  # Take only the best moment
            logger.info(
                f"[MomentFinder] LLM call took {elapsed:.2f}s | "
                f"raw moments returned: {len(raw_moments)}"
            )

            # Build FoundMoment objects
            found: List[FoundMoment] = []
            for m in raw_moments:
                start = _coerce_seconds(
                    m.get("start"),
                    m.get("start_time"),
                    m.get("timestamp"),
                    m.get("time"),
                )
                if start is None:
                    logger.warning(
                        "[MomentFinder] Could not parse start time from model output: "
                        f"{m}"
                    )
                    continue

                start = max(0.0, start)

                # End time is optional; we keep it for metadata while embeds use start only.
                end = _coerce_seconds(m.get("end"), m.get("end_time"))
                if end is None or end <= start:
                    end = start + 45
                if end - start < 10:
                    end = start + 15  # Minimum 15s

                embed_url = self._build_embed_url(video_id, start, platform)

                found.append(FoundMoment(
                    video_id=video_id,
                    start=round(start, 2),
                    end=round(end, 2),
                    title=m.get("title", "Moment"),
                    description=m.get("description", ""),
                    embed_url=embed_url,
                    video_title=video_title,
                    platform=platform,
                    sub_query_order=sub_query_order,
                    sub_query_title=sub_query_title,
                ))

            for m in found:
                logger.info(
                    f"[MomentFinder] Moment: {m.title} | "
                    f"{_fmt_time(m.start)}-{_fmt_time(m.end)} | "
                    f"{m.embed_url}"
                )

            logger.info(
                f"[MomentFinder] Found {len(found)} moments in "
                f"\"{video_title[:40]}\""
            )
            return found

        except json.JSONDecodeError as e:
            logger.error(
                f"[MomentFinder] JSON parse failed: {e} | "
                f"raw response: {raw[:200] if 'raw' in dir() else 'N/A'}"
            )
            return []
        except Exception as e:
            logger.error(f"[MomentFinder] Failed for {video_id}: {e}", exc_info=True)
            return []

    def _build_embed_url(
        self,
        video_id: str,
        start: float,
        platform: Platform,
    ) -> str:
        """Construct the platform-specific embed URL with start time only."""
        if platform == Platform.YOUTUBE:
            return (
                f"https://www.youtube.com/embed/{video_id}"
                f"?start={int(start)}"
                f"&autoplay=0&rel=0"
            )
        elif platform == Platform.TIKTOK:
            # TikTok uses /player/v1/{id} — no URL timestamp, seekTo via postMessage
            return f"https://www.tiktok.com/player/v1/{video_id}"
        elif platform == Platform.X:
            # X posts are embedded via react-tweet component, URL is the post link
            return f"https://x.com/i/status/{video_id}"
        return ""


def _fmt_time(seconds: float) -> str:
    """Format seconds as MM:SS."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def _coerce_seconds(*values: Any) -> Optional[float]:
    """
    Parse a timestamp into seconds.

    Accepts:
    - numeric seconds (int/float or numeric string)
    - "M:SS" / "MM:SS"
    - "H:MM:SS"
    """
    for value in values:
        if value is None:
            continue

        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                continue

            # Numeric string seconds
            try:
                return float(raw)
            except ValueError:
                pass

            # Timestamp string (M:SS, MM:SS, H:MM:SS)
            parts = raw.split(":")
            if 2 <= len(parts) <= 3:
                try:
                    nums = [int(p) for p in parts]
                except ValueError:
                    continue

                if len(nums) == 2:
                    minutes, seconds = nums
                    return float(minutes * 60 + seconds)

                hours, minutes, seconds = nums
                return float(hours * 3600 + minutes * 60 + seconds)

    return None
