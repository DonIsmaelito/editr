"""
Editr FFmpeg Renderer

Builds and executes a single-pass FFmpeg command inside the sandbox
that applies captions, zooms, popups, and audio mixing.
"""

import json
import logging
import time
from typing import List

from src_2.render.render_models import EditPlan, CaptionOp, ZoomOp, PopupOp, AudioOp
from src_2.sandbox.scripts import FFMPEG_RENDER_TEMPLATE

logger = logging.getLogger(__name__)


def _build_caption_drawtext(captions: List[CaptionOp], width: int = 1080) -> str:
    """Build FFmpeg drawtext filter chain for captions."""
    if not captions:
        return ""

    filters = []
    for cap in captions:
        # Escape special characters for FFmpeg
        text = cap.text.replace("'", "\\'").replace(":", "\\:")
        text = text.replace("\\", "\\\\")

        fontsize = 48 if cap.style == "hook" else (44 if cap.style == "emphasis" else 36)
        fontcolor = "white"
        borderw = 3

        y_pos = "h-120" if cap.position == "bottom" else (
            "h/2-30" if cap.position == "center" else "80"
        )

        filters.append(
            f"drawtext=text='{text}'"
            f":fontsize={fontsize}"
            f":fontcolor={fontcolor}"
            f":borderw={borderw}"
            f":bordercolor=black"
            f":x=(w-text_w)/2"
            f":y={y_pos}"
            f":enable='between(t,{cap.start},{cap.end})'"
        )

    return ",".join(filters)


def _build_zoom_filter(zooms: List[ZoomOp], width: int = 1080, height: int = 1920) -> str:
    """Build FFmpeg zoompan filter for zoom effects."""
    if not zooms:
        return ""

    # For simplicity, we apply zooms via crop+scale rather than zoompan
    # (zoompan has frame-by-frame control but is complex)
    filters = []
    for zoom in zooms:
        # Crop to zoom region and scale back up
        crop_w = int(width / zoom.zoom_factor)
        crop_h = int(height / zoom.zoom_factor)
        crop_x = int((width - crop_w) * zoom.target_x)
        crop_y = int((height - crop_h) * zoom.target_y)

        filters.append(
            f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
            f":enable='between(t,{zoom.start},{zoom.end})',"
            f"scale={width}:{height}"
            f":enable='between(t,{zoom.start},{zoom.end})'"
        )

    return ",".join(filters)


def _build_filter_complex(plan: EditPlan) -> str:
    """Build the complete FFmpeg filter_complex string."""
    video_filters = []
    audio_filters = []

    # Caption overlay
    caption_filter = _build_caption_drawtext(plan.captions)
    if caption_filter:
        video_filters.append(caption_filter)

    # Build video chain
    if video_filters:
        v_chain = f"[0:v]{','.join(video_filters)}[vout]"
    else:
        v_chain = "[0:v]copy[vout]"

    # Audio mixing
    if plan.audio_ops:
        # Mix original audio with generated music
        audio_parts = ["[0:a]"]
        for i in range(len(plan.audio_ops)):
            audio_parts.append(f"[{i+1}:a]")

        a_chain = (
            f"{''.join(audio_parts)}"
            f"amix=inputs={len(audio_parts)}:duration=first:dropout_transition=2[aout]"
        )
    else:
        a_chain = "[0:a]acopy[aout]"

    return f"{v_chain};{a_chain}"


async def render_video(plan: EditPlan, input_path: str, sandbox) -> str:
    """
    Render the edited video inside the sandbox using FFmpeg.
    Returns the output path inside the sandbox.
    """
    t0 = time.perf_counter()
    output_path = f"/tmp/{plan.video_id}_edited.mp4"

    filter_complex = _build_filter_complex(plan)
    audio_inputs = [op.audio_path for op in plan.audio_ops if op.audio_path]

    script = FFMPEG_RENDER_TEMPLATE.format(
        input_path=input_path,
        output_path=output_path,
        filter_complex=filter_complex,
        audio_inputs=json.dumps(audio_inputs),
    )

    result = await sandbox.exec_script(script, timeout=300)

    elapsed = time.perf_counter() - t0
    logger.info(f"[FFmpeg] Render completed in {elapsed:.2f}s -> {output_path}")

    return output_path
