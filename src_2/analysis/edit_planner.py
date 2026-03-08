"""
Editr Edit Planner

Merges the 4 Gemini agent outputs into a single EditPlan
that drives the FFmpeg render pipeline.
"""

import logging
from typing import List

from src_2.analysis.analysis_models import (
    EditMechanics,
    MusicAnalysis,
    TranscriptAnalysis,
    VisualCueAnalysis,
)
from src_2.render.render_models import (
    AudioOp,
    CaptionOp,
    EditPlan,
    PopupOp,
    ZoomOp,
)

logger = logging.getLogger(__name__)


def create_edit_plan(
    video_id: str,
    duration: float,
    transcript: TranscriptAnalysis,
    visual: VisualCueAnalysis,
    music: MusicAnalysis,
    mechanics: EditMechanics,
) -> EditPlan:
    """
    Merge all analysis outputs into a unified EditPlan.
    """
    plan = EditPlan(video_id=video_id, duration=duration)

    # -- Captions from transcript --
    for cap in transcript.captions:
        style = "hook" if cap.start < 3 else ("emphasis" if cap.emphasis else "default")
        plan.captions.append(CaptionOp(
            start=cap.start,
            end=cap.end,
            text=cap.text,
            style=style,
            position="bottom",
        ))

    # -- Zoom ops from visual cues --
    for cue in visual.cues:
        if cue.zoom_suggested:
            target_y = 0.3 if cue.zoom_target == "face" else 0.5
            plan.zooms.append(ZoomOp(
                start=cue.timestamp,
                end=cue.timestamp + 1.5,
                target_x=0.5,
                target_y=target_y,
                zoom_factor=1.4,
            ))

    # -- Zoom ops from edit mechanics --
    for sug in mechanics.suggestions:
        if sug.mechanic_type in ("zoom_in", "zoom_out"):
            factor = 1.5 if sug.mechanic_type == "zoom_in" else 0.8
            plan.zooms.append(ZoomOp(
                start=sug.timestamp,
                end=sug.timestamp + sug.duration,
                target_x=0.5,
                target_y=0.5,
                zoom_factor=factor,
            ))
        elif sug.mechanic_type == "popup":
            plan.popups.append(PopupOp(
                timestamp=sug.timestamp,
                duration=sug.duration,
                image_path="",  # filled by asset_generator
                position="top_right",
            ))

    # -- Music from music analysis --
    if music.replace_music and music.suggestions:
        for sug in music.suggestions:
            plan.audio_ops.append(AudioOp(
                start=sug.start,
                end=sug.end,
                audio_path="",  # filled by asset_generator
                volume=0.4 if music.original_has_music else 0.7,
                fade_in=0.5,
                fade_out=0.5,
            ))

    # Deduplicate overlapping zooms
    plan.zooms = _deduplicate_zooms(plan.zooms)

    logger.info(
        f"[EditPlanner] Plan created for {video_id}: "
        f"{len(plan.captions)} captions, {len(plan.zooms)} zooms, "
        f"{len(plan.popups)} popups, {len(plan.audio_ops)} audio ops"
    )

    return plan


def _deduplicate_zooms(zooms: List[ZoomOp]) -> List[ZoomOp]:
    """Remove overlapping zoom operations, keeping the first."""
    if not zooms:
        return zooms

    sorted_zooms = sorted(zooms, key=lambda z: z.start)
    result = [sorted_zooms[0]]

    for zoom in sorted_zooms[1:]:
        last = result[-1]
        if zoom.start >= last.end:
            result.append(zoom)

    return result
