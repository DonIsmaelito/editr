"""
Editr — Video Editor Pipeline Orchestrator

Wires all 4 editing steps together for a single video:
  1. Video Understanding (Gemini 3.1 Pro) — analyze video, get transcript + cue moments + music mood
  2. Music Generation (Lyria) + loop extension — generate background track to match video duration
  3. Overlay Generation (Nano Banana 2) — generate images for each cue moment in parallel
  4. Composition (FFmpeg) — overlay images + mix audio into final video

Can run in two modes:
  A. SANDBOX MODE: video is inside a Daytona sandbox, API calls on host, FFmpeg in sandbox
  B. LOCAL MODE: everything runs on the host machine (fallback)

HOST ↔ SANDBOX TRANSFER MAP:
  SBX → HOST: video bytes (for Gemini analysis)
  HOST → SBX: overlay PNGs (from Nano Banana)
  HOST → SBX: music WAV (from Lyria + loop extension)
  HOST → SBX: FFmpeg command (inline script)
  SBX → HOST: final edited video bytes (for GCS upload)
"""

import asyncio
import base64
import io
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src_2.config import MUSIC_SEGMENT_DURATION
from src_2.editor.video_understanding import (
    VideoUnderstandingResult,
    analyze_video_with_gemini_pro,
)
from src_2.editor.music_track_generator import (
    generate_music_track_via_lyria,
    extend_track_to_match_video_duration,
)
from src_2.editor.overlay_image_generator import (
    GeneratedOverlay,
    generate_all_overlay_images_in_parallel,
)
from src_2.editor.video_composer import (
    CompositionResult,
    compose_video_locally,
    compose_video_in_sandbox,
)

logger = logging.getLogger(__name__)


@dataclass
class EditPipelineResult:
    """Complete result from the editing pipeline."""
    output_path: str              # local path or sandbox path of the edited video
    output_bytes: Optional[bytes] # raw bytes of the edited video (if read from sandbox)
    understanding: VideoUnderstandingResult
    overlays_applied: int
    has_music: bool
    total_seconds: float
    skipped: bool = False         # true if video was skipped (already edited, etc.)
    skip_reason: str = ""
    music_preview: Optional[dict] = None
    overlay_previews: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main entry point: edit one video
# ---------------------------------------------------------------------------

