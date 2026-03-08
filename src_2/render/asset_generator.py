"""
Editr Asset Generator

Generates visual and audio assets for the edit:
- Nano Banana 2 popup overlays (gemini-3.1-flash-image-preview)
- Lyria RealTime music segments (lyria-realtime-exp → raw 16-bit PCM @ 48kHz stereo)

Assets are generated and uploaded into the Daytona sandbox for FFmpeg to consume.

IMPORTANT API NOTES:
- Nano Banana 2 uses generate_content() with image_config, NOT generate_images()
- Lyria outputs raw 16-bit PCM at 48kHz stereo — we must wrap it in a WAV header
  so FFmpeg can read it
"""

import asyncio
import base64
import logging
import struct
import time
from typing import List, Optional

from src_2.config import GOOGLE_CLOUD_API_KEY, MUSIC_SEGMENT_DURATION, NANO_BANANA_MODEL, LYRIA_MODEL
from src_2.render.render_models import EditPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: Convert raw PCM bytes to WAV format
# ---------------------------------------------------------------------------

def _convert_raw_pcm_to_wav_with_header(
    pcm_data: bytes,
    sample_rate: int = 48000,
    channels: int = 2,
    bits_per_sample: int = 16,
) -> bytes:
    """
    Wrap raw 16-bit PCM audio data in a proper WAV (RIFF) header.

    Lyria RealTime outputs raw 16-bit PCM at 48kHz stereo. FFmpeg needs
    a WAV header to know the sample rate, channels, and bit depth.

    The WAV header is exactly 44 bytes:
    - RIFF chunk descriptor (12 bytes)
    - fmt sub-chunk (24 bytes)
    - data sub-chunk header (8 bytes)
    """
    data_size = len(pcm_data)
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8

    # Build the 44-byte WAV header using struct.pack
    header = struct.pack(
        '<4sI4s'       # RIFF header: "RIFF", file size, "WAVE"
        '4sIHHIIHH'    # fmt chunk: "fmt ", chunk size, audio format, channels, sample rate, byte rate, block align, bits
        '4sI',         # data chunk: "data", data size
        b'RIFF',
        36 + data_size,  # total file size minus 8 bytes for RIFF header
        b'WAVE',
        b'fmt ',
        16,              # fmt chunk size (16 for PCM)
        1,               # audio format (1 = PCM)
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data',
        data_size,
    )

    logger.debug(
        f"[AssetGen] WAV header built | "
        f"pcm_size={data_size} | sample_rate={sample_rate} | "
        f"channels={channels} | bits={bits_per_sample} | "
        f"total_wav_size={len(header) + data_size}"
    )

    return header + pcm_data


# ---------------------------------------------------------------------------
# Helper: Extract image bytes from Gemini response parts
# ---------------------------------------------------------------------------

def _extract_image_bytes_from_gemini_response(response) -> Optional[bytes]:
    """
    Iterate through Gemini response parts and find the first inline_data
    part containing image bytes. Returns None if no image found.

    Nano Banana 2 (gemini-3.1-flash-image-preview) returns images as
    inline_data parts within response.candidates[0].content.parts.
    """
    if not response.candidates:
        logger.warning("[AssetGen] No candidates in Gemini image response")
        return None

    candidate = response.candidates[0]
    if not candidate.content or not candidate.content.parts:
        logger.warning("[AssetGen] No parts in Gemini image response candidate")
        return None

    # Walk through all parts looking for inline_data (image bytes)
    for i, part in enumerate(candidate.content.parts):
        if hasattr(part, "inline_data") and part.inline_data:
            image_data = part.inline_data.data
            # inline_data.data can be raw bytes or base64 string
            if isinstance(image_data, str):
                image_data = base64.b64decode(image_data)
            logger.debug(
                f"[AssetGen] Found image in part {i} | size={len(image_data)} bytes"
            )
            return image_data

    logger.warning("[AssetGen] No inline_data image found in any response part")
    return None


# ---------------------------------------------------------------------------
# Helper: Extract audio bytes from Lyria response
# ---------------------------------------------------------------------------

