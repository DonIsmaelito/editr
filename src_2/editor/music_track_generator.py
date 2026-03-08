"""
Editr — Music Track Generator via Lyria RealTime (WebSocket)

Lyria RealTime uses a WebSocket streaming session, NOT the standard
generateContent REST endpoint. The flow is:

  1. Connect: client.aio.live.music.connect(model='lyria-realtime-exp')
  2. Configure: session.set_music_generation_config(bpm, density, brightness, etc.)
  3. Set prompts: session.set_weighted_prompts([{text: "...", weight: 1.0}])
  4. Play: session.play()
  5. Receive: async for message in session.receive() → audio chunks (raw PCM)
  6. Stop after target duration
  7. Close session

Output is raw 16-bit PCM at 48kHz stereo. We wrap it in a WAV header
and optionally loop-extend if shorter than the video.
"""

import asyncio
import base64
import logging
import math
import re
import struct
import time
from typing import Optional

from src_2.config import GOOGLE_CLOUD_API_KEY, LYRIA_MODEL

logger = logging.getLogger(__name__)

LYRIA_TIMEOUT_BUFFER_SECONDS = 20
LYRIA_MIN_TIMEOUT_SECONDS = 45
LYRIA_PROMPT_WORD_LIMIT = 18


def _build_lyria_client():
    """Create a Gemini Developer API client on the v1alpha surface required by Lyria."""
    try:
        from google import genai
    except ImportError:
        raise RuntimeError("google-genai not installed")

    if not GOOGLE_CLOUD_API_KEY:
        raise RuntimeError("GOOGLE_CLOUD_API_KEY not configured")

    return genai.Client(
        api_key=GOOGLE_CLOUD_API_KEY,
        http_options={"api_version": "v1alpha"},
    )


