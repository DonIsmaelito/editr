"""
Findr Pipeline Orchestrator

End-to-end flow: user query → classified sub-queries → platform search
→ transcript fetch → segment embedding → vector similarity filter
→ LLM moment finding → progressive result delivery via Convex.

This is the main entry point that wires all services together.
"""

import asyncio
import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from openai import AsyncOpenAI

from src.config import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    OPENAI_API_KEY,
    TOP_SEGMENTS_AFTER_FILTER,
)
from src.classifier.query_classifier import QueryClassifier
from src.db import convex_store
from src.models.schemas import (
    ClassifierOutput,
    FindrResult,
    FoundMoment,
    OutputFormat,
    Platform,
    SubQuery,
)
from src.moment_finder.finder import MomentFinder
from src.search.tiktok import TikTokSearchService
from src.search.twitter import TwitterSearchService
from src.search.youtube import YouTubeSearchService
from src.transcript.segment_processor import process_transcript

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Service singletons
# ---------------------------------------------------------------------------
_classifier = QueryClassifier()
_youtube = YouTubeSearchService()
_tiktok = TikTokSearchService()
_twitter = TwitterSearchService()
_moment_finder = MomentFinder()
_openai: Optional[AsyncOpenAI] = None


def _get_openai() -> AsyncOpenAI:
    """Lazy OpenAI client — reads API key at call time, not import time."""
    global _openai
    if _openai is None:
        import os
        key = OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        _openai = AsyncOpenAI(api_key=key)
    return _openai


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    query: str,
    conversation_context: str = "",
    search_id: Optional[str] = None,
    on_progress: Optional[Callable[[str, Any], Awaitable[None]]] = None,
) -> FindrResult:
    """
    Full Findr pipeline:

    1. Classify query → output format + sub-queries with reasoning
    2. For each sub-query (parallel per sub-query):
       a. Search platform API (top 1 result per sub-query, transcript-verified)
       b. Fetch & process transcript → 5-min embedded segments
       c. Store segments in Convex
       d. Vector similarity search (reasoning trace as query) → 1-2 segments
       e. LLM moment finder → exact timestamp
       f. Write result to Convex (frontend sees it immediately)
    3. Mark search complete

    Args:
        query: User's natural language request.
        conversation_context: Prior messages for multi-turn disambiguation.
        search_id: Optional pre-created Convex search ID.
        on_progress: Optional async callback(event_type, data) called at each
                     pipeline stage. The SSE server uses this to stream events.
                     Default None — existing callers are unaffected.

    Returns:
        FindrResult with all discovered moments.
    """
    convex_persistence_enabled = True

    if not search_id:
        try:
            # Create a real Convex search document so downstream writes
            # (status, results, events) have a valid searchId.
            search_id = str(convex_store.create_search(query, []))
        except Exception as e:
            logger.warning(
                f"[Pipeline] Failed to create Convex search; "
                f"running without Convex persistence: {e}"
            )
            search_id = f"session_{uuid.uuid4().hex[:12]}"
            convex_persistence_enabled = False
    elif search_id.startswith("findr_") or search_id.startswith("session_"):
        convex_persistence_enabled = False

    async def _emit_progress(event_type: str, data: Any):
        outgoing = data
        if isinstance(data, dict) and "searchId" not in data:
            outgoing = {**data, "searchId": search_id}

        # 1) Push to live stream callback (SSE)
        if on_progress:
            try:
                await on_progress(event_type, outgoing)
            except Exception as e:
                logger.debug(f"[Pipeline] Progress callback skipped: {e}")

        # 2) Persist event to Convex for UI replay/debugging
        if convex_persistence_enabled:
            try:
                stage = outgoing.get("stage") if isinstance(outgoing, dict) else None
                message = outgoing.get("message") if isinstance(outgoing, dict) else None
                payload = outgoing if isinstance(outgoing, dict) else {"value": str(outgoing)}
                convex_store.add_search_event(
                    search_id=search_id,
                    event_type=event_type,
                    stage=stage,
                    message=message,
                    data=payload,
                )
            except Exception as e:
                logger.debug(f"[Pipeline] Convex event write skipped: {e}")

    result = FindrResult(
        search_id=search_id,
        query=query,
        output_format=OutputFormat.DIRECT,
        platform=Platform.YOUTUBE,
        status="processing",
    )

    pipeline_start = time.perf_counter()

    try:
        # ==================================================================
        # STEP 1: Classify the query
        # ==================================================================
        logger.info(f"[Pipeline] Step 1: Classifying query...")
        _update_status(search_id, "classifying", enabled=convex_persistence_enabled)
        await _emit_progress("status", {
            "stage": "classifying",
            "message": "Classifying your query...",
        })

        t0 = time.perf_counter()
        classification = await _classifier.classify(query, conversation_context)
        logger.info(f"[Pipeline] Classification took {time.perf_counter() - t0:.2f}s")

        # If clarifying questions needed, return them
        if classification.needs_clarification:
            result.status = "needs_clarification"
            result.clarifying_questions = classification.clarifying_questions
            logger.info(
                f"[Pipeline] Needs clarification: "
                f"{len(classification.clarifying_questions)} questions"
            )
            await _emit_progress("clarification", {
                "questions": [
                    {"question": q.question, "options": q.options}
                    for q in classification.clarifying_questions
                ]
            })
            return result

        result.platform = classification.platform
        result.output_format = classification.output_format

        logger.info(
            f"[Pipeline] Classified: platform={classification.platform.value}, "
            f"format={classification.output_format.value}, "
            f"sub_queries={len(classification.sub_queries)}"
        )
        if convex_persistence_enabled:
            try:
                convex_store.update_search_metadata(
                    search_id=search_id,
                    platforms=[classification.platform.value],
                    output_format=classification.output_format.value,
                )
            except Exception as e:
                logger.debug(f"[Pipeline] Convex metadata update skipped: {e}")
        await _emit_progress("trace", {
            "message": (
                f"Classified as {classification.platform.value} "
                f"({classification.output_format.value}), "
                f"{len(classification.sub_queries)} sub-query"
                f"{'ies' if len(classification.sub_queries) != 1 else 'y'}"
            ),
            "platform": classification.platform.value,
            "outputFormat": classification.output_format.value,
            "subQueryCount": len(classification.sub_queries),
        })
        for sq in classification.sub_queries:
            await _emit_progress("trace", {
                "message": (
                    f"Sub-query #{sq.order}: {sq.proposed_video_query}"
                ),
                "order": sq.order,
                "subQuery": sq.proposed_video_query,
                "reasoning": sq.reasoning,
                "title": sq.title,
            })

        # ==================================================================
        # STEP 2: Process sub-queries
        # ==================================================================
        _update_status(search_id, "searching", enabled=convex_persistence_enabled)
        await _emit_progress("status", {
            "stage": "searching",
            "message": f"Searching {classification.platform.value}...",
        })

        requested_count = _extract_requested_result_count(query)

        if classification.platform == Platform.YOUTUBE:
            if (
                classification.output_format == OutputFormat.DIRECT
                and requested_count > 1
            ):
                moments = await _process_youtube_requested_count(
                    classification=classification,
                    raw_query=query,
                    requested_count=requested_count,
                    search_id=search_id,
                    on_progress=_emit_progress,
                    convex_enabled=convex_persistence_enabled,
                )
            else:
                moments = await _process_youtube_subqueries(
                    classification,
                    search_id,
                    _emit_progress,
                    convex_enabled=convex_persistence_enabled,
                )
            result.moments = moments
        elif classification.platform == Platform.TIKTOK:
            moments = await _process_tiktok_subqueries(
                classification,
                search_id,
                _emit_progress,
                convex_enabled=convex_persistence_enabled,
            )
            result.moments = moments
        elif classification.platform == Platform.X:
            moments = await _process_x_subqueries(
                classification,
                search_id,
                _emit_progress,
                convex_enabled=convex_persistence_enabled,
            )
            result.moments = moments

        # ==================================================================
        # STEP 3: Complete
        # ==================================================================
        result.status = "complete"
        _update_status(search_id, "complete", enabled=convex_persistence_enabled)

        total_elapsed = time.perf_counter() - pipeline_start
        logger.info(
            f"[Pipeline] Complete in {total_elapsed:.2f}s | "
            f"{len(result.moments)} moments found | "
            f"query={query[:50]!r}"
        )
        await _emit_progress("done", {
            "query": query,
            "outputFormat": result.output_format.value,
            "platform": result.platform.value,
            "momentCount": len(result.moments),
        })
        return result

    except Exception as e:
        logger.error(f"[Pipeline] Failed: {e}", exc_info=True)
        result.status = "error"
        result.error_message = str(e)
        _update_status(
            search_id,
            "error",
            str(e),
            enabled=convex_persistence_enabled,
        )
        await _emit_progress("error", {"message": str(e)})
        return result


