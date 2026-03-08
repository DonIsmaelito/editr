"""
Editr — Video Composer (FFmpeg)

Takes the original video + generated assets (music WAV, overlay PNGs)
and produces the final edited video using FFmpeg.

This module builds FFmpeg commands and can run either:
  - Inside a Daytona sandbox (preferred — isolated, no local deps needed)
  - Locally on the host machine (fallback if sandbox is unavailable)

The composition does 4 things in a single FFmpeg pass:
  1. Overlay PNG images at specific timestamps (fade in/out for 3-5 frames)
  2. Draw captions from Gemini transcript segments
  3. Mix the Lyria background track under the original audio (low volume)
  4. Output the final MP4

WHY SINGLE PASS: Multiple FFmpeg passes means re-encoding the video multiple
times, each time losing quality. Single pass with filter_complex keeps quality
and is faster.
"""

import logging
import os
import shlex
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional

from PIL import Image

from src_2.editor.overlay_image_generator import GeneratedOverlay

logger = logging.getLogger(__name__)


@dataclass
class CompositionResult:
    """Output from the video composition step."""
    output_path: str
    duration_seconds: float
    file_size_bytes: int
    overlays_applied: int
    has_background_music: bool


# ---------------------------------------------------------------------------
# FFmpeg command builder
# ---------------------------------------------------------------------------