def _compact_music_prompt(prompt: str, energy: Optional[str] = None) -> str:
    """Keep the Lyria prompt short and stable so generation starts faster."""
    normalized = " ".join((prompt or "").split())
    normalized = re.sub(
        r"\b(background music|soundtrack|instrumental track|background track)\b",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = normalized.strip(" ,.;:")
    compact_subject = " ".join(normalized.split()[:LYRIA_PROMPT_WORD_LIMIT]).lower()
    energy_hint = (energy or "low").strip().lower()
    if energy_hint not in {"low", "medium", "high"}:
        energy_hint = "low"

    if not compact_subject:
        compact_subject = "ambient lo-fi texture"

    return (
        "soft instrumental background bed, no vocals, no lyrics, "
        f"{energy_hint} energy, unobtrusive, simple arrangement. {compact_subject}"
    ).strip()


def _build_music_generation_config(bpm: int, energy: Optional[str] = None):
    """Build a slightly lighter config to favor faster background-bed generations."""
    from google.genai import types

    energy_hint = (energy or "low").strip().lower()
    if energy_hint == "high":
        temperature = 0.72
        density = 0.34
        brightness = 0.48
    elif energy_hint == "medium":
        temperature = 0.68
        density = 0.28
        brightness = 0.42
    else:
        temperature = 0.62
        density = 0.22
        brightness = 0.34

    # Keep the config on fields confirmed by the installed SDK. Newer docs mention
    # music_generation_mode, but the local SDK version rejects it.
    return types.LiveMusicGenerationConfig(
        bpm=max(60, min(200, bpm)),
        temperature=temperature,
        guidance=2.5,
        density=density,
        brightness=brightness,
    )


async def generate_music_track_via_lyria(
    prompt: str,
    target_duration_seconds: float,
    bpm: int = 75,
    energy: Optional[str] = None,
) -> Optional[bytes]:
    """
    Generate a background music track using Lyria RealTime WebSocket API.

    Connects to Lyria, streams audio chunks for target_duration_seconds,
    collects all PCM data, wraps in WAV header, returns WAV bytes.

    Returns None if generation fails (non-fatal for the pipeline).
    """
    try:
        from google.genai import types
    except ImportError:
        raise RuntimeError("google-genai not installed")

    client = _build_lyria_client()

    target_duration_seconds = max(4.0, float(target_duration_seconds))
    lyria_prompt = _compact_music_prompt(prompt, energy=energy)

    logger.info(
        f"[MusicGen] Connecting to Lyria WebSocket | model={LYRIA_MODEL} | "
        f"target={target_duration_seconds:.0f}s | bpm={bpm} | "
        f"energy={energy or 'low'} | "
        f"prompt_len={len(lyria_prompt)} | "
        f"prompt='{lyria_prompt[:80]}...'"
    )

    t0 = time.perf_counter()
    all_pcm_chunks: list[bytes] = []
    # 48kHz stereo 16-bit = 192000 bytes per second
    target_bytes = int(target_duration_seconds * 192000)
    total_timeout_seconds = max(
        LYRIA_MIN_TIMEOUT_SECONDS,
        int(target_duration_seconds) + LYRIA_TIMEOUT_BUFFER_SECONDS,
    )

    try:
        async with asyncio.timeout(total_timeout_seconds):
            # Connect to Lyria via WebSocket
            async with client.aio.live.music.connect(model=f"models/{LYRIA_MODEL}") as session:
                logger.info(
                    f"[MusicGen] WebSocket connected | api_version=v1alpha | "
                    f"timeout={total_timeout_seconds}s"
                )

                # Configure the generation parameters
                config = _build_music_generation_config(bpm, energy=energy)
                logger.info(
                    f"[MusicGen] Sending music config | bpm={config.bpm} | "
                    f"guidance={config.guidance} | density={config.density} | "
                    f"brightness={config.brightness} | temperature={config.temperature}"
                )
                await session.set_music_generation_config(config)

                # Set the weighted prompts (what kind of music to generate)
                await session.set_weighted_prompts([
                    types.WeightedPrompt(text=lyria_prompt, weight=1.0),
                ])
                logger.info("[MusicGen] Weighted prompt sent")

                # Start playback (begins generating audio)
                await session.play()
                logger.info("[MusicGen] Playback started, receiving audio chunks...")

                collected_bytes = 0
                chunk_count = 0

                # Receive audio chunks until we have enough for the target duration
                async for message in session.receive():
                    # Check if we got audio content
                    if message.server_content and message.server_content.audio_chunks:
                        for chunk in message.server_content.audio_chunks:
                            if chunk.data:
                                # chunk.data is raw PCM bytes
                                pcm_data = chunk.data
                                if isinstance(pcm_data, str):
                                    pcm_data = base64.b64decode(pcm_data)
                                all_pcm_chunks.append(pcm_data)
                                collected_bytes += len(pcm_data)
                                chunk_count += 1

                    # Check if we have enough audio
                    if collected_bytes >= target_bytes:
                        logger.info(
                            f"[MusicGen] Reached target duration | "
                            f"chunks={chunk_count} | collected={collected_bytes} bytes | "
                            f"target={target_bytes} bytes"
                        )
                        break

                    # Check for filtered prompts (content policy)
                    if message.filtered_prompt:
                        logger.warning(
                            f"[MusicGen] Prompt was filtered by content policy: "
                            f"{message.filtered_prompt}"
                        )

                # Stop the session
                await session.stop()
                logger.info(
                    f"[MusicGen] Session stopped cleanly | chunks={chunk_count} | "
                    f"collected={collected_bytes} bytes"
                )
    except TimeoutError:
        logger.error(
            f"[MusicGen] Lyria timed out after {total_timeout_seconds}s | "
            f"target={target_duration_seconds:.0f}s"
        )
        return None
    except Exception as e:
        logger.error(f"[MusicGen] Lyria WebSocket session failed: {e}", exc_info=True)
        return None

    elapsed = time.perf_counter() - t0

    if not all_pcm_chunks:
        logger.warning(f"[MusicGen] No audio chunks received after {elapsed:.1f}s")
        return None

    # Concatenate all PCM chunks
    raw_pcm = b"".join(all_pcm_chunks)
    # Trim to exact target duration
    raw_pcm = raw_pcm[:target_bytes]

    pcm_duration = len(raw_pcm) / 192000
    logger.info(
        f"[MusicGen] Lyria generated {pcm_duration:.1f}s of audio in {elapsed:.1f}s | "
        f"pcm_size={len(raw_pcm)} bytes"
    )

    # Wrap raw PCM in WAV header
    wav_bytes = _convert_raw_pcm_to_wav(raw_pcm)
    return wav_bytes


def _convert_raw_pcm_to_wav(pcm_data: bytes, sample_rate=48000, channels=2, bits=16) -> bytes:
    """Wrap raw PCM in a 44-byte WAV header."""
    data_size = len(pcm_data)
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8

    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE',
        b'fmt ', 16, 1, channels, sample_rate,
        byte_rate, block_align, bits,
        b'data', data_size,
    )
    return header + pcm_data


def extend_track_to_match_video_duration(
    wav_bytes: bytes,
    video_duration_seconds: float,
    crossfade_ms: int = 220,
    fade_out_ms: int = 300,
) -> bytes:
    """
    If the WAV is shorter than the video, loop-extend it with
    beat-matched crossfades so it sounds natural.

    Tries librosa beat detection first for smart seam finding,
    falls back to naive crossfade loop if that fails.
    """
    from pydub import AudioSegment
    import io

    audio = AudioSegment.from_file(io.BytesIO(wav_bytes), format="wav")
    audio_dur = len(audio) / 1000.0

    logger.info(
        f"[MusicGen] Track extension check | "
        f"track={audio_dur:.1f}s | video={video_duration_seconds:.1f}s"
    )

    # Already long enough — just trim + fade out
    if audio_dur >= video_duration_seconds:
        final = audio[:int(video_duration_seconds * 1000)]
        final = final.fade_out(min(fade_out_ms, len(final)))
        buf = io.BytesIO()
        final.export(buf, format="wav")
        return buf.getvalue()

    # Need to extend — try beat-matched looping, fall back to naive
    try:
        final = _beat_matched_loop_extension(
            audio, video_duration_seconds, crossfade_ms, fade_out_ms
        )
    except Exception as e:
        logger.warning(f"[MusicGen] Beat-matched loop failed: {e} — falling back to naive loop")
        final = _naive_crossfade_loop(audio, video_duration_seconds, crossfade_ms, fade_out_ms)

    buf = io.BytesIO()
    final.export(buf, format="wav")
    result_bytes = buf.getvalue()

    logger.info(
        f"[MusicGen] Track extended | "
        f"original={audio_dur:.1f}s | "
        f"extended={len(final)/1000:.1f}s | "
        f"target={video_duration_seconds:.1f}s"
    )

    return result_bytes


