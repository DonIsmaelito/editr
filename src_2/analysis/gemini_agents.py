"""
Editr Gemini Analysis Agents

4 parallel Gemini 3 Flash calls that analyze a downloaded video:
1. Transcript Agent — extract captions + key moments by WATCHING the video
2. Visual Cue Agent — detect reaction moments, zoom targets from actual frames
3. Music Agent — suggest background music mood/genre based on video content
4. Edit Mechanics Agent — suggest cuts, zooms, popups, pacing fixes

KEY: We pass the actual video as multimodal inline_data so Gemini can WATCH it.
This is way better than the old approach of just sending text descriptions.

Model: gemini-3-flash-preview (cheaper, $0.50/M tokens, supports video input)
Temperature: 1.0 (Gemini 3 docs WARN against lowering — causes looping)
Thinking: "low" level for faster responses
"""

import asyncio
import base64
import json
import logging
import time
from typing import Optional, Tuple

from src_2.config import GEMINI_ANALYSIS_MODEL, GOOGLE_CLOUD_API_KEY
from src_2.analysis.analysis_models import (
    CaptionSegment,
    EditMechanics,
    EditMechanicsSuggestion,
    MusicAnalysis,
    MusicSuggestion,
    TranscriptAnalysis,
    VisualCue,
    VisualCueAnalysis,
)
from src_2.scorer.scorer_models import ScoredVideo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cached Gemini client (created once, reused across calls)
# ---------------------------------------------------------------------------
_cached_genai_client = None


def _create_or_get_cached_genai_client():
    """Create or return the cached google-genai Client singleton."""
    global _cached_genai_client

    if _cached_genai_client is not None:
        return _cached_genai_client

    try:
        from google import genai
    except ImportError:
        raise RuntimeError("google-genai not installed. Run: pip install google-genai")

    if not GOOGLE_CLOUD_API_KEY:
        raise RuntimeError("GOOGLE_CLOUD_API_KEY not configured")

    _cached_genai_client = genai.Client(api_key=GOOGLE_CLOUD_API_KEY)
    logger.info(f"[Gemini] Created genai client | model={GEMINI_ANALYSIS_MODEL}")
    return _cached_genai_client


# ---------------------------------------------------------------------------
# Core Gemini call — supports text-only or multimodal (video + text)
# ---------------------------------------------------------------------------

async def _send_prompt_to_gemini_with_optional_video(
    prompt: str,
    system_instruction: str,
    video_bytes: Optional[bytes] = None,
) -> str:
    """
    Send a prompt to Gemini, optionally with a video file as multimodal input.
    Returns the raw text response (expected to be JSON).

    When video_bytes is provided, we build a multimodal content list:
    [video_part, text_part] so Gemini actually watches the video.

    Uses Gemini 3 Flash (gemini-3-flash-preview) with:
    - temperature=1.0 (Gemini 3 default — docs warn lowering causes looping)
    - thinking_level="low" (fast responses, minimal reasoning overhead)
    - response_mime_type="application/json" (structured output)
    """
    from google.genai import types

    client = _create_or_get_cached_genai_client()

    # Build the contents — either multimodal (video + text) or text-only
    if video_bytes:
        # Multimodal: pass the actual video so Gemini can watch it
        video_size_mb = len(video_bytes) / (1024 * 1024)
        logger.info(
            f"[Gemini] Building multimodal request | "
            f"video_size={video_size_mb:.1f}MB | prompt_len={len(prompt)}"
        )
        contents = [
            types.Part.from_bytes(data=video_bytes, mime_type="video/mp4"),
            types.Part.from_text(text=prompt),
        ]
    else:
        # Text-only fallback (no video available)
        logger.info(f"[Gemini] Building text-only request | prompt_len={len(prompt)}")
        contents = prompt

    # Make the API call (wrapped in to_thread because google-genai SDK is synchronous)
    t0 = time.perf_counter()

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_ANALYSIS_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            temperature=1.0,  # MUST be 1.0 for Gemini 3 — docs warn against lowering
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        ),
    )

    elapsed = time.perf_counter() - t0
    raw_text = response.text
    logger.info(
        f"[Gemini] Response received | elapsed={elapsed:.2f}s | "
        f"response_len={len(raw_text)} | model={GEMINI_ANALYSIS_MODEL}"
    )

    return raw_text


# ---------------------------------------------------------------------------
# Agent 1: Transcript extraction (watches video, extracts captions + hooks)
# ---------------------------------------------------------------------------

