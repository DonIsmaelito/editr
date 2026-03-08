"""
Test Script: Run the full editing pipeline on a single downloaded video.

This runs LOCALLY (no Daytona sandbox) so you can test the Gemini + Lyria + NB2
pipeline end-to-end on a video file from the downloads/ folder.

Usage:
    python scripts/test_edit_single_video.py downloads/malharpandy/7577186287996783927_1000views.mp4

    # Or with duration hint (speeds up Gemini analysis):
    python scripts/test_edit_single_video.py downloads/malharpandy/7577186287996783927_1000views.mp4 22

Outputs:
    {input_name}_edited.mp4 in the same directory as the input

Requires:
    - GOOGLE_CLOUD_API_KEY in .env (for Gemini, Lyria, Nano Banana)
    - ffmpeg installed locally
    - pip install pydub librosa (for music loop extension)
"""

import asyncio
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()


async def edit_video(input_path: str, duration_hint: float = 0.0):
    # Import here so dotenv is loaded first
    from src_2.editor.editor_pipeline import run_editor_pipeline_for_single_video_locally

    if not os.path.exists(input_path):
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    # Build output path
    base, ext = os.path.splitext(input_path)
    output_path = f"{base}_edited{ext}"

    file_size_mb = os.path.getsize(input_path) / (1024 * 1024)

    print(f"\n{'='*60}")
    print(f"  EDITR — Single Video Edit Test (Local Mode)")
    print(f"  Input:  {input_path} ({file_size_mb:.1f}MB)")
    print(f"  Output: {output_path}")
    print(f"  Duration hint: {duration_hint}s")
    print(f"{'='*60}\n")

    async def on_progress(event_type, data):
        step = data.get("step", "?")
        status = data.get("status", "?")
        detail = data.get("detail", "")
        print(f"  [{step}] {status} {detail}")

    t0 = time.perf_counter()

    result = await run_editor_pipeline_for_single_video_locally(
        video_path=input_path,
        output_path=output_path,
        video_duration_hint=duration_hint,
        on_progress=on_progress,
    )

    elapsed = time.perf_counter() - t0

    print(f"\n{'='*60}")
    print(f"  RESULT")
    print(f"  Time: {elapsed:.1f}s")

    if result.skipped:
        print(f"  SKIPPED: {result.skip_reason}")
    else:
        print(f"  Output: {result.output_path}")
        if result.output_bytes:
            print(f"  Size: {len(result.output_bytes)/(1024*1024):.1f}MB")
        print(f"  Overlays applied: {result.overlays_applied}")
        print(f"  Has background music: {result.has_music}")

    print(f"\n  VIDEO UNDERSTANDING:")
    print(f"  Summary: {result.understanding.video_summary}")
    print(f"  Transcript segments: {len(result.understanding.transcript)}")
    print(f"  Cue moments: {len(result.understanding.cue_moments)}")
    for cm in result.understanding.cue_moments:
        print(f"    @{cm.timestamp:.1f}s — '{cm.spoken_text}' ({cm.noun_type})")
    print(f"  Has existing captions: {result.understanding.has_existing_captions}")
    print(f"  Has existing effects: {result.understanding.has_existing_effects}")
    if result.understanding.music_mood:
        print(f"  Music mood: {result.understanding.music_mood.prompt[:80]}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_edit_single_video.py <video.mp4> [duration_seconds]")
        sys.exit(1)

    input_path = sys.argv[1]
    duration_hint = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0

    asyncio.run(edit_video(input_path, duration_hint))
