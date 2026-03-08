"""
Editr — Overlay Image Generator via Nano Banana 2

For each cue moment identified by Gemini video understanding, generates
a small overlay image using Nano Banana 2 (gemini-3.1-flash-image-preview).

These images pop up on screen when the speaker mentions specific
nouns/brands/concepts — like a visual aid that makes the video more engaging.

Flow:
  1. Receive list of CueMoments from video understanding
  2. For each cue, call Nano Banana 2 with the image_prompt
  3. Get back PNG bytes
  4. Return list of (cue_moment, png_bytes) pairs
"""

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from src_2.config import GOOGLE_CLOUD_API_KEY, NANO_BANANA_MODEL
from src_2.editor.video_understanding import CueMoment

logger = logging.getLogger(__name__)

OVERLAY_GENERATION_TIMEOUT_SECONDS = 60
_cached_client = None


@dataclass
class GeneratedOverlay:
    """A generated overlay image paired with its cue moment timing."""
    cue: CueMoment
    png_bytes: bytes
    width: int = 256
    height: int = 256


def _get_overlay_client():
    global _cached_client
    if _cached_client is not None:
        return _cached_client

    try:
        from google import genai
    except ImportError:
        raise RuntimeError("google-genai not installed")

    if not GOOGLE_CLOUD_API_KEY:
        raise RuntimeError("GOOGLE_CLOUD_API_KEY not configured")

    _cached_client = genai.Client(api_key=GOOGLE_CLOUD_API_KEY)
    logger.info(
        f"[OverlayGen] Nano Banana client created | model={NANO_BANANA_MODEL}"
    )
    return _cached_client


async def generate_overlay_image_for_single_cue_moment(
    cue: CueMoment,
    index: int,
) -> Optional[GeneratedOverlay]:
    """
    Generate one overlay PNG for a single cue moment.

    Uses Nano Banana 2 (gemini-3.1-flash-image-preview) via generate_content
    with image_config. The image is small (256x256) and clean — designed to
    be overlaid on a video frame without being distracting.
    """
    try:
        from google.genai import types
    except ImportError:
        raise RuntimeError("google-genai not installed")

    client = _get_overlay_client()

    # Build a detailed prompt for the overlay image
    # Context: the speaker in the TikTok says a specific noun (company, brand,
    # concept, place, object) and we want to show a relevant image as a small
    # pop-up overlay at that exact moment. Like a visual aid — when they say
    # "Google", a Google logo pops up in the corner for 2 seconds.
    prompt = (
        f"Generate a single image of: {cue.spoken_text}\n\n"
        f"Context: {cue.image_prompt}\n\n"
        f"REQUIREMENTS:\n"
        f"- This image will be used as a small pop-up overlay on a TikTok video\n"
        f"- It appears for {cue.duration:.0f} seconds when the speaker mentions '{cue.spoken_text}'\n"
        f"- The image must be IMMEDIATELY recognizable as '{cue.spoken_text}'\n"
        f"- Clean, simple, iconic — think logo, symbol, or clear illustration\n"
        f"- White or very light solid background (not transparent — we handle that in compositing)\n"
        f"- No text labels, no captions, no watermarks\n"
        f"- The image will be scaled to ~160x160 pixels so it must read clearly at small size\n"
        f"- High contrast, bold shapes, minimal detail"
    )

    logger.info(
        f"[OverlayGen] Generating overlay {index} | "
        f"noun='{cue.spoken_text}' | model={NANO_BANANA_MODEL} | "
        f"prompt_len={len(prompt)} | "
        f"ts={cue.timestamp:.2f}s | dur={cue.duration:.2f}s | "
        f"pos={cue.overlay_position}"
    )

    t0 = time.perf_counter()

    try:
        async with asyncio.timeout(OVERLAY_GENERATION_TIMEOUT_SECONDS):
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=NANO_BANANA_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    image_config=types.ImageConfig(
                        aspect_ratio="1:1",
                        image_size="0.5K",
                    ),
                ),
            )
    except TimeoutError:
        logger.error(
            f"[OverlayGen] Timed out after {OVERLAY_GENERATION_TIMEOUT_SECONDS}s "
            f"for '{cue.spoken_text}'"
        )
        return None
    except Exception as e:
        logger.error(
            f"[OverlayGen] Nano Banana call failed for '{cue.spoken_text}': {e}",
            exc_info=True,
        )
        return None

    elapsed = time.perf_counter() - t0

    # Extract image bytes from response parts
    png_bytes = _extract_image_bytes_from_response(response)
    if not png_bytes:
        logger.warning(
            f"[OverlayGen] No image returned for '{cue.spoken_text}' after {elapsed:.1f}s | "
            f"candidates={len(getattr(response, 'candidates', []) or [])}"
        )
        return None

    logger.info(
        f"[OverlayGen] Overlay {index} generated in {elapsed:.1f}s | "
        f"noun='{cue.spoken_text}' | size={len(png_bytes)} bytes"
    )

    return GeneratedOverlay(cue=cue, png_bytes=png_bytes)


def _extract_image_bytes_from_response(response) -> Optional[bytes]:
    """Walk through Gemini response parts to find inline_data image bytes."""
    candidates = getattr(response, "candidates", None)
    if not candidates:
        logger.warning("[OverlayGen] Response contained no candidates")
        return None

    candidate = candidates[0]
    if not candidate.content or not candidate.content.parts:
        logger.warning("[OverlayGen] Candidate contained no content parts")
        return None

    for part_index, part in enumerate(candidate.content.parts):
        if hasattr(part, "inline_data") and part.inline_data:
            data = part.inline_data.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            logger.info(
                f"[OverlayGen] Inline image bytes found in part {part_index} | "
                f"mime={getattr(part.inline_data, 'mime_type', 'unknown')}"
            )
            return data

    logger.warning(
        f"[OverlayGen] No inline image data found across {len(candidate.content.parts)} parts"
    )

    return None


async def generate_all_overlay_images_in_parallel(
    cue_moments: List[CueMoment],
) -> List[GeneratedOverlay]:
    """
    Generate overlay images for all cue moments in parallel.

    Each Nano Banana call takes ~3-5s, so running them in parallel
    (typically 3-5 images) finishes in ~5s total instead of ~20s sequential.
    """
    if not cue_moments:
        logger.info("[OverlayGen] No cue moments — skipping overlay generation")
        return []

    logger.info(f"[OverlayGen] Generating {len(cue_moments)} overlays in parallel...")
    t0 = time.perf_counter()

    # Launch all image generations in parallel
    tasks = [
        generate_overlay_image_for_single_cue_moment(cue, i)
        for i, cue in enumerate(cue_moments)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out failures
    overlays = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"[OverlayGen] Overlay {i} failed: {result}")
        elif result is not None:
            overlays.append(result)

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[OverlayGen] Generated {len(overlays)}/{len(cue_moments)} overlays in {elapsed:.1f}s"
    )

    return overlays