async def _extract_transcript_captions_and_hooks_from_video(
    video_path: str,
    scored_video: ScoredVideo,
    sandbox,
    video_bytes: Optional[bytes] = None,
) -> TranscriptAnalysis:
    """
    Watch the video and extract timestamped captions + identify hook moments.
    Returns TranscriptAnalysis with captions, key_moments, hook_timestamp.
    """
    logger.info(f"[Gemini:Transcript] Starting for video {scored_video.video_id}")

    system = (
        "You are a video transcript analyst. Watch the video carefully and extract "
        "a timestamped transcript with captions. Identify the hook moment (first 3s), "
        "emphasis points, and all spoken/displayed text with timestamps. Return JSON."
    )

    # Prompt changes based on whether we have video or just metadata
    if video_bytes:
        prompt = (
            f"Watch this video carefully (duration: {scored_video.duration}s).\n"
            f"Caption/description: {scored_video.desc[:200]}\n\n"
            f"Extract:\n"
            f"1. Full timestamped captions (every spoken word with start/end times)\n"
            f"2. Hook timestamp (the attention-grabbing moment in first 3 seconds)\n"
            f"3. Key moments worth emphasizing (timestamps of impactful points)\n\n"
            f"Return JSON:\n"
            f'{{"captions": [{{"start": 0.0, "end": 2.5, "text": "...", "emphasis": false}}], '
            f'"key_moments": [1.5, 8.0], "hook_timestamp": 0.5}}'
        )
    else:
        # Fallback: text-only prompt when video bytes not available
        prompt = (
            f"Analyze this video (duration: {scored_video.duration}s, "
            f"description: {scored_video.desc[:200]}).\n"
            f"Generate plausible timestamped captions and identify key moments.\n\n"
            f"Return JSON:\n"
            f'{{"captions": [{{"start": 0.0, "end": 2.5, "text": "...", "emphasis": false}}], '
            f'"key_moments": [1.5, 8.0], "hook_timestamp": 0.5}}'
        )

    try:
        raw = await _send_prompt_to_gemini_with_optional_video(prompt, system, video_bytes)

        # Parse the JSON response into our typed model
        data = json.loads(raw)
        result = TranscriptAnalysis(
            captions=[
                CaptionSegment(
                    start=c.get("start", 0),
                    end=c.get("end", 0),
                    text=c.get("text", ""),
                    emphasis=c.get("emphasis", False),
                )
                for c in data.get("captions", [])
            ],
            key_moments=data.get("key_moments", []),
            hook_timestamp=data.get("hook_timestamp", 0),
        )

        logger.info(
            f"[Gemini:Transcript] Done for {scored_video.video_id} | "
            f"{len(result.captions)} captions | "
            f"{len(result.key_moments)} key moments | "
            f"hook_at={result.hook_timestamp}s"
        )
        return result

    except Exception as e:
        logger.error(f"[Gemini:Transcript] FAILED for {scored_video.video_id}: {e}")
        return TranscriptAnalysis()


# ---------------------------------------------------------------------------
# Agent 2: Visual cue detection (watches video, finds zoom-worthy moments)
# ---------------------------------------------------------------------------

async def _detect_visual_cues_and_zoom_targets_in_video(
    video_path: str,
    scored_video: ScoredVideo,
    sandbox,
    video_bytes: Optional[bytes] = None,
) -> VisualCueAnalysis:
    """
    Watch the video and detect visual cues: reactions, transitions, zoom targets.
    Returns VisualCueAnalysis with timestamped cues.
    """
    logger.info(f"[Gemini:Visual] Starting for video {scored_video.video_id}")

    system = (
        "You are a visual analysis agent for short-form video editing. Watch the video "
        "and identify moments that deserve visual emphasis: facial reactions, product reveals, "
        "transitions, text on screen, and zoom-worthy frames. Return JSON."
    )

    if video_bytes:
        prompt = (
            f"Watch this video carefully (duration: {scored_video.duration}s).\n"
            f"Caption: {scored_video.desc[:200]}\n\n"
            f"Identify visual cue points with exact timestamps:\n"
            f"1. Reaction moments (surprised face, laugh, excitement)\n"
            f"2. Product/object reveals or close-ups\n"
            f"3. Text appearing on screen\n"
            f"4. Good transition points\n"
            f"5. Suggest zoom targets (face, object, text)\n\n"
            f"Return JSON:\n"
            f'{{"cues": [{{"timestamp": 5.0, "cue_type": "reaction", '
            f'"description": "speaker laughs", "zoom_suggested": true, "zoom_target": "face"}}]}}'
        )
    else:
        prompt = (
            f"Analyze this video (duration: {scored_video.duration}s, "
            f"description: {scored_video.desc[:200]}).\n"
            f"Suggest plausible visual cue points for editing.\n\n"
            f"Return JSON:\n"
            f'{{"cues": [{{"timestamp": 5.0, "cue_type": "reaction", '
            f'"description": "...", "zoom_suggested": true, "zoom_target": "face"}}]}}'
        )

    try:
        raw = await _send_prompt_to_gemini_with_optional_video(prompt, system, video_bytes)
        data = json.loads(raw)

        result = VisualCueAnalysis(
            cues=[
                VisualCue(
                    timestamp=c.get("timestamp", 0),
                    cue_type=c.get("cue_type", "highlight"),
                    description=c.get("description", ""),
                    zoom_suggested=c.get("zoom_suggested", False),
                    zoom_target=c.get("zoom_target", ""),
                )
                for c in data.get("cues", [])
            ]
        )

        logger.info(
            f"[Gemini:Visual] Done for {scored_video.video_id} | "
            f"{len(result.cues)} cues found | "
            f"zoom_suggested={sum(1 for c in result.cues if c.zoom_suggested)}"
        )
        return result

    except Exception as e:
        logger.error(f"[Gemini:Visual] FAILED for {scored_video.video_id}: {e}")
        return VisualCueAnalysis()