def _extract_audio_bytes_from_lyria_response(response) -> Optional[bytes]:
    """
    Extract raw PCM audio bytes from a Lyria RealTime response.

    Lyria outputs raw 16-bit PCM at 48kHz stereo. The response may have
    the audio in different locations depending on the API version:
    1. response.audio (direct attribute)
    2. response.candidates[0].content.parts[*].inline_data.data
    """
    # Try direct .audio attribute first
    if hasattr(response, "audio") and response.audio:
        audio_data = response.audio
        if isinstance(audio_data, str):
            audio_data = base64.b64decode(audio_data)
        logger.debug(f"[AssetGen] Found audio via response.audio | size={len(audio_data)} bytes")
        return audio_data

    # Try extracting from candidates/parts
    if hasattr(response, "candidates") and response.candidates:
        candidate = response.candidates[0]
        if hasattr(candidate, "content") and candidate.content and candidate.content.parts:
            for i, part in enumerate(candidate.content.parts):
                if hasattr(part, "inline_data") and part.inline_data:
                    audio_data = part.inline_data.data
                    if isinstance(audio_data, str):
                        audio_data = base64.b64decode(audio_data)
                    logger.debug(
                        f"[AssetGen] Found audio in candidate part {i} | "
                        f"size={len(audio_data)} bytes"
                    )
                    return audio_data

    logger.warning("[AssetGen] No audio data found in Lyria response")
    return None


# ---------------------------------------------------------------------------
# Image generation: Nano Banana 2 (gemini-3.1-flash-image-preview)
# ---------------------------------------------------------------------------