# ---------------------------------------------------------------------------
# YouTube sub-query processor
# ---------------------------------------------------------------------------

async def _process_youtube_subqueries(
    classification: ClassifierOutput,
    search_id: str,
    on_progress: Optional[Callable[[str, Any], Awaitable[None]]] = None,
    convex_enabled: bool = True,
) -> List[FoundMoment]:
    """
    Process all YouTube sub-queries. For structured output, processes
    sequentially so results stream to the user in order. For direct
    output, processes in parallel for speed.
    """
    all_moments: List[FoundMoment] = []

    if on_progress:
        await on_progress("status", {
            "stage": "processing",
            "message": "Processing transcripts...",
        })

    if classification.output_format == OutputFormat.STRUCTURED:
        # Sequential — drop collapsables one by one in order.
        # Order matters: the user sees these as expandable sections
        # appearing top-to-bottom in pedagogical sequence.
        used_video_ids: List[str] = []
        for sq in sorted(classification.sub_queries, key=lambda s: s.order):
            if on_progress:
                await on_progress("status", {
                    "stage": "finding",
                    "message": f"Finding best moment for sub-query #{sq.order}...",
                })

            moments = await _process_single_youtube_subquery(
                sub_query=sq.proposed_video_query,
                reasoning=sq.reasoning,
                order=sq.order,
                sub_query_title=sq.title,
                search_id=search_id,
                exclude_video_ids=used_video_ids,
                on_progress=on_progress,
            )
            all_moments.extend(moments)

            # Track used video IDs to avoid duplicates in later sub-queries
            for m in moments:
                if m.video_id not in used_video_ids:
                    used_video_ids.append(m.video_id)

            # Stream each result to Convex + SSE as it's found
            for m in moments:
                _add_result_to_convex(search_id, m, enabled=convex_enabled)
                if on_progress:
                    await on_progress("moment", _moment_to_event(m))

    else:
        # Parallel — all sub-queries at once
        tasks = [
            _process_single_youtube_subquery(
                sub_query=sq.proposed_video_query,
                reasoning=sq.reasoning,
                order=sq.order,
                sub_query_title=sq.title,
                search_id=search_id,
                on_progress=on_progress,
            )
            for sq in classification.sub_queries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                logger.error(f"[Pipeline] Sub-query failed: {r}")
                if on_progress:
                    await on_progress("trace", {
                        "message": f"Sub-query failed: {r}",
                        "platform": "youtube",
                    })
                continue
            all_moments.extend(r)
            for m in r:
                _add_result_to_convex(search_id, m, enabled=convex_enabled)
                if on_progress:
                    await on_progress("moment", _moment_to_event(m))

    return all_moments


async def _process_youtube_requested_count(
    classification: ClassifierOutput,
    raw_query: str,
    requested_count: int,
    search_id: str,
    on_progress: Optional[Callable[[str, Any], Awaitable[None]]] = None,
    convex_enabled: bool = True,
) -> List[FoundMoment]:
    """
    Direct YouTube path for explicit numeric asks (e.g., "find me 3 videos").

    Treat count as a retrieval target, not semantic decomposition:
    - Use one intent anchor query.
    - Keep pulling unique candidates until requested_count is met
      or attempts are exhausted.
    """
    all_moments: List[FoundMoment] = []
    used_video_ids: List[str] = []
    attempts = 0
    max_attempts = max(3, requested_count * 3)

    ordered_subqueries = sorted(classification.sub_queries, key=lambda s: s.order)
    if ordered_subqueries:
        anchor = ordered_subqueries[0]
        anchor_query = anchor.proposed_video_query
        anchor_reasoning = anchor.reasoning
        anchor_title = anchor.title or "Result"
    else:
        anchor_query = raw_query
        anchor_reasoning = f"User asked for {requested_count} relevant videos about: {raw_query}"
        anchor_title = "Result"

    logger.info(
        f"[Pipeline] Explicit result-count mode | requested={requested_count} | "
        f"anchor_query={anchor_query[:80]!r}"
    )

    if on_progress:
        await on_progress("status", {
            "stage": "processing",
            "message": "Processing transcripts...",
        })

    while len(all_moments) < requested_count and attempts < max_attempts:
        if on_progress:
            await on_progress("status", {
                "stage": "finding",
                "message": (
                    f"Finding result {len(all_moments) + 1} of {requested_count}..."
                ),
            })

        moments = await _process_single_youtube_subquery(
            sub_query=anchor_query,
            reasoning=anchor_reasoning,
            order=attempts,
            sub_query_title=anchor_title,
            search_id=search_id,
            exclude_video_ids=used_video_ids,
            on_progress=on_progress,
        )
        attempts += 1

        if not moments:
            continue

        for m in moments:
            if len(all_moments) >= requested_count:
                break

            m.sub_query_order = len(all_moments)
            if not m.sub_query_title:
                m.sub_query_title = f"Result {m.sub_query_order + 1}"

            all_moments.append(m)
            _add_result_to_convex(search_id, m, enabled=convex_enabled)
            if on_progress:
                await on_progress("moment", _moment_to_event(m))

    logger.info(
        f"[Pipeline] Explicit result-count mode complete | "
        f"requested={requested_count}, returned={len(all_moments)}, attempts={attempts}"
    )
    return all_moments


async def _process_single_youtube_subquery(
    sub_query: str,
    reasoning: str,
    order: int,
    search_id: str,
    sub_query_title: str = "",
    exclude_video_ids: Optional[List[str]] = None,
    on_progress: Optional[Callable[[str, Any], Awaitable[None]]] = None,
) -> List[FoundMoment]:
    """
    Full pipeline for a single sub-query:
    1. Search YouTube (top 1 result with verified transcript)
    2. Process transcript → 5-min embedded segments
    3. Store segments in Convex
    4. Vector similarity search using reasoning trace
    5. LLM moment finding on filtered segments
    """

    subquery_start = time.perf_counter()

    # ------------------------------------------------------------------
    # 1. Search YouTube — get top 1 result with transcript
    # ------------------------------------------------------------------
    logger.info(f"[SubQuery #{order}] Searching: {sub_query[:60]}...")
    if on_progress:
        await on_progress("trace", {
            "message": f"[SubQuery #{order}] Searching YouTube: {sub_query}",
            "order": order,
            "subQuery": sub_query,
            "reasoning": reasoning,
            "title": sub_query_title,
            "platform": "youtube",
        })
    t0 = time.perf_counter()

    video_results = await _youtube.search_with_transcript(
        query=sub_query,
        max_results=1,
        exclude_video_ids=exclude_video_ids,
    )

    logger.info(f"[SubQuery #{order}] YouTube search took {time.perf_counter() - t0:.2f}s")

    if not video_results:
        logger.warning(f"[SubQuery #{order}] No videos with transcript found")
        if on_progress:
            await on_progress("trace", {
                "message": f"[SubQuery #{order}] No transcript-enabled YouTube results found",
                "order": order,
                "platform": "youtube",
            })
        return []

    video_data = video_results[0]
    video = video_data["video"]
    transcript = video_data["transcript"]

    # Register this candidate immediately so repeated retrieval attempts can
    # advance even if moment finding returns 0 for this video.
    if exclude_video_ids is not None and video.video_id not in exclude_video_ids:
        exclude_video_ids.append(video.video_id)

    logger.info(
        f"[SubQuery #{order}] Selected: \"{video.title}\" "
        f"({video.duration:.0f}s, {len(transcript)} segments)"
    )
    if on_progress:
        await on_progress("trace", {
            "message": (
                f"[SubQuery #{order}] Selected video: {video.title[:90]} "
                f"({video.duration:.0f}s)"
            ),
            "order": order,
            "videoId": video.video_id,
            "videoTitle": video.title,
            "platform": "youtube",
        })

    # ------------------------------------------------------------------
    # 1b. Check transcript cache — skip fetch if we already have it
    # ------------------------------------------------------------------
    cached_transcript = None
    try:
        cached_transcript = convex_store.get_cached_transcript(video.video_id)
    except Exception as e:
        logger.debug(f"[SubQuery #{order}] Transcript cache check skipped: {e}")

    if cached_transcript:
        logger.info(
            f"[SubQuery #{order}] Transcript cache HIT for {video.video_id} "
            f"({len(cached_transcript)} segments)"
        )
        transcript = cached_transcript
        if on_progress:
            await on_progress("trace", {
                "message": f"[SubQuery #{order}] Transcript cache hit ({len(transcript)} segments)",
                "order": order,
                "videoId": video.video_id,
                "platform": "youtube",
            })
    else:
        logger.info(f"[SubQuery #{order}] Transcript cache MISS — using fetched transcript")
        if on_progress:
            await on_progress("trace", {
                "message": f"[SubQuery #{order}] Transcript cache miss; using fetched transcript",
                "order": order,
                "videoId": video.video_id,
                "platform": "youtube",
            })
        # Cache the transcript for future reuse
        try:
            convex_store.cache_transcript(
                video_id=video.video_id,
                platform="youtube",
                segments=transcript,
            )
            logger.info(f"[SubQuery #{order}] Cached transcript for {video.video_id}")
        except Exception as e:
            logger.debug(f"[SubQuery #{order}] Transcript cache write skipped: {e}")

    # ------------------------------------------------------------------
    # 2. Process transcript → embedded 5-min segments
    # ------------------------------------------------------------------
    logger.info(f"[SubQuery #{order}] Processing transcript...")
    t0 = time.perf_counter()

    embedded_segments = await process_transcript(transcript, video.video_id)

    logger.info(f"[SubQuery #{order}] Transcript processing took {time.perf_counter() - t0:.2f}s")
    if on_progress:
        await on_progress("trace", {
            "message": (
                f"[SubQuery #{order}] Processed transcript into "
                f"{len(embedded_segments)} embedded segment(s)"
            ),
            "order": order,
            "videoId": video.video_id,
            "segments": len(embedded_segments),
            "platform": "youtube",
        })

    if not embedded_segments:
        logger.warning(f"[SubQuery #{order}] No segments after processing")
        if on_progress:
            await on_progress("trace", {
                "message": f"[SubQuery #{order}] No segments after transcript processing",
                "order": order,
                "videoId": video.video_id,
                "platform": "youtube",
            })
        return []

    # ------------------------------------------------------------------
    # 3. Store segments in Convex for vector search
    # ------------------------------------------------------------------
    logger.info(f"[SubQuery #{order}] Storing {len(embedded_segments)} segments in Convex...")

    try:
        convex_store.store_segments([
            {
                "video_id": seg.video_id,
                "segment_index": seg.segment_index,
                "start_time": seg.start_time,
                "end_time": seg.end_time,
                "text": seg.text,
                "embedding": seg.embedding,
            }
            for seg in embedded_segments
        ])
    except Exception as e:
        logger.warning(f"[SubQuery #{order}] Convex store failed, using local fallback: {e}")
        if on_progress:
            await on_progress("trace", {
                "message": (
                    f"[SubQuery #{order}] Convex vector store unavailable; "
                    "falling back to full transcript scan"
                ),
                "order": order,
                "videoId": video.video_id,
                "platform": "youtube",
            })
        # Fallback: skip vector search, scan ALL segments with LLM
        return await _moment_finder.find_moments(
            segments=[
                {"text": s.text, "startTime": s.start_time, "endTime": s.end_time}
                for s in embedded_segments
            ],
            sub_query=sub_query,
            reasoning=reasoning,
            video_id=video.video_id,
            video_title=video.title,
            sub_query_order=order,
            sub_query_title=sub_query_title,
        )

    # ------------------------------------------------------------------
    # 4. Vector similarity search — filter to top 1-2 segments
    # ------------------------------------------------------------------
    logger.info(f"[SubQuery #{order}] Running vector similarity search...")
    t0 = time.perf_counter()

    # Embed the reasoning trace as the search query
    reasoning_embedding = await _embed_text(reasoning)

    filtered_segments = convex_store.search_similar_segments(
        query_embedding=reasoning_embedding,
        video_id=video.video_id,
        limit=TOP_SEGMENTS_AFTER_FILTER,
    )

    logger.info(f"[SubQuery #{order}] Vector search took {time.perf_counter() - t0:.2f}s")
    if on_progress:
        await on_progress("trace", {
            "message": (
                f"[SubQuery #{order}] Vector search kept "
                f"{len(filtered_segments)} segment(s)"
            ),
            "order": order,
            "videoId": video.video_id,
            "segmentsKept": len(filtered_segments),
            "platform": "youtube",
        })

    if not filtered_segments:
        logger.warning(f"[SubQuery #{order}] Vector search returned 0 segments, scanning all")
        filtered_segments = [
            {"text": s.text, "startTime": s.start_time, "endTime": s.end_time}
            for s in embedded_segments[:3]  # Cap at 3
        ]

    logger.info(
        f"[SubQuery #{order}] Filtered to {len(filtered_segments)} segments "
        f"from {len(embedded_segments)} total"
    )

    # ------------------------------------------------------------------
    # 5. LLM moment finding — exact timestamps
    # ------------------------------------------------------------------
    logger.info(f"[SubQuery #{order}] Finding exact moments...")
    t0 = time.perf_counter()

    moments = await _moment_finder.find_moments(
        segments=filtered_segments,
        sub_query=sub_query,
        reasoning=reasoning,
        video_id=video.video_id,
        video_title=video.title,
        sub_query_order=order,
        sub_query_title=sub_query_title,
    )

    logger.info(
        f"[SubQuery #{order}] Moment finder took {time.perf_counter() - t0:.2f}s | "
        f"found {len(moments)} moments"
    )
    if on_progress:
        await on_progress("trace", {
            "message": f"[SubQuery #{order}] Found {len(moments)} moment(s)",
            "order": order,
            "videoId": video.video_id,
            "momentCount": len(moments),
            "platform": "youtube",
        })

    # ------------------------------------------------------------------
    # 6. Segment cleanup — remove stored segments to avoid stale data
    # ------------------------------------------------------------------
    try:
        convex_store.delete_segments_by_video(video.video_id)
        logger.info(f"[SubQuery #{order}] Cleaned up segments for {video.video_id}")
    except Exception as e:
        logger.debug(f"[SubQuery #{order}] Segment cleanup skipped: {e}")

    elapsed = time.perf_counter() - subquery_start
    logger.info(
        f"[SubQuery #{order}] Complete in {elapsed:.2f}s | "
        f"video={video.video_id} | moments={len(moments)}"
    )
    if on_progress:
        await on_progress("trace", {
            "message": (
                f"[SubQuery #{order}] Complete ({elapsed:.2f}s, "
                f"{len(moments)} moment(s))"
            ),
            "order": order,
            "videoId": video.video_id,
            "platform": "youtube",
        })

    return moments


# ---------------------------------------------------------------------------
# TikTok sub-query processor
# ---------------------------------------------------------------------------

async def _process_tiktok_subqueries(
    classification: ClassifierOutput,
    search_id: str,
    on_progress: Optional[Callable[[str, Any], Awaitable[None]]] = None,
    convex_enabled: bool = True,
) -> List[FoundMoment]:
    """
    Process TikTok sub-queries. Always parallel (TikTok = direct format).

    TikTok pipeline is simpler than YouTube:
    - No transcripts, no segment embedding, no vector search, no moment finding.
    - The entire short-form video IS the moment.
    - Flow: search → normalize → build embed URL → write to Convex.

    NUANCE: TikTok embeds use the iframe player at
    https://www.tiktok.com/player/v1/{video_id}
    There are no start/end timestamps — the whole video plays.
    """
    all_moments: List[FoundMoment] = []

    tiktok_start = time.perf_counter()
    logger.info(
        f"[Pipeline:TikTok] Processing {len(classification.sub_queries)} "
        f"sub-queries in parallel"
    )

    if on_progress:
        await on_progress("status", {"stage": "searching"})

    tasks = [
        _process_single_tiktok_subquery(sq, search_id, on_progress=on_progress)
        for sq in classification.sub_queries
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, Exception):
            logger.error(f"[Pipeline:TikTok] Sub-query failed: {r}")
            if on_progress:
                await on_progress("trace", {
                    "message": f"TikTok sub-query failed: {r}",
                    "platform": "tiktok",
                })
            continue
        all_moments.extend(r)
        for m in r:
            _add_result_to_convex(search_id, m, enabled=convex_enabled)
            if on_progress:
                await on_progress("moment", _moment_to_event(m))

    elapsed = time.perf_counter() - tiktok_start
    logger.info(
        f"[Pipeline:TikTok] Complete in {elapsed:.2f}s | "
        f"{len(all_moments)} results"
    )
    return all_moments


async def _process_single_tiktok_subquery(
    sq: SubQuery,
    search_id: str,
    on_progress: Optional[Callable[[str, Any], Awaitable[None]]] = None,
) -> List[FoundMoment]:
    """
    Search TikTok and convert results directly to FoundMoments.

    No transcript processing — TikTok videos are short-form (15s-3min),
    so the entire video IS the moment. We search, normalize, and build
    embed URLs directly.
    """
    t0 = time.perf_counter()
    logger.info(f"[TikTok #{sq.order}] Searching: {sq.proposed_video_query[:60]}...")
    if on_progress:
        await on_progress("trace", {
            "message": f"[TikTok #{sq.order}] Searching TikTok: {sq.proposed_video_query}",
            "order": sq.order,
            "subQuery": sq.proposed_video_query,
            "reasoning": sq.reasoning,
            "title": sq.title,
            "platform": "tiktok",
        })

    search_results = await _tiktok.search_videos(
        query=sq.proposed_video_query,
        max_results=1,
    )

    if not search_results:
        logger.warning(f"[TikTok #{sq.order}] No results found")
        if on_progress:
            await on_progress("trace", {
                "message": (
                    f"[TikTok #{sq.order}] No TikTok results found "
                    "(likely blocked by login/anti-bot)"
                ),
                "order": sq.order,
                "platform": "tiktok",
            })
        return []

    # Convert the top search result into a FoundMoment
    # NUANCE: We only take the top result per sub-query, same as YouTube.
    # For TikTok, "moment finding" is trivial — the whole video IS the moment.
    video = search_results[0]
    embed_url = f"https://www.tiktok.com/player/v1/{video.video_id}"

    moment = FoundMoment(
        video_id=video.video_id,
        start=0,
        end=0,  # TikTok: no timestamps, whole video plays
        title=video.title[:100],
        description=video.description[:200] if video.description else "",
        embed_url=embed_url,
        video_title=video.title[:150],
        platform=Platform.TIKTOK,
        sub_query_order=sq.order,
        sub_query_title=sq.title,
    )

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[TikTok #{sq.order}] Complete in {elapsed:.2f}s | "
        f"video={video.video_id} | {video.title[:50]}"
    )
    if on_progress:
        await on_progress("trace", {
            "message": (
                f"[TikTok #{sq.order}] Selected result: {video.title[:90]}"
            ),
            "order": sq.order,
            "videoId": video.video_id,
            "videoTitle": video.title,
            "platform": "tiktok",
        })

    return [moment]


# ---------------------------------------------------------------------------
# X/Twitter sub-query processor
# ---------------------------------------------------------------------------

async def _process_x_subqueries(
    classification: ClassifierOutput,
    search_id: str,
    on_progress: Optional[Callable[[str, Any], Awaitable[None]]] = None,
    convex_enabled: bool = True,
) -> List[FoundMoment]:
    """
    Process X/Twitter sub-queries. Always parallel (X = direct format).

    X pipeline is the simplest:
    - No transcripts, no timestamps, no moment finding.
    - Posts (text + optional media) are the results.
    - The frontend renders them via react-tweet component.
    - Flow: search → normalize → build embed ref → write to Convex.

    NUANCE: X embeds are NOT iframes. The embed_url is a reference for
    the react-tweet component: https://x.com/i/status/{post_id}
    The component fetches and renders the post natively.
    """
    all_moments: List[FoundMoment] = []

    x_start = time.perf_counter()
    logger.info(
        f"[Pipeline:X] Processing {len(classification.sub_queries)} "
        f"sub-queries in parallel"
    )

    if on_progress:
        await on_progress("status", {"stage": "searching"})

    tasks = [
        _process_single_x_subquery(sq, search_id, on_progress=on_progress)
        for sq in classification.sub_queries
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, Exception):
            logger.error(f"[Pipeline:X] Sub-query failed: {r}")
            if on_progress:
                await on_progress("trace", {
                    "message": f"X sub-query failed: {r}",
                    "platform": "x",
                })
            continue
        all_moments.extend(r)
        for m in r:
            _add_result_to_convex(search_id, m, enabled=convex_enabled)
            if on_progress:
                await on_progress("moment", _moment_to_event(m))

    elapsed = time.perf_counter() - x_start
    logger.info(
        f"[Pipeline:X] Complete in {elapsed:.2f}s | "
        f"{len(all_moments)} results"
    )
    return all_moments


async def _process_single_x_subquery(
    sq: SubQuery,
    search_id: str,
    on_progress: Optional[Callable[[str, Any], Awaitable[None]]] = None,
) -> List[FoundMoment]:
    """
    Search X/Twitter and convert results directly to FoundMoments.

    NUANCE: X posts are text-first. Not all results have video — some are
    text-only or image posts. We still return them as FoundMoments because
    the frontend's react-tweet component handles all post types. The
    embed_url (x.com/i/status/{id}) works for any post type.

    We use search_with_relevance_filter when reasoning is available to
    do a keyword-based pre-filter on post text. This is cheaper than
    an LLM call and works well for text-heavy content.
    """
    t0 = time.perf_counter()
    logger.info(f"[X #{sq.order}] Searching: {sq.proposed_video_query[:60]}...")
    if on_progress:
        await on_progress("trace", {
            "message": f"[X #{sq.order}] Searching X: {sq.proposed_video_query}",
            "order": sq.order,
            "subQuery": sq.proposed_video_query,
            "reasoning": sq.reasoning,
            "title": sq.title,
            "platform": "x",
        })

    # Use relevance filter since X posts are text-heavy
    search_results = await _twitter.search_with_relevance_filter(
        query=sq.proposed_video_query,
        reasoning=sq.reasoning,
        max_results=1,
    )

    if not search_results:
        # Fallback to unfiltered search
        logger.info(f"[X #{sq.order}] Relevance filter returned 0, trying unfiltered...")
        if on_progress:
            await on_progress("trace", {
                "message": f"[X #{sq.order}] No matches after relevance filter; retrying broad search",
                "order": sq.order,
                "platform": "x",
            })
        search_results = await _twitter.search_videos(
            query=sq.proposed_video_query,
            max_results=1,
        )

    if not search_results:
        logger.warning(f"[X #{sq.order}] No results found")
        if on_progress:
            await on_progress("trace", {
                "message": f"[X #{sq.order}] No X results found",
                "order": sq.order,
                "platform": "x",
            })
        return []

    # Convert the top result into a FoundMoment
    post = search_results[0]
    embed_url = f"https://x.com/i/status/{post.video_id}"

    moment = FoundMoment(
        video_id=post.video_id,
        start=0,
        end=0,  # X: no timestamps
        title=post.title[:100],
        description=post.description[:200] if post.description else "",
        embed_url=embed_url,
        video_title=post.title[:150],
        platform=Platform.X,
        sub_query_order=sq.order,
        sub_query_title=sq.title,
    )

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[X #{sq.order}] Complete in {elapsed:.2f}s | "
        f"post={post.video_id} | {post.channel}"
    )
    if on_progress:
        await on_progress("trace", {
            "message": f"[X #{sq.order}] Selected post by {post.channel}",
            "order": sq.order,
            "videoId": post.video_id,
            "platform": "x",
        })

    return [moment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _extract_requested_result_count(query: str) -> int:
    """
    Extract explicit result count from the raw query.

    Matches patterns such as:
    - "3 videos", "2 clips", "4 results", "3 reviews"
    - "three videos", "five reviews"

    Returns 1 when no explicit count is detected.
    """
    text = (query or "").lower()
    noun_pattern = r"(?:videos?|results?|clips?|posts?|reviews?)"

    digit_match = re.search(rf"\b(\d+)\s+{noun_pattern}\b", text)
    if digit_match:
        try:
            value = int(digit_match.group(1))
            return max(1, min(value, 10))
        except ValueError:
            pass

    word_match = re.search(
        rf"\b({'|'.join(_NUMBER_WORDS.keys())})\s+{noun_pattern}\b",
        text,
    )
    if word_match:
        return _NUMBER_WORDS[word_match.group(1)]

    return 1


def _moment_to_event(m: FoundMoment) -> Dict[str, Any]:
    """Convert a FoundMoment to the SSE moment event shape."""
    return {
        "videoName": m.video_title or m.title,
        "videoId": m.video_id,
        "embedUrl": m.embed_url,
        "start": m.start,
        "end": m.end,
        "title": m.sub_query_title or m.title,
        "description": m.description,
        "order": m.sub_query_order,
    }


async def _embed_text(text: str) -> List[float]:
    """Generate OpenAI embedding for a single text string."""
    client = _get_openai()
    response = await client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
        dimensions=EMBEDDING_DIMENSIONS,
    )
    return response.data[0].embedding


def _update_status(
    search_id: str,
    status: str,
    error: str = None,
    enabled: bool = True,
):
    """Update Convex search status. Silently ignores Convex errors."""
    if not enabled:
        return
    try:
        convex_store.update_search_status(search_id, status, error)
    except Exception as e:
        logger.debug(f"[Pipeline] Convex status update skipped: {e}")


def _add_result_to_convex(
    search_id: str,
    moment: FoundMoment,
    enabled: bool = True,
):
    """
    Write a single result to Convex. Frontend sees it immediately.

    For structured output, the frontend uses `subQueryTitle` as the
    collapsable section header. When the user expands the section,
    `embedUrl` plays the video at the exact moment. `title` is the
    moment-level label shown inside the expanded section.
    """
    if not enabled:
        return
    try:
        convex_store.add_result(search_id, {
            "platform": moment.platform.value,
            "videoId": moment.video_id,
            "embedUrl": moment.embed_url,
            "title": moment.title,
            "description": moment.description,
            "startTime": moment.start,
            "endTime": moment.end,
            "highlightType": "",
            "relevanceScore": 0.8,
            "order": moment.sub_query_order,
            "subQueryTitle": moment.sub_query_title,
        })
    except Exception as e:
        logger.debug(f"[Pipeline] Convex result write skipped: {e}")