async def run_editor_pipeline_for_single_video_in_sandbox(
    sandbox,
    video_sandbox_path: str,
    video_duration_hint: float = 0.0,
    on_progress=None,
) -> EditPipelineResult:
    """
    Full editing pipeline for one video inside a Daytona sandbox.

    Args:
        sandbox: SandboxManager instance with the video already downloaded
        video_sandbox_path: path to the MP4 inside the sandbox (e.g. /tmp/video.mp4)
        video_duration_hint: approximate duration from tikwm metadata
        on_progress: optional callback for progress events

    Returns:
        EditPipelineResult with the edited video bytes and metadata
    """
    t0 = time.perf_counter()

    async def emit(step, status, detail=""):
        if on_progress:
            await on_progress("editor_progress", {
                "step": step, "status": status, "detail": detail
            })

    # ======================================================================
    # STEP 1: Read video from sandbox → send to Gemini 3.1 Pro
    # This is the big transfer: video bytes come OUT of the sandbox
    # ======================================================================
    await emit("understanding", "started", "Reading video from sandbox...")

    logger.info(f"[EditorPipeline] Step 1: Reading video from sandbox for Gemini analysis")
    t_read = time.perf_counter()
    video_b64 = await sandbox.read_file_b64(video_sandbox_path)
    video_bytes = base64.b64decode(video_b64)
    logger.info(
        f"[EditorPipeline] Video read from sandbox in {time.perf_counter()-t_read:.1f}s | "
        f"size={len(video_bytes)/(1024*1024):.1f}MB"
    )

    await emit("understanding", "analyzing", "Gemini 3.1 Pro is watching the video...")

    # Send to Gemini 3.1 Pro for video understanding
    understanding = await analyze_video_with_gemini_pro(
        video_bytes=video_bytes,
        video_duration_hint=video_duration_hint,
    )

    await emit("understanding", "done",
        f"{len(understanding.transcript)} transcript segments, "
        f"{len(understanding.cue_moments)} cue moments"
    )

    # Check if video is already well-edited — skip if so
    if understanding.has_existing_captions and understanding.has_existing_effects:
        logger.info(
            f"[EditorPipeline] Video already has captions AND effects — skipping"
        )
        return EditPipelineResult(
            output_path="", output_bytes=None,
            understanding=understanding,
            overlays_applied=0, has_music=False,
            total_seconds=time.perf_counter() - t0,
            skipped=True,
            skip_reason="Video already has captions and effects",
        )

    # ======================================================================
    # STEP 2 + 3: Generate music AND overlay images IN PARALLEL
    # Music gen + overlay gen are independent — run them simultaneously
    # These are API calls on the HOST, not in the sandbox
    # ======================================================================
    await emit("assets", "started", "Generating music and overlay images...")

    logger.info(f"[EditorPipeline] Steps 2+3: Generating music + overlays in parallel")

    video_duration = understanding.video_duration or video_duration_hint or 60.0

    # Launch music gen and overlay gen in parallel
    music_task = _generate_and_extend_music_track(
        understanding=understanding,
        video_duration=video_duration,
    )

    overlay_task = generate_all_overlay_images_in_parallel(
        cue_moments=understanding.cue_moments,
    )

    # Wait for both to complete
    music_wav_bytes, overlays = await asyncio.gather(music_task, overlay_task)

    await emit("assets", "done",
        f"Music: {'yes' if music_wav_bytes else 'no'}, "
        f"Overlays: {len(overlays)}/{len(understanding.cue_moments)}"
    )

    # ======================================================================
    # STEP 4: Upload assets to sandbox + run FFmpeg composition
    # Assets go INTO the sandbox, FFmpeg renders, result stays in sandbox
    # ======================================================================
    await emit("rendering", "started", "Running FFmpeg in sandbox...")

    output_sandbox_path = video_sandbox_path.replace(".mp4", "_edited.mp4")
    caption_segments = _build_caption_segments(understanding)

    logger.info(f"[EditorPipeline] Step 4: Composing in sandbox → {output_sandbox_path}")

    composition = await compose_video_in_sandbox(
        sandbox=sandbox,
        input_video_sandbox_path=video_sandbox_path,
        output_video_sandbox_path=output_sandbox_path,
        music_wav_bytes=music_wav_bytes,
        overlays=overlays,
        caption_segments=caption_segments,
        music_volume=0.15,  # very quiet — speaker voice is priority
    )

    await emit("rendering", "done", f"Rendered: {output_sandbox_path}")

    # ======================================================================
    # STEP 5: Read the edited video OUT of the sandbox
    # This is the final big transfer: edited video comes back to us
    # ======================================================================
    await emit("reading", "started", "Reading edited video from sandbox...")

    logger.info(f"[EditorPipeline] Step 5: Reading edited video from sandbox")
    t_read = time.perf_counter()
    edited_b64 = await sandbox.read_file_b64(output_sandbox_path)
    edited_bytes = base64.b64decode(edited_b64)
    logger.info(
        f"[EditorPipeline] Edited video read in {time.perf_counter()-t_read:.1f}s | "
        f"size={len(edited_bytes)/(1024*1024):.1f}MB"
    )

    await emit("reading", "done", f"{len(edited_bytes)/(1024*1024):.1f}MB")

    total = time.perf_counter() - t0
    logger.info(
        f"[EditorPipeline] Pipeline complete in {total:.1f}s | "
        f"overlays={len(overlays)} | "
        f"has_music={music_wav_bytes is not None}"
    )

    return EditPipelineResult(
        output_path=output_sandbox_path,
        output_bytes=edited_bytes,
        understanding=understanding,
        overlays_applied=len(overlays),
        has_music=music_wav_bytes is not None,
        total_seconds=total,
    )


# ---------------------------------------------------------------------------
# LOCAL MODE: edit a video without a sandbox (fallback)
# ---------------------------------------------------------------------------