async def _generate_popup_overlay_png_via_nano_banana(
    prompt: str,
    popup_index: int,
    sandbox,
) -> str:
    """
    Generate a single popup overlay PNG using Nano Banana 2 and upload to sandbox.

    Uses generate_content() with image_config (NOT generate_images — that's Imagen).
    Model: gemini-3.1-flash-image-preview
    Output: PNG image bytes extracted from response.candidates[0].content.parts

    Returns the remote path inside the sandbox, or "" on failure.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise RuntimeError("google-genai not installed")

    client = genai.Client(api_key=GOOGLE_CLOUD_API_KEY)

    logger.info(
        f"[AssetGen] Generating popup {popup_index} via Nano Banana 2 | "
        f"model={NANO_BANANA_MODEL} | prompt_len={len(prompt)}"
    )

    t0 = time.perf_counter()

    # Call generate_content with image_config — this is how Nano Banana 2 works
    # (NOT generate_images, which is the old Imagen API)
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=NANO_BANANA_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            image_config=types.ImageConfig(
                aspect_ratio="1:1",     # square popup overlay
                image_size="0.5K",      # small — it's just a popup graphic
            ),
        ),
    )

    elapsed = time.perf_counter() - t0

    # Extract image bytes from the response parts
    image_bytes = _extract_image_bytes_from_gemini_response(response)

    if not image_bytes:
        logger.warning(
            f"[AssetGen] No image generated for popup {popup_index} "
            f"after {elapsed:.2f}s"
        )
        return ""

    # Upload the PNG to the sandbox so FFmpeg can use it
    remote_path = f"/tmp/popup_{popup_index}.png"
    await sandbox.upload_file(image_bytes, remote_path)

    logger.info(
        f"[AssetGen] Popup {popup_index} generated in {elapsed:.2f}s | "
        f"image_size={len(image_bytes)} bytes | "
        f"uploaded_to={remote_path}"
    )
    return remote_path


# ---------------------------------------------------------------------------
# Music generation: Lyria RealTime (lyria-realtime-exp)
# ---------------------------------------------------------------------------

async def _generate_music_segment_via_lyria_realtime(
    prompt: str,
    duration_seconds: int,
    segment_index: int,
    sandbox,
) -> str:
    """
    Generate a music segment using Lyria RealTime and upload as WAV to sandbox.

    Model: lyria-realtime-exp
    Input: text (weighted prompts describing desired music)
    Output: raw 16-bit PCM at 48kHz stereo — we wrap in WAV header for FFmpeg

    Returns the remote path inside the sandbox, or "" on failure.
    """
    try:
        from google import genai
    except ImportError:
        raise RuntimeError("google-genai not installed")

    client = genai.Client(api_key=GOOGLE_CLOUD_API_KEY)

    logger.info(
        f"[AssetGen] Generating music segment {segment_index} via Lyria | "
        f"model={LYRIA_MODEL} | duration={duration_seconds}s | "
        f"prompt_len={len(prompt)}"
    )

    t0 = time.perf_counter()

    # Call Lyria RealTime — it uses generate_content with text prompts
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=LYRIA_MODEL,
        contents=f"Generate a {duration_seconds}-second music track: {prompt}",
    )

    elapsed = time.perf_counter() - t0

    # Extract the raw PCM audio bytes from the response
    raw_pcm_bytes = _extract_audio_bytes_from_lyria_response(response)

    if not raw_pcm_bytes:
        logger.warning(
            f"[AssetGen] No audio generated for segment {segment_index} "
            f"after {elapsed:.2f}s"
        )
        return ""

    # Convert raw PCM to WAV format (FFmpeg needs the WAV header to know
    # sample rate, channels, and bit depth)
    wav_bytes = _convert_raw_pcm_to_wav_with_header(raw_pcm_bytes)

    # Upload the WAV file to the sandbox
    remote_path = f"/tmp/music_{segment_index}.wav"
    await sandbox.upload_file(wav_bytes, remote_path)

    # Calculate approximate audio duration from PCM size
    # (48000 Hz * 2 channels * 2 bytes per sample = 192000 bytes per second)
    approx_duration = len(raw_pcm_bytes) / 192000

    logger.info(
        f"[AssetGen] Music segment {segment_index} generated in {elapsed:.2f}s | "
        f"pcm_size={len(raw_pcm_bytes)} bytes | "
        f"wav_size={len(wav_bytes)} bytes | "
        f"approx_duration={approx_duration:.1f}s | "
        f"uploaded_to={remote_path}"
    )
    return remote_path


# ---------------------------------------------------------------------------
# Main entry point: generate all popup + music assets in parallel
# ---------------------------------------------------------------------------

async def generate_all_popup_and_music_assets_in_parallel(
    edit_plan: EditPlan,
    sandbox,
) -> EditPlan:
    """
    Generate all assets (popup PNGs + music WAVs) in parallel and update
    the EditPlan with local paths inside the sandbox.

    Failed generations are silently removed from the plan (the video
    will render without those particular effects).
    """
    t0 = time.perf_counter()

    logger.info(
        f"[AssetGen] Starting asset generation | "
        f"popups={len(edit_plan.popups)} | "
        f"music_segments={len(edit_plan.audio_ops)}"
    )

    tasks = []

    # Queue up all popup PNG generations
    for i, popup in enumerate(edit_plan.popups):
        prompt = (
            f"Simple popup emoji/icon overlay for a viral TikTok video. "
            f"Transparent background, bold and eye-catching. "
            f"Position: {popup.position}"
        )
        tasks.append((
            "popup", i,
            _generate_popup_overlay_png_via_nano_banana(prompt, i, sandbox),
        ))

    # Queue up all music segment generations
    for i, audio_op in enumerate(edit_plan.audio_ops):
        duration = min(int(audio_op.end - audio_op.start), MUSIC_SEGMENT_DURATION)
        prompt = "Upbeat catchy background music for a viral short-form video"
        tasks.append((
            "music", i,
            _generate_music_segment_via_lyria_realtime(prompt, duration, i, sandbox),
        ))

    # Run all generations in parallel
    if tasks:
        logger.info(f"[AssetGen] Launching {len(tasks)} parallel generations...")

        results = await asyncio.gather(
            *[t[2] for t in tasks],
            return_exceptions=True,
        )

        # Map results back to the edit plan
        for (asset_type, index, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                logger.error(
                    f"[AssetGen] {asset_type} {index} FAILED: {result}"
                )
                continue

            if asset_type == "popup" and result:
                edit_plan.popups[index].image_path = result
                logger.debug(f"[AssetGen] Popup {index} path set: {result}")
            elif asset_type == "music" and result:
                edit_plan.audio_ops[index].audio_path = result
                logger.debug(f"[AssetGen] Music {index} path set: {result}")

    # Remove popups/audio ops that failed to generate (empty paths)
    before_popups = len(edit_plan.popups)
    before_audio = len(edit_plan.audio_ops)
    edit_plan.popups = [p for p in edit_plan.popups if p.image_path]
    edit_plan.audio_ops = [a for a in edit_plan.audio_ops if a.audio_path]

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[AssetGen] All assets done in {elapsed:.2f}s | "
        f"popups={len(edit_plan.popups)}/{before_popups} succeeded | "
        f"music={len(edit_plan.audio_ops)}/{before_audio} succeeded"
    )

    return edit_plan
