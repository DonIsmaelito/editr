"""
Editr — Video Understanding via Gemini 3.1 Pro

Sends the downloaded TikTok video to Gemini 3.1 Pro for deep analysis.
Returns structured JSON with:
  - Full transcript with timestamps
  - Cue moments (descriptive nouns/brands/concepts worth overlaying images for)
  - Music mood description (for Lyria generation)
  - Edit signals (has captions, has effects, etc.)

This is the BRAIN of the editing pipeline. Its output drives everything else:
  - Cue moments → Nano Banana 2 image generation
  - Music mood → Lyria track generation
  - Transcript → caption overlay timing
"""

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

from src_2.config import GEMINI_MODEL, GOOGLE_CLOUD_API_KEY

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------

@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str

@dataclass
class CueMoment:
    """A moment in the video where the speaker says something visual/specific
    that we can generate an overlay image for."""
    timestamp: float          # when the noun is spoken (seconds)
    duration: float           # how long to show the overlay (seconds)
    spoken_text: str          # the specific word/phrase ("Google", "stock market")
    noun_type: str            # "company", "brand", "concept", "person", "place", "object"
    image_prompt: str         # description for Nano Banana to generate the overlay image
    overlay_position: str     # "top_right", "center_right", "top_left", "bottom_right"

@dataclass
class MusicMood:
    """Description of the background track to generate with Lyria."""
    prompt: str               # full Lyria generation prompt
    bpm: int                  # target BPM
    energy: str               # "low", "medium", "high"

@dataclass
class VideoUnderstandingResult:
    """Complete output from Gemini video analysis."""
    transcript: List[TranscriptSegment] = field(default_factory=list)
    cue_moments: List[CueMoment] = field(default_factory=list)
    music_mood: Optional[MusicMood] = None
    video_summary: str = ""
    has_existing_captions: bool = False
    has_existing_effects: bool = False
    has_existing_music: bool = False
    speaker_gender: str = "unknown"
    video_duration: float = 0.0


# ---------------------------------------------------------------------------
# The Gemini prompt — this is the most important prompt in the whole system
# ---------------------------------------------------------------------------

VIDEO_UNDERSTANDING_PROMPT = """Watch this video carefully. You are analyzing it for an AI video editor that will add overlay images at specific moments.

Return a JSON object with this EXACT structure:

{{
  "transcript": [
    {{"start": 0.0, "end": 2.5, "text": "exact words spoken"}},
    {{"start": 2.5, "end": 5.1, "text": "next segment"}}
  ],

  "cue_moments": [
    {{
      "timestamp": 2.5,
      "duration": 2.0,
      "spoken_text": "the specific noun mentioned",
      "noun_type": "company|brand|concept|person|place|object",
      "image_prompt": "A clear illustration of [the thing]. Iconic, clean, white background.",
      "overlay_position": "top_right"
    }}
  ],

  "video_summary": "one sentence describing what the person is talking about",
  "has_existing_captions": false,
  "has_existing_effects": false,
  "video_duration": 45.0
}}

RULES FOR CUE MOMENTS:
- Flag SPECIFIC, VISUAL nouns the viewer would want to SEE as a pop-up image
- Good: company names, brands, products, places, charts, specific objects
- Bad: abstract words, pronouns, common verbs, emotions
- image_prompt goes directly to an image AI — be specific and visual
- Example: "The Google logo — multicolored G on white. Iconic, recognizable."
- Example: "A stock chart — green line with candlesticks. Clean illustration."
- Minimum 5 seconds between cue moments
- Maximum 5 per video
- Each overlay is ~160px, appears in a corner for the specified duration

RULES FOR TRANSCRIPT:
- Segments of 2-4 seconds each
- Include ALL spoken words
- Accurate timestamps (within 0.5s)
"""

VIDEO_UNDERSTANDING_SYSTEM = (
    "You are a professional video editor's AI assistant. You watch short-form "
    "videos (TikTok/Reels) and produce precise, structured analysis for automated "
    "editing. Your timestamp accuracy is critical — the editing pipeline depends on it. "
    "Always return valid JSON. Never include markdown formatting or code blocks."
)


# ---------------------------------------------------------------------------
# Gemini client (reuses pattern from gemini_agents.py)
# ---------------------------------------------------------------------------

_cached_client = None

def _get_gemini_client():
    """Create or return cached google-genai Client."""
    global _cached_client
    if _cached_client is not None:
        return _cached_client

    try:
        from google import genai
    except ImportError:
        raise RuntimeError("google-genai not installed. Run: pip install google-genai")

    if not GOOGLE_CLOUD_API_KEY:
        raise RuntimeError("GOOGLE_CLOUD_API_KEY not configured")

    _cached_client = genai.Client(api_key=GOOGLE_CLOUD_API_KEY)
    logger.info(f"[VideoUnderstanding] Gemini client created | model={GEMINI_MODEL}")
    return _cached_client


# ---------------------------------------------------------------------------
# Main function: send video to Gemini, get back structured analysis
# ---------------------------------------------------------------------------