async def run_editor_pipeline_for_single_video_locally(
    video_path: str,
    output_path: str,
    video_duration_hint: float = 0.0,
    asset_dir: Optional[str] = None,
    on_progress=None,
) -> EditPipelineResult:
    """
    Full editing pipeline running entirely on the host machine.
    No Daytona sandbox needed — uses local FFmpeg.
    """
    t0 = time.perf_counter()
    import tempfile

    async def emit(step, status, detail="", payload: Optional[dict] = None):
        if on_progress:
            event = {
                "step": step, "status": status, "detail": detail
            }
            if payload:
                event.update(payload)
            await on_progress("editor_progress", event)

    # Step 1: Read video and send to Gemini
    await emit("understanding", "started", "Gemini is reading the downloaded clip...")
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    understanding = await analyze_video_with_gemini_pro(
        video_bytes=video_bytes,
        video_duration_hint=video_duration_hint,
    )
    await emit(
        "understanding",
        "done",
        (
            f"Found {len(understanding.cue_moments)} cue moment"
            f"{'s' if len(understanding.cue_moments) != 1 else ''}"
        ),
        {
            "summary": understanding.video_summary,
            "cueMoments": [
                {
                    "timestamp": cue.timestamp,
                    "duration": cue.duration,
                    "spokenText": cue.spoken_text,
                    "nounType": cue.noun_type,
                    "imagePrompt": cue.image_prompt,
                    "position": cue.overlay_position,
                }
                for cue in understanding.cue_moments
            ],
            "musicMood": {
                "prompt": understanding.music_mood.prompt,
                "bpm": understanding.music_mood.bpm,
                "energy": understanding.music_mood.energy,
            } if understanding.music_mood else None,
        },
    )

    if understanding.has_existing_captions and understanding.has_existing_effects:
        return EditPipelineResult(
            output_path="", output_bytes=None,
            understanding=understanding,
            overlays_applied=0, has_music=False,
            total_seconds=time.perf_counter() - t0,
            skipped=True, skip_reason="Already edited",
        )

    video_duration = understanding.video_duration or video_duration_hint or 60.0

    # Steps 2+3: Generate music + overlays in parallel
    await emit("assets", "started", "Generating music and overlay assets...")
    music_wav_bytes, overlays = await asyncio.gather(
        _generate_and_extend_music_track(understanding, video_duration),
        generate_all_overlay_images_in_parallel(understanding.cue_moments),
    )

    music_preview, overlay_previews = _persist_preview_assets(
        asset_dir=asset_dir,
        video_path=video_path,
        music_wav_bytes=music_wav_bytes,
        overlays=overlays,
        understanding=understanding,
    )

    await emit(
        "assets",
        "done",
        (
            f"Prepared {len(overlay_previews)} overlay preview"
            f"{'s' if len(overlay_previews) != 1 else ''}"
            f"{' and music' if music_preview else ''}"
        ),
        {
            "music": music_preview,
            "overlays": overlay_previews,
        },
    )

    # Step 4: Compose locally
    await emit("rendering", "started", "Composing the final edit with FFmpeg...")
    caption_segments = _build_caption_segments(understanding)

    music_wav_path = None
    if music_wav_bytes:
        # Write music WAV to a temp file for FFmpeg
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(music_wav_bytes)
        tmp.close()
        music_wav_path = tmp.name

    try:
        composition = compose_video_locally(
            input_video_path=video_path,
            output_video_path=output_path,
            music_wav_path=music_wav_path,
            overlays=overlays,
            caption_segments=caption_segments,
            music_volume=0.15,
        )
    finally:
        if music_wav_path:
            os.unlink(music_wav_path)

    await emit("rendering", "done", "Final cut rendered locally.")

    with open(output_path, "rb") as f:
        edited_bytes = f.read()

    total = time.perf_counter() - t0
    return EditPipelineResult(
        output_path=output_path,
        output_bytes=edited_bytes,
        understanding=understanding,
        overlays_applied=len(overlays),
        has_music=music_wav_bytes is not None,
        total_seconds=total,
        music_preview=music_preview,
        overlay_previews=overlay_previews,
    )


# ---------------------------------------------------------------------------
# Helper: generate music + extend to match video duration
# ---------------------------------------------------------------------------