# ---------------------------------------------------------------------------
# Agent 3: Music suggestion (analyzes video mood and suggests music params)
# ---------------------------------------------------------------------------

async def _suggest_background_music_genre_and_mood(
    video_path: str,
    scored_video: ScoredVideo,
    sandbox,
    video_bytes: Optional[bytes] = None,
) -> MusicAnalysis:
    """
    Analyze the video's content/mood and suggest ideal background music.
    Returns MusicAnalysis with genre, mood, BPM, and Lyria prompt.
    """
    logger.info(f"[Gemini:Music] Starting for video {scored_video.video_id}")

    system = (
        "You are a music director for short-form video. Analyze the video's content, "
        "mood, and pacing to suggest ideal background music parameters. "
        "Consider the existing audio and whether it should be replaced. Return JSON."
    )

    if video_bytes:
        prompt = (
            f"Watch this video (duration: {scored_video.duration}s).\n"
            f"Caption: {scored_video.desc[:200]}\n"
            f"Current music/audio: '{scored_video.music_title}'\n\n"
            f"Suggest background music:\n"
            f"1. Should we replace or layer over the original audio?\n"
            f"2. What genre/mood/BPM matches the video's energy?\n"
            f"3. Write a Lyria-compatible music generation prompt.\n\n"
            f"Return JSON:\n"
            f'{{"suggestions": [{{"genre": "lo-fi", "mood": "chill", "bpm": 90, '
            f'"prompt": "chill lo-fi beat with soft drums", "start": 0, "end": 30}}], '
            f'"original_has_music": true, "replace_music": false}}'
        )
    else:
        prompt = (
            f"Video info: duration={scored_video.duration}s, "
            f"description={scored_video.desc[:200]}, "
            f"current music='{scored_video.music_title}'.\n"
            f"Suggest background music.\n\n"
            f"Return JSON:\n"
            f'{{"suggestions": [{{"genre": "lo-fi", "mood": "chill", "bpm": 90, '
            f'"prompt": "...", "start": 0, "end": 30}}], '
            f'"original_has_music": true, "replace_music": false}}'
        )

    try:
        raw = await _send_prompt_to_gemini_with_optional_video(prompt, system, video_bytes)
        data = json.loads(raw)

        result = MusicAnalysis(
            suggestions=[
                MusicSuggestion(
                    genre=s.get("genre", ""),
                    mood=s.get("mood", ""),
                    bpm=s.get("bpm", 120),
                    prompt=s.get("prompt", ""),
                    start=s.get("start", 0),
                    end=s.get("end", 30),
                )
                for s in data.get("suggestions", [])
            ],
            original_has_music=data.get("original_has_music", True),
            replace_music=data.get("replace_music", False),
        )

        logger.info(
            f"[Gemini:Music] Done for {scored_video.video_id} | "
            f"{len(result.suggestions)} suggestions | "
            f"replace_music={result.replace_music}"
        )
        return result

    except Exception as e:
        logger.error(f"[Gemini:Music] FAILED for {scored_video.video_id}: {e}")
        return MusicAnalysis()


# ---------------------------------------------------------------------------
# Agent 4: Edit mechanics (watches video, suggests specific edits)
# ---------------------------------------------------------------------------