def build_ffmpeg_compose_command(
    input_video_path: str,
    output_video_path: str,
    music_track_path: Optional[str],
    overlay_files: List[dict],  # [{"path": "/tmp/overlay_0.png", "start": 2.5, "duration": 2.0, "position": "top_right"}]
    caption_segments: Optional[List[dict]] = None,  # [{"start": 0.0, "end": 2.5, "text": "hello world"}]
    music_volume: float = 0.15,  # very low — speaker voice is priority
    original_audio_volume: float = 1.0,
) -> List[str]:
    """
    Build the FFmpeg command for single-pass video composition.

    Args:
        input_video_path: path to original MP4
        output_video_path: path for rendered output
        music_track_path: path to the Lyria WAV (or None if no music)
        overlay_files: list of overlay PNGs with timing info
        music_volume: volume level for background music (0.0-1.0, default 0.15 = very quiet)
        original_audio_volume: volume for the original audio track (default 1.0 = unchanged)

    Returns:
        FFmpeg command as a list of strings (for subprocess.run)
    """

    cmd = ["ffmpeg", "-y"]  # -y = overwrite output

    # Input 0: original video
    cmd.extend(["-i", input_video_path])

    # Input 1: background music (if provided)
    if music_track_path:
        cmd.extend(["-i", music_track_path])

    normalized_overlays = _normalize_overlay_files(overlay_files)

    # Input 2+: overlay images
    for i, overlay in enumerate(normalized_overlays):
        cmd.extend(["-loop", "1", "-i", overlay["path"]])

    # Build filter_complex
    filter_parts = []
    current_video_label = "0:v"

    # --- Overlay images onto video ---
    # Each overlay fades in, stays visible, then fades out
    for i, overlay in enumerate(normalized_overlays):
        input_idx = i + (2 if music_track_path else 1)  # offset by video + optional music inputs
        start = overlay["start"]
        duration = overlay["duration"]
        end = start + duration
        position = overlay.get("position", "top_right")
        fade_duration = min(0.4, max(duration / 2.0, 0.08))
        fade_out_start = max(start, end - fade_duration)

        # Determine x/y position for the overlay
        # Overlays are ~256px, video is typically 1080x1920 (portrait TikTok)
        x, y = _get_overlay_xy_for_position(position)

        # Scale the overlay to be clearly visible (~22% of a 1080-wide video = 240px)
        # Add rounded corners effect by using format=rgba and a slight fade
        scale_label = f"scaled{i}"
        filter_parts.append(
            f"[{input_idx}:v]scale=240:240,format=rgba,"
            f"fade=t=in:st={start}:d={fade_duration}:alpha=1,"
            f"fade=t=out:st={fade_out_start}:d={fade_duration}:alpha=1"
            f"[{scale_label}]"
        )

        # Overlay onto the current video stream
        next_label = f"v{i}"
        filter_parts.append(
            f"[{current_video_label}][{scale_label}]overlay={x}:{y}:"
            f"enable='between(t,{start},{end})'[{next_label}]"
        )
        current_video_label = next_label

    # --- Captions (drawtext) applied after overlays ---
    if caption_segments:
        caption_count = 0
        for i, seg in enumerate(caption_segments):
            text = _normalize_caption_text(seg.get("text", ""))
            if not text:
                continue

            start = max(float(seg.get("start", 0.0)), 0.0)
            end = max(float(seg.get("end", start)), start + 0.1)

            # Escape user text so drawtext accepts normal punctuation.
            safe_text = _escape_ffmpeg_drawtext_text(text)

            # If we have a current video label from overlays, apply drawtext to that stream
            # Otherwise apply to the base video
            next_label = f"cap{i}"
            filter_parts.append(
                f"[{current_video_label}]drawtext="
                f"text='{safe_text}':"
                f"fontsize=42:"
                f"fontcolor=white:"
                f"borderw=3:"
                f"bordercolor=black:"
                f"box=1:"
                f"boxcolor=black@0.25:"
                f"boxborderw=16:"
                f"line_spacing=8:"
                f"fix_bounds=true:"
                f"x=(w-text_w)/2:"
                f"y=h-text_h-140:"
                f"enable='between(t,{start},{end})'"
                f"[{next_label}]"
            )
            current_video_label = next_label
            caption_count += 1

        logger.info(f"[Composer] Added {caption_count} caption segments to filter chain")

    # --- Audio mixing ---
    if music_track_path:
        # Mix original audio (at full volume) with background music (very low)
        filter_parts.append(
            f"[0:a]volume={original_audio_volume}[orig_audio]"
        )
        filter_parts.append(
            f"[1:a]volume={music_volume}[music_audio]"
        )
        filter_parts.append(
            f"[orig_audio][music_audio]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = "0:a?"

    # Final video output label
    if current_video_label != "0:v":
        video_map = f"[{current_video_label}]"
    else:
        video_map = "0:v"

    # Assemble filter_complex string
    if filter_parts:
        filter_complex = ";".join(filter_parts)
        cmd.extend(["-filter_complex", filter_complex])
        cmd.extend(["-map", video_map, "-map", audio_map])
    else:
        # No filters needed — just copy
        cmd.extend(["-c:v", "copy"])
        if music_track_path:
            cmd.extend(["-map", "0:v", "-map", audio_map])

    # Output encoding settings
    cmd.extend([
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        output_video_path,
    ])

    return cmd


def _normalize_overlay_files(overlay_files: List[dict]) -> List[dict]:
    """Clamp and sort overlay timing so composition is deterministic."""
    normalized: List[dict] = []
    for overlay in overlay_files:
        start = max(float(overlay.get("start", 0.0)), 0.0)
        duration = max(float(overlay.get("duration", 0.0)), 0.2)
        normalized.append({
            **overlay,
            "start": start,
            "duration": duration,
        })

    normalized.sort(key=lambda item: (item["start"], item.get("path", "")))
    return normalized


def _get_overlay_xy_for_position(position: str) -> tuple:
    """
    Return (x, y) FFmpeg expressions for overlay positioning.
    Assumes 1080x1920 portrait video, 240x240 overlay after scaling.
    Positioned with comfortable padding from edges.
    """
    positions = {
        "top_right":    ("W-w-50", "180"),
        "top_left":     ("50", "180"),
        "center_right": ("W-w-50", "(H-h)/2"),
        "center_left":  ("50", "(H-h)/2"),
        "bottom_right": ("W-w-50", "H-h-250"),
        "bottom_left":  ("50", "H-h-250"),
    }
    return positions.get(position, positions["top_right"])


def _normalize_caption_text(text: str, max_chars_per_line: int = 28) -> str:
    """
    Collapse whitespace and wrap long captions so they stay readable on portrait video.
    """
    collapsed = " ".join(str(text).split())
    if not collapsed:
        return ""

    wrapped_lines = textwrap.wrap(
        collapsed,
        width=max_chars_per_line,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return "\n".join(wrapped_lines)


def _escape_ffmpeg_drawtext_text(text: str) -> str:
    """Escape caption text for FFmpeg drawtext."""
    escaped = text.replace("\\", r"\\")
    replacements = {
        ":": r"\:",
        "'": "\u2019",
        "%": r"\%",
        ",": r"\,",
        ";": r"\;",
        "[": r"\[",
        "]": r"\]",
        "\n": r"\n",
    }
    for source, target in replacements.items():
        escaped = escaped.replace(source, target)
    return escaped


def _prepare_overlay_png_bytes(png_bytes: bytes) -> bytes:
    """
    Preserve the generated popup image as a PNG for FFmpeg input.

    The editor needs overlays to read clearly on busy mobile footage. Keeping
    the image opaque is more reliable than aggressively keying out light
    backgrounds, which can make product shots nearly invisible.
    """
    with Image.open(BytesIO(png_bytes)) as image:
        rgba = image.convert("RGBA")
        output = BytesIO()
        rgba.save(output, format="PNG")
        return output.getvalue()


# ---------------------------------------------------------------------------
# Run composition locally (no sandbox)
# ---------------------------------------------------------------------------

def compose_video_locally(
    input_video_path: str,
    output_video_path: str,
    music_wav_path: Optional[str],
    overlays: List[GeneratedOverlay],
    caption_segments: Optional[List[dict]] = None,
    music_volume: float = 0.15,
) -> CompositionResult:
    """
    Run the full video composition using local FFmpeg.

    This is the fallback when Daytona sandbox is unavailable.
    Writes overlay PNGs to temp files, builds FFmpeg command, runs it.
    """
    logger.info(
        f"[Composer] Starting local composition | "
        f"input={input_video_path} | "
        f"overlays={len(overlays)} | "
        f"has_music={music_wav_path is not None}"
    )

    t0 = time.perf_counter()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write overlay PNGs to temp files
        overlay_files = []
        for i, ov in enumerate(overlays):
            png_path = os.path.join(tmpdir, f"overlay_{i}.png")
            prepared_png = _prepare_overlay_png_bytes(ov.png_bytes)
            with open(png_path, "wb") as f:
                f.write(prepared_png)
            overlay_files.append({
                "path": png_path,
                "start": ov.cue.timestamp,
                "duration": ov.cue.duration,
                "position": ov.cue.overlay_position,
            })
            logger.info(
                f"[Composer] Overlay {i}: '{ov.cue.spoken_text}' at {ov.cue.timestamp}s "
                f"for {ov.cue.duration}s | position={ov.cue.overlay_position} | "
                f"png_size={len(prepared_png)} bytes | path={png_path}"
            )

        # Build and run FFmpeg command
        cmd = build_ffmpeg_compose_command(
            input_video_path=input_video_path,
            output_video_path=output_video_path,
            music_track_path=music_wav_path,
            overlay_files=overlay_files,
            caption_segments=caption_segments,
            music_volume=music_volume,
        )

        logger.info(
            f"[Composer] Running FFmpeg command:\n"
            f"  Full command: {' '.join(cmd)}\n"
            f"  Input: {input_video_path}\n"
            f"  Output: {output_video_path}\n"
            f"  Music: {music_wav_path or 'none'}\n"
            f"  Overlays: {len(overlay_files)}"
        )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout
        )

        if result.returncode != 0:
            logger.error(
                f"[Composer] FFmpeg FAILED (exit code {result.returncode})\n"
                f"  STDERR (first 1000 chars): {result.stderr[:1000]}\n"
                f"  STDOUT: {result.stdout[:500]}\n"
                f"  Command was: {' '.join(cmd)}"
            )
            raise RuntimeError(f"FFmpeg composition failed: {result.stderr[:300]}")

        logger.info(f"[Composer] FFmpeg succeeded | stderr_len={len(result.stderr)}")

    # Get output file info
    file_size = os.path.getsize(output_video_path)
    elapsed = time.perf_counter() - t0

    logger.info(
        f"[Composer] Local composition done in {elapsed:.1f}s | "
        f"output={output_video_path} | "
        f"size={file_size / (1024*1024):.1f}MB"
    )

    return CompositionResult(
        output_path=output_video_path,
        duration_seconds=0,  # could probe with ffprobe
        file_size_bytes=file_size,
        overlays_applied=len(overlays),
        has_background_music=music_wav_path is not None,
    )


# ---------------------------------------------------------------------------
# Run composition inside Daytona sandbox
# ---------------------------------------------------------------------------

async def compose_video_in_sandbox(
    sandbox,
    input_video_sandbox_path: str,
    output_video_sandbox_path: str,
    music_wav_bytes: Optional[bytes],
    overlays: List[GeneratedOverlay],
    caption_segments: Optional[List[dict]] = None,
    music_volume: float = 0.15,
) -> CompositionResult:
    """
    Run the full video composition inside a Daytona sandbox.

    1. Upload overlay PNGs and music WAV into the sandbox
    2. Build FFmpeg command
    3. Execute inside sandbox
    4. Return result (output file stays in sandbox — caller reads it out)
    """
    logger.info(
        f"[Composer] Starting sandbox composition | "
        f"input={input_video_sandbox_path} | "
        f"overlays={len(overlays)} | "
        f"has_music={music_wav_bytes is not None}"
    )

    t0 = time.perf_counter()

    # Upload music WAV to sandbox
    music_sandbox_path = None
    if music_wav_bytes:
        music_sandbox_path = "/tmp/editr_music.wav"
        await sandbox.upload_file(music_wav_bytes, music_sandbox_path)
        logger.info(f"[Composer] Uploaded music to sandbox: {len(music_wav_bytes)} bytes")

    # Upload overlay PNGs to sandbox
    overlay_files = []
    for i, ov in enumerate(overlays):
        png_sandbox_path = f"/tmp/editr_overlay_{i}.png"
        prepared_png = _prepare_overlay_png_bytes(ov.png_bytes)
        await sandbox.upload_file(prepared_png, png_sandbox_path)
        overlay_files.append({
            "path": png_sandbox_path,
            "start": ov.cue.timestamp,
            "duration": ov.cue.duration,
            "position": ov.cue.overlay_position,
        })
        logger.info(
            f"[Composer] Uploaded overlay {i} to sandbox: "
            f"{len(prepared_png)} bytes | noun='{ov.cue.spoken_text}'"
        )

    # Build the FFmpeg command
    cmd = build_ffmpeg_compose_command(
        input_video_path=input_video_sandbox_path,
        output_video_path=output_video_sandbox_path,
        music_track_path=music_sandbox_path,
        overlay_files=overlay_files,
        caption_segments=caption_segments,
        music_volume=music_volume,
    )

    # Run FFmpeg inside the sandbox via shell command
    cmd_str = " ".join(shlex.quote(c) for c in cmd)
    logger.info(f"[Composer] Running FFmpeg in sandbox...")

    result = await sandbox.exec_command(cmd_str, timeout=300)
    if getattr(result, "exit_code", 0) not in (0, None):
        stderr = getattr(result, "stderr", "") or ""
        stdout = getattr(result, "stdout", "") or ""
        raise RuntimeError(
            f"Sandbox FFmpeg composition failed: {(stderr or stdout)[:300]}"
        )

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[Composer] Sandbox composition done in {elapsed:.1f}s | "
        f"output={output_video_sandbox_path}"
    )

    return CompositionResult(
        output_path=output_video_sandbox_path,
        duration_seconds=0,
        file_size_bytes=0,  # caller can check after reading from sandbox
        overlays_applied=len(overlays),
        has_background_music=music_wav_bytes is not None,
    )