async def analyze_video_with_gemini_pro(
    video_bytes: bytes,
    video_duration_hint: float = 0.0,
) -> VideoUnderstandingResult:
    """
    Send a video file to Gemini 3.1 Pro for deep analysis.

    Args:
        video_bytes: raw MP4 bytes of the video
        video_duration_hint: approximate duration in seconds (from tikwm metadata)

    Returns:
        VideoUnderstandingResult with transcript, cue moments, music mood, etc.

    This is the most important API call in the pipeline. It drives:
      - Which frames get overlay images (cue_moments → Nano Banana)
      - What music to generate (music_mood → Lyria)
      - Where to put captions (transcript → FFmpeg drawtext)
    """
    from google.genai import types

    client = _get_gemini_client()
    video_size_mb = len(video_bytes) / (1024 * 1024)

    logger.info(
        f"[VideoUnderstanding] Sending video to Gemini 3.1 Pro | "
        f"size={video_size_mb:.1f}MB | model={GEMINI_MODEL}"
    )

    t0 = time.perf_counter()

    # Build multimodal content: video + analysis prompt
    # Note: from_bytes and from_text are keyword-only in google-genai SDK
    contents = [
        types.Part.from_bytes(data=video_bytes, mime_type="video/mp4"),
        types.Part.from_text(text=VIDEO_UNDERSTANDING_PROMPT),
    ]

    # Call Gemini 3.1 Pro (the big model — we need accuracy here)
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=VIDEO_UNDERSTANDING_SYSTEM,
            response_mime_type="application/json",
            temperature=1.0,  # Gemini 3 requires 1.0
            thinking_config=types.ThinkingConfig(thinking_level="medium"),
        ),
    )

    elapsed = time.perf_counter() - t0
    raw_text = response.text

    logger.info(
        f"[VideoUnderstanding] Gemini responded in {elapsed:.2f}s | "
        f"response_len={len(raw_text)}"
    )

    # Parse the JSON response into our structured output
    try:
        # Gemini sometimes wraps JSON in markdown code blocks — strip them
        clean_text = raw_text.strip()
        if clean_text.startswith("```"):
            # Remove ```json ... ``` wrapper
            first_newline = clean_text.index("\n")
            last_backticks = clean_text.rfind("```")
            clean_text = clean_text[first_newline+1:last_backticks].strip()
            logger.info(f"[VideoUnderstanding] Stripped markdown code block wrapper from response")

        data = json.loads(clean_text)
        logger.info(
            f"[VideoUnderstanding] JSON parsed successfully | "
            f"keys={list(data.keys())} | "
            f"transcript_count={len(data.get('transcript', []))} | "
            f"cue_count={len(data.get('cue_moments', []))}"
        )
    except json.JSONDecodeError as e:
        # Gemini sometimes outputs double closing braces or trailing garbage
        # Try to find the outermost valid JSON object
        logger.warning(f"[VideoUnderstanding] First JSON parse failed: {e} — attempting recovery")
        try:
            # Find the first { and try progressively shorter substrings
            first_brace = clean_text.index("{")
            # Walk backwards from the end to find the matching }
            depth = 0
            last_valid_end = -1
            for i, ch in enumerate(clean_text[first_brace:], start=first_brace):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        last_valid_end = i + 1
                        break

            if last_valid_end > first_brace:
                data = json.loads(clean_text[first_brace:last_valid_end])
                logger.info(
                    f"[VideoUnderstanding] JSON recovered by finding matching braces | "
                    f"keys={list(data.keys())}"
                )
            else:
                raise json.JSONDecodeError("No matching braces found", clean_text, 0)
        except (json.JSONDecodeError, ValueError) as e2:
            logger.error(
                f"[VideoUnderstanding] JSON PARSE FAILED even after recovery attempt\n"
                f"  Error: {e2}\n"
                f"  Raw response (first 500 chars): {raw_text[:500]}\n"
                f"  Raw response (last 200 chars): {raw_text[-200:]}"
            )
            return VideoUnderstandingResult()

    # Build the result object from parsed JSON
    result = VideoUnderstandingResult(
        transcript=[
            TranscriptSegment(
                start=seg.get("start", 0),
                end=seg.get("end", 0),
                text=seg.get("text", ""),
            )
            for seg in data.get("transcript", [])
        ],
        cue_moments=[
            CueMoment(
                timestamp=cm.get("timestamp", 0),
                duration=cm.get("duration", 1.5),
                spoken_text=cm.get("spoken_text", ""),
                noun_type=cm.get("noun_type", "concept"),
                image_prompt=cm.get("image_prompt", ""),
                overlay_position=cm.get("overlay_position", "top_right"),
            )
            for cm in data.get("cue_moments", [])
        ],
        music_mood=MusicMood(
            prompt=data.get("music_mood", {}).get("prompt", "soft ambient lo-fi, no lyrics, minimal"),
            bpm=data.get("music_mood", {}).get("bpm", 75),
            energy=data.get("music_mood", {}).get("energy", "low"),
        ) if data.get("music_mood") else None,
        video_summary=data.get("video_summary", ""),
        has_existing_captions=data.get("has_existing_captions", False),
        has_existing_effects=data.get("has_existing_effects", False),
        has_existing_music=data.get("has_existing_music", False),
        speaker_gender=data.get("speaker_gender", "unknown"),
        video_duration=data.get("video_duration", video_duration_hint),
    )

    logger.info(
        f"[VideoUnderstanding] Analysis complete | "
        f"transcript_segments={len(result.transcript)} | "
        f"cue_moments={len(result.cue_moments)} | "
        f"has_captions={result.has_existing_captions} | "
        f"has_effects={result.has_existing_effects} | "
        f"summary={result.video_summary[:60]}"
    )

    return result