def _naive_crossfade_loop(audio, target_sec, crossfade_ms, fade_out_ms):
    """Simple loop: repeat the track with crossfades until long enough."""
    reps = math.ceil(target_sec / (len(audio) / 1000)) + 1
    final = audio
    for _ in range(reps - 1):
        cf = min(crossfade_ms, len(audio) // 10)
        final = final.append(audio, crossfade=cf)
    final = final[:int(target_sec * 1000)]
    final = final.fade_out(min(fade_out_ms, len(final)))
    return final


def _beat_matched_loop_extension(audio, target_sec, crossfade_ms, fade_out_ms):
    """
    Use librosa beat detection to find the best seam point for looping.
    Scores candidate seams by chroma similarity, RMS, and spectral centroid.
    Falls back to naive loop if not enough beats are found.
    """
    import io
    import numpy as np
    import librosa

    # Export to WAV for librosa analysis
    buf = io.BytesIO()
    audio.export(buf, format="wav")
    buf.seek(0)
    y, sr = librosa.load(buf, sr=22050, mono=True)

    # Find beats
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="time")
    beats = np.array(beat_frames) if not isinstance(beat_frames, np.ndarray) else beat_frames

    audio_dur = len(audio) / 1000.0

    # Not enough beats — fall back to naive loop
    if len(beats) < 8:
        return _naive_crossfade_loop(audio, target_sec, crossfade_ms, fade_out_ms)

    # Find best seam: early beat (20-70%) paired with late beat (65-95%)
    early_beats = [t for t in beats if 0.20 * audio_dur <= t <= 0.70 * audio_dur]
    late_beats = [t for t in beats if 0.65 * audio_dur <= t <= 0.95 * audio_dur]

    best_seam = None
    best_score = float("inf")

    for i, early in enumerate(early_beats):
        for j, late in enumerate(late_beats):
            if late <= early + 1.0:
                continue
            # Prefer beats at the same bar phase (every 4th beat)
            if (i % 4) != (j % 4):
                continue
            score = _compute_seam_similarity_score(y, sr, early, late)
            if score < best_score:
                best_score = score
                best_seam = (early, late)

    if best_seam is None:
        return _naive_crossfade_loop(audio, target_sec, crossfade_ms, fade_out_ms)

    # Build the looped track using the best seam
    early_sec, late_sec = best_seam
    early_ms = int(early_sec * 1000)
    late_ms = int(late_sec * 1000)

    # Play from start to late_sec, then loop from early_sec to late_sec
    prefix = audio[:late_ms]
    loop_segment = audio[early_ms:late_ms]

    final = prefix
    while len(final) < int(target_sec * 1000) + crossfade_ms:
        cf = min(crossfade_ms, len(loop_segment) // 8)
        final = final.append(loop_segment, crossfade=cf)

    final = final[:int(target_sec * 1000)]
    final = final.fade_out(min(fade_out_ms, len(final)))

    logger.info(
        f"[MusicGen] Beat-matched seam found at {early_sec:.1f}s→{late_sec:.1f}s | "
        f"score={best_score:.3f}"
    )

    return final


def _compute_seam_similarity_score(y, sr, a_sec, b_sec, win_sec=0.35):
    """Score how similar two points in the audio are (lower = more similar).
    Uses chroma, RMS loudness, and spectral centroid."""
    import numpy as np
    import librosa

    n = int(win_sec * sr)
    a = int(a_sec * sr)
    b = int(b_sec * sr)

    A = y[max(0, a - n // 2): a + n // 2]
    B = y[max(0, b - n // 2): b + n // 2]
    if len(A) < n or len(B) < n:
        return float("inf")

    chroma_A = librosa.feature.chroma_stft(y=A, sr=sr).mean(axis=1)
    chroma_B = librosa.feature.chroma_stft(y=B, sr=sr).mean(axis=1)
    rms_A = float(librosa.feature.rms(y=A).mean())
    rms_B = float(librosa.feature.rms(y=B).mean())
    cent_A = float(librosa.feature.spectral_centroid(y=A, sr=sr).mean())
    cent_B = float(librosa.feature.spectral_centroid(y=B, sr=sr).mean())

    return (
        np.linalg.norm(chroma_A - chroma_B)
        + 0.75 * abs(rms_A - rms_B)
        + 0.001 * abs(cent_A - cent_B)
    )
