"""
Findr Segment Processor

Takes a raw transcript and:
1. Consolidates word-level segments into sentence-level (5s max per chunk)
2. Splits into 5-minute macro-segments for vector embedding
3. Generates OpenAI embeddings for each macro-segment
4. Prepares data for Convex vector storage

The 5-minute segments are the unit of vector search. After similarity
filtering narrows to 1-2 segments, those segments are fed to the LLM
moment finder for exact timestamp extraction.
"""

import logging
import time
from typing import Any, Dict, List

from openai import AsyncOpenAI

from src.config import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    OPENAI_API_KEY,
    SEGMENT_DURATION_SEC,
)
from src.models.schemas import EmbeddedSegment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Consolidate word-level → sentence-level segments
# ---------------------------------------------------------------------------

def consolidate_segments(
    segments: List[Dict[str, Any]],
    max_duration: float = 5.0,
) -> List[Dict[str, Any]]:
    """
    Merge fine-grained transcript segments into ~5-second chunks.
    Preserves all text (lossless), just groups for readability.

    Reuses Clippa's proven consolidation logic from SafeBatchProcessor.
    """
    if not segments:
        return []

    consolidated = []
    current_text_parts: List[str] = []
    current_start = segments[0].get("start", 0)
    current_end = segments[0].get("end", 0)

    for seg in segments:
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", seg_start)
        seg_text = seg.get("text", "").strip()

        if not seg_text:
            continue

        span = seg_end - current_start

        if span > max_duration and current_text_parts:
            # Flush current group
            consolidated.append({
                "text": " ".join(current_text_parts),
                "start": round(current_start, 3),
                "end": round(current_end, 3),
                "duration": round(current_end - current_start, 3),
            })
            current_text_parts = [seg_text]
            current_start = seg_start
            current_end = seg_end
        else:
            current_text_parts.append(seg_text)
            current_end = seg_end

    # Flush remainder
    if current_text_parts:
        consolidated.append({
            "text": " ".join(current_text_parts),
            "start": round(current_start, 3),
            "end": round(current_end, 3),
            "duration": round(current_end - current_start, 3),
        })

    logger.info(
        f"[Segments] Consolidated {len(segments)} → {len(consolidated)} segments"
    )
    return consolidated


# ---------------------------------------------------------------------------
# 2. Split into 5-minute macro-segments
# ---------------------------------------------------------------------------

def split_into_macro_segments(
    segments: List[Dict[str, Any]],
    video_id: str,
    segment_duration: int = SEGMENT_DURATION_SEC,
) -> List[EmbeddedSegment]:
    """
    Group consolidated segments into 5-minute chunks for vector embedding.

    Each macro-segment gets all transcript text within its time window.
    These are the units that get embedded and similarity-searched.
    """
    if not segments:
        return []

    # Determine total duration
    total_end = max(s.get("end", 0) for s in segments)
    num_macro = max(1, int(total_end // segment_duration) + 1)

    # Bucket segments into macro-segments by start time
    buckets: Dict[int, List[str]] = {i: [] for i in range(num_macro)}
    bucket_times: Dict[int, Dict[str, float]] = {
        i: {"start": i * segment_duration, "end": min((i + 1) * segment_duration, total_end)}
        for i in range(num_macro)
    }

    for seg in segments:
        bucket_idx = min(int(seg["start"] // segment_duration), num_macro - 1)
        # Include timestamp markers so the moment finder LLM can pinpoint
        # exact positions within a macro-segment.
        start_s = seg.get("start", 0)
        m, s = int(start_s // 60), int(start_s % 60)
        buckets[bucket_idx].append(f"[{m}:{s:02d}] {seg['text']}")

    # Build EmbeddedSegment objects (without embeddings yet)
    macro_segments: List[EmbeddedSegment] = []
    for idx in range(num_macro):
        text = "\n".join(buckets[idx]).strip()
        if not text:
            continue  # Skip empty segments

        macro_segments.append(EmbeddedSegment(
            video_id=video_id,
            segment_index=idx,
            start_time=bucket_times[idx]["start"],
            end_time=bucket_times[idx]["end"],
            text=text,
        ))

    logger.info(
        f"[Segments] Split into {len(macro_segments)} macro-segments "
        f"({segment_duration}s each) for video {video_id}"
    )
    return macro_segments


# ---------------------------------------------------------------------------
# 3. Generate OpenAI embeddings
# ---------------------------------------------------------------------------

async def embed_segments(
    segments: List[EmbeddedSegment],
) -> List[EmbeddedSegment]:
    """
    Generate OpenAI embeddings for each macro-segment's text.
    Mutates segments in-place (adds embedding field) and returns them.
    Uses batch embedding API for efficiency.
    """
    if not segments:
        return segments

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    texts = [seg.text for seg in segments]

    total_chars = sum(len(t) for t in texts)
    logger.info(
        f"[Embed] Generating embeddings for {len(texts)} segments "
        f"({total_chars:,} chars total)..."
    )

    t0 = time.perf_counter()
    try:
        response = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
            dimensions=EMBEDDING_DIMENSIONS,
        )

        for i, seg in enumerate(segments):
            seg.embedding = response.data[i].embedding

        elapsed = time.perf_counter() - t0
        logger.info(
            f"[Embed] Generated {len(segments)} embeddings in {elapsed:.2f}s | "
            f"model={EMBEDDING_MODEL}, dims={EMBEDDING_DIMENSIONS}"
        )
        return segments

    except Exception as e:
        logger.error(f"[Embed] Failed after {time.perf_counter() - t0:.2f}s: {e}")
        raise


# ---------------------------------------------------------------------------
# 4. Full pipeline: raw transcript → embedded macro-segments
# ---------------------------------------------------------------------------

async def process_transcript(
    transcript: List[Dict[str, Any]],
    video_id: str,
) -> List[EmbeddedSegment]:
    """
    End-to-end transcript processing:
    1. Consolidate word-level → sentence-level
    2. Split into 5-minute macro-segments
    3. Generate embeddings

    Returns list of EmbeddedSegments ready for Convex vector storage.
    """
    t0 = time.perf_counter()

    consolidated = consolidate_segments(transcript)
    macro_segments = split_into_macro_segments(consolidated, video_id)
    embedded = await embed_segments(macro_segments)

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[Segments] Full transcript processing for {video_id} in {elapsed:.2f}s | "
        f"raw={len(transcript)} → consolidated={len(consolidated)} → "
        f"macro={len(macro_segments)} → embedded={len(embedded)}"
    )
    return embedded