async def _suggest_cuts_zooms_popups_and_pacing_fixes(
    video_path: str,
    scored_video: ScoredVideo,
    sandbox,
    video_bytes: Optional[bytes] = None,
) -> EditMechanics:
    """
    Watch the video and suggest specific edit improvements with timestamps.
    Returns EditMechanics with cut/zoom/popup suggestions and pacing score.
    """
    logger.info(f"[Gemini:EditMech] Starting for video {scored_video.video_id}")

    system = (
        "You are a professional video editor specializing in viral short-form content "
        "(TikTok, Reels, Shorts). Watch the video and suggest specific edit improvements "
        "with exact timestamps. Focus on making the video more engaging and viral. Return JSON."
    )

    if video_bytes:
        prompt = (
            f"Watch this video carefully (duration: {scored_video.duration}s).\n"
            f"Caption: {scored_video.desc[:200]}\n\n"
            f"This video underperformed. Suggest specific edit improvements:\n"
            f"1. Where to add jump cuts to remove dead air\n"
            f"2. Where to add zoom-ins/outs for emphasis\n"
            f"3. Where to add popup graphics/emoji\n"
            f"4. Where to add text overlays\n"
            f"5. Rate the overall pacing quality (0.0 = terrible, 1.0 = perfect)\n\n"
            f"Return JSON:\n"
            f'{{"suggestions": [{{"timestamp": 3.0, "mechanic_type": "zoom_in", '
            f'"description": "zoom into face during punchline", "duration": 1.5}}], '
            f'"pacing_score": 0.4}}'
        )
    else:
        prompt = (
            f"Video info: duration={scored_video.duration}s, "
            f"description={scored_video.desc[:200]}.\n"
            f"Suggest edit improvements.\n\n"
            f"Return JSON:\n"
            f'{{"suggestions": [{{"timestamp": 3.0, "mechanic_type": "zoom_in", '
            f'"description": "...", "duration": 1.5}}], "pacing_score": 0.4}}'
        )

    try:
        raw = await _send_prompt_to_gemini_with_optional_video(prompt, system, video_bytes)
        data = json.loads(raw)

        result = EditMechanics(
            suggestions=[
                EditMechanicsSuggestion(
                    timestamp=s.get("timestamp", 0),
                    mechanic_type=s.get("mechanic_type", ""),
                    description=s.get("description", ""),
                    duration=s.get("duration", 1.0),
                )
                for s in data.get("suggestions", [])
            ],
            pacing_score=data.get("pacing_score", 0.5),
        )

        logger.info(
            f"[Gemini:EditMech] Done for {scored_video.video_id} | "
            f"{len(result.suggestions)} suggestions | "
            f"pacing_score={result.pacing_score}"
        )
        return result

    except Exception as e:
        logger.error(f"[Gemini:EditMech] FAILED for {scored_video.video_id}: {e}")
        return EditMechanics()


# ---------------------------------------------------------------------------
# Main entry point: run all 4 agents in parallel on the same video
# ---------------------------------------------------------------------------

async def run_all_four_gemini_agents_in_parallel(
    video_path: str,
    scored_video: ScoredVideo,
    sandbox,
) -> Tuple[TranscriptAnalysis, VisualCueAnalysis, MusicAnalysis, EditMechanics]:
    """
    Read the video from the sandbox ONCE, then run all 4 Gemini analysis
    agents in parallel, each receiving the same video bytes.

    Returns (transcript, visual_cues, music, edit_mechanics).
    """
    t0 = time.perf_counter()
    logger.info(
        f"[Gemini] Starting 4 parallel agents for video {scored_video.video_id} | "
        f"model={GEMINI_ANALYSIS_MODEL}"
    )

    # Read the video file from the sandbox as base64, then decode to raw bytes.
    # We do this ONCE and pass the same bytes to all 4 agents to avoid
    # reading the same file 4 times from the sandbox.
    video_bytes: Optional[bytes] = None
    try:
        t_read = time.perf_counter()
        video_b64 = await sandbox.read_file_b64(video_path)
        video_bytes = base64.b64decode(video_b64)
        read_elapsed = time.perf_counter() - t_read
        video_size_mb = len(video_bytes) / (1024 * 1024)
        logger.info(
            f"[Gemini] Video read from sandbox in {read_elapsed:.2f}s | "
            f"size={video_size_mb:.1f}MB | path={video_path}"
        )
    except Exception as e:
        # If we can't read the video, fall back to text-only analysis
        logger.warning(
            f"[Gemini] Could not read video from sandbox: {e} | "
            f"Falling back to text-only analysis"
        )
        video_bytes = None

    # Launch all 4 agents in parallel with the same video bytes
    results = await asyncio.gather(
        _extract_transcript_captions_and_hooks_from_video(
            video_path, scored_video, sandbox, video_bytes
        ),
        _detect_visual_cues_and_zoom_targets_in_video(
            video_path, scored_video, sandbox, video_bytes
        ),
        _suggest_background_music_genre_and_mood(
            video_path, scored_video, sandbox, video_bytes
        ),
        _suggest_cuts_zooms_popups_and_pacing_fixes(
            video_path, scored_video, sandbox, video_bytes
        ),
    )

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[Gemini] All 4 agents complete in {elapsed:.2f}s | "
        f"video={scored_video.video_id} | "
        f"multimodal={'yes' if video_bytes else 'no (text fallback)'}"
    )

    return results