async def _generate_and_extend_music_track(
    understanding: VideoUnderstandingResult,
    video_duration: float,
) -> Optional[bytes]:
    """Generate a Lyria track and extend it to match the video duration."""

    if not understanding.music_mood:
        logger.info("[EditorPipeline] No music mood from Gemini — skipping music generation")
        return None

    # Generate the initial track via Lyria
    # NOTE: Lyria may not be available (404) — treat as non-fatal, video edits without music
    segment_duration = min(float(video_duration), float(MUSIC_SEGMENT_DURATION))
    logger.info(
        f"[EditorPipeline] Requesting Lyria segment | "
        f"segment={segment_duration:.1f}s | target_video={video_duration:.1f}s"
    )
    try:
        wav_bytes = await generate_music_track_via_lyria(
            prompt=understanding.music_mood.prompt,
            target_duration_seconds=segment_duration,
        )
    except Exception as e:
        logger.warning(f"[EditorPipeline] Lyria music generation failed (non-fatal): {e}")
        return None

    if not wav_bytes:
        logger.warning("[EditorPipeline] Lyria returned no audio — skipping music")
        return None

    # Extend the track if it's shorter than the video
    # (Lyria typically generates ~30s, videos can be up to 150s)
    try:
        extended_wav = extend_track_to_match_video_duration(
            wav_bytes=wav_bytes,
            video_duration_seconds=video_duration,
        )
        return extended_wav
    except Exception as e:
        logger.warning(f"[EditorPipeline] Track extension failed: {e} — using raw track")
        return wav_bytes


def _build_caption_segments(understanding: VideoUnderstandingResult) -> list[dict]:
    """Convert Gemini transcript segments into FFmpeg caption inputs."""
    segments: list[dict] = []

    for seg in understanding.transcript:
        text = " ".join(seg.text.split())
        if not text:
            continue

        start = max(float(seg.start), 0.0)
        end = max(float(seg.end), start + 0.1)
        segments.append({
            "start": start,
            "end": end,
            "text": text,
        })

    logger.info(
        f"[EditorPipeline] Prepared {len(segments)} caption segments "
        f"from {len(understanding.transcript)} transcript chunks"
    )
    return segments


def _persist_preview_assets(
    asset_dir: Optional[str],
    video_path: str,
    music_wav_bytes: Optional[bytes],
    overlays: list[GeneratedOverlay],
    understanding: VideoUnderstandingResult,
) -> tuple[Optional[dict], list[dict]]:
    """Persist generated preview assets for the frontend, if a target dir is provided."""
    if not asset_dir:
        return None, []

    base_dir = Path(asset_dir)
    overlays_dir = base_dir / "overlays"
    music_dir = base_dir / "music"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    music_dir.mkdir(parents=True, exist_ok=True)

    video_stem = Path(video_path).stem

    music_preview = None
    if music_wav_bytes:
        music_path = music_dir / f"{video_stem}_generated.wav"
        music_path.write_bytes(music_wav_bytes)
        music_preview = {
            "localPath": str(music_path).replace("\\", "/"),
            "prompt": understanding.music_mood.prompt if understanding.music_mood else "",
            "bpm": understanding.music_mood.bpm if understanding.music_mood else None,
            "energy": understanding.music_mood.energy if understanding.music_mood else None,
        }
        logger.info(
            f"[EditorPipeline] Music preview saved | path={music_preview['localPath']} | "
            f"bytes={len(music_wav_bytes)}"
        )

    overlay_previews: list[dict] = []
    for index, overlay in enumerate(overlays):
        safe_word = re.sub(r"[^a-z0-9_-]+", "_", overlay.cue.spoken_text.lower()).strip("_")
        if not safe_word:
            safe_word = f"overlay_{index}"
        overlay_path = overlays_dir / f"{index:02d}_{safe_word}.png"
        overlay_path.write_bytes(overlay.png_bytes)
        overlay_previews.append({
            "localPath": str(overlay_path).replace("\\", "/"),
            "spokenText": overlay.cue.spoken_text,
            "timestamp": overlay.cue.timestamp,
            "duration": overlay.cue.duration,
            "imagePrompt": overlay.cue.image_prompt,
            "position": overlay.cue.overlay_position,
            "nounType": overlay.cue.noun_type,
        })

    logger.info(
        f"[EditorPipeline] Overlay previews saved | count={len(overlay_previews)} | "
        f"asset_dir={str(base_dir).replace('\\', '/')}"
    )

    return music_preview, overlay_previews
