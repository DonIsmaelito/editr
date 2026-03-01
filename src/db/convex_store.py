"""
Findr Convex Store

Python wrapper around Convex for:
- Storing search sessions and results (mutations)
- Storing transcript segments with embeddings (mutations)
- Running vector similarity search on transcript segments (actions)
- Transcript caching (query)

Convex vector search runs server-side in actions (not queries).
The Python client calls these via the HTTP API.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Convex client
# ---------------------------------------------------------------------------

_client = None


def _get_client():
    global _client
    if _client is None:
        try:
            from convex import ConvexClient
            url = os.environ.get("CONVEX_URL", "")
            if not url:
                raise RuntimeError("CONVEX_URL environment variable not set")
            _client = ConvexClient(url)
            logger.info(f"[Convex] Connected to {url[:40]}...")
        except ImportError:
            raise RuntimeError("convex package not installed. Run: pip install convex")
    return _client


# ---------------------------------------------------------------------------
# Search Session
# ---------------------------------------------------------------------------

def create_search(query: str, platforms: List[str]) -> str:
    """Create a new search session. Returns the Convex document ID."""
    client = _get_client()
    search_id = client.mutation("searches:create", {
        "query": query,
        "platforms": platforms,
    })
    logger.info(f"[Convex] Created search: {search_id}")
    return search_id


def update_search_status(
    search_id: str,
    status: str,
    error_message: Optional[str] = None,
):
    """Update search session status (classifying→searching→analyzing→complete→error)."""
    client = _get_client()
    args = {"id": search_id, "status": status}
    if error_message:
        args["errorMessage"] = error_message
    client.mutation("searches:updateStatus", args)
    logger.debug(f"[Convex] Search {search_id} → {status}")


def update_search_metadata(
    search_id: str,
    platforms: Optional[List[str]] = None,
    output_format: Optional[str] = None,
):
    """Patch platform/output format metadata on a search session."""
    client = _get_client()
    args: Dict[str, Any] = {"id": search_id}
    if platforms is not None:
        args["platforms"] = platforms
    if output_format is not None:
        args["outputFormat"] = output_format
    client.mutation("searches:updateMetadata", args)
    logger.debug(
        f"[Convex] Search {search_id} metadata updated | "
        f"platforms={platforms} output_format={output_format}"
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def add_result(search_id: str, result: Dict[str, Any]) -> str:
    """
    Add a single moment result to a search session.
    The frontend sees this immediately via Convex subscription.
    Returns the result document ID.
    """
    client = _get_client()
    result_id = client.mutation("results:addResult", {
        "searchId": search_id,
        **result,
    })
    logger.debug(f"[Convex] Added result {result_id} to search {search_id}")
    return result_id


# ---------------------------------------------------------------------------
# Search Events (Progress / Trace)
# ---------------------------------------------------------------------------

def add_search_event(
    search_id: str,
    event_type: str,
    stage: Optional[str] = None,
    message: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Append a progress/trace event to a search.
    """
    client = _get_client()
    args: Dict[str, Any] = {
        "searchId": search_id,
        "eventType": event_type,
    }
    if stage is not None:
        args["stage"] = stage
    if message is not None:
        args["message"] = message
    if data is not None:
        args["dataJson"] = json.dumps(data, default=str)

    event_id = client.mutation("events:add", args)
    logger.debug(
        f"[Convex] Added event {event_id} to search {search_id} "
        f"| type={event_type} stage={stage or '-'}"
    )
    return event_id


# ---------------------------------------------------------------------------
# Transcript Segment Storage (for vector search)
# ---------------------------------------------------------------------------

def store_segments(segments: List[Dict[str, Any]]):
    """
    Store embedded transcript segments in Convex for vector search.
    Each segment has: videoId, segmentIndex, startTime, endTime, text, embedding.
    """
    client = _get_client()
    t0 = time.perf_counter()
    for seg in segments:
        client.mutation("segments:insert", {
            "videoId": seg["video_id"],
            "segmentIndex": seg["segment_index"],
            "startTime": seg["start_time"],
            "endTime": seg["end_time"],
            "text": seg["text"],
            "embedding": seg["embedding"],
        })
    elapsed = time.perf_counter() - t0
    logger.info(
        f"[Convex] Stored {len(segments)} transcript segments in {elapsed:.2f}s | "
        f"video={segments[0]['video_id'] if segments else 'N/A'}"
    )


def search_similar_segments(
    query_embedding: List[float],
    video_id: str,
    limit: int = 2,
) -> List[Dict[str, Any]]:
    """
    Run vector similarity search on transcript segments.
    Calls the Convex action that wraps vectorSearch.

    Returns list of segments sorted by similarity score,
    each with: videoId, segmentIndex, startTime, endTime, text, _score.
    """
    client = _get_client()
    t0 = time.perf_counter()
    results = client.action("segments:searchSimilar", {
        "queryEmbedding": query_embedding,
        "videoId": video_id,
        "limit": limit,
    })
    elapsed = time.perf_counter() - t0
    if results:
        scores = [r.get("_score", 0) for r in results]
        logger.info(
            f"[Convex] Vector search in {elapsed:.2f}s | "
            f"video={video_id} | {len(results)} segments | "
            f"scores={[f'{s:.3f}' for s in scores]}"
        )
    else:
        logger.warning(
            f"[Convex] Vector search in {elapsed:.2f}s | "
            f"video={video_id} | 0 segments returned"
        )
    return results


# ---------------------------------------------------------------------------
# Segment Cleanup
# ---------------------------------------------------------------------------

def delete_segments_by_video(video_id: str) -> int:
    """
    Delete all transcript segments for a video after moment finding.
    Prevents stale vector data from accumulating in Convex.
    Returns the number of segments deleted.
    """
    client = _get_client()
    count = client.mutation("segments:deleteByVideo", {"videoId": video_id})
    logger.info(f"[Convex] Deleted {count} segments for video {video_id}")
    return count


# ---------------------------------------------------------------------------
# Transcript Cache
# ---------------------------------------------------------------------------

def get_cached_transcript(video_id: str) -> Optional[List[Dict[str, Any]]]:
    """Check if we already have a cached transcript for this video."""
    client = _get_client()
    try:
        result = client.query("transcriptCache:getByVideoId", {"videoId": video_id})
        if result and result.get("segments"):
            segments = json.loads(result["segments"])
            logger.info(f"[Convex] Transcript cache hit for {video_id}")
            return segments
    except Exception as e:
        logger.debug(f"[Convex] Transcript cache miss for {video_id}: {e}")
    return None


def cache_transcript(
    video_id: str,
    platform: str,
    segments: List[Dict[str, Any]],
):
    """Cache a transcript for future reuse."""
    client = _get_client()
    try:
        client.mutation("transcriptCache:insert", {
            "videoId": video_id,
            "platform": platform,
            "segments": json.dumps(segments),
        })
        logger.info(f"[Convex] Cached transcript for {video_id} ({len(segments)} segments)")
    except Exception as e:
        logger.warning(f"[Convex] Failed to cache transcript: {e}")
