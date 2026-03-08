"""
Editr Convex Store

Python wrapper for Convex CRUD operations on jobs, videos, and events.
Reuses the lazy client pattern from src/db/convex_store.py.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Convex client (same pattern as src/db/convex_store.py)
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
            logger.info(f"[Editr Convex] Connected to {url[:40]}...")
        except ImportError:
            raise RuntimeError("convex package not installed. Run: pip install convex")
    return _client


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def create_job(
    username: str,
    platform: str,
    max_videos: int,
) -> str:
    client = _get_client()
    job_id = client.mutation("jobs:create", {
        "username": username,
        "platform": platform,
        "maxVideos": max_videos,
    })
    logger.info(f"[Editr Convex] Created job: {job_id}")
    return job_id


def update_job_status(
    job_id: str,
    status: str,
    error_message: Optional[str] = None,
):
    client = _get_client()
    args: Dict[str, Any] = {"id": job_id, "status": status}
    if error_message:
        args["errorMessage"] = error_message
    client.mutation("jobs:updateStatus", args)
    logger.debug(f"[Editr Convex] Job {job_id} -> {status}")


def update_job_profile(job_id: str, profile_data_json: str):
    client = _get_client()
    client.mutation("jobs:updateProfile", {
        "id": job_id,
        "profileDataJson": profile_data_json,
    })


def update_job_videos_processed(job_id: str, count: int):
    client = _get_client()
    client.mutation("jobs:updateVideosProcessed", {
        "id": job_id,
        "videosProcessed": count,
    })


# ---------------------------------------------------------------------------
# Job Events
# ---------------------------------------------------------------------------

def add_job_event(
    job_id: str,
    event_type: str,
    message: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> str:
    client = _get_client()
    args: Dict[str, Any] = {
        "jobId": job_id,
        "eventType": event_type,
    }
    if message is not None:
        args["message"] = message
    if data is not None:
        args["dataJson"] = json.dumps(data, default=str)
    event_id = client.mutation("jobEvents:add", args)
    logger.debug(
        f"[Editr Convex] Event {event_id} for job {job_id} | type={event_type}"
    )
    return event_id


# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------

def create_video(
    job_id: str,
    platform: str,
    video_id: str,
    original_url: str,
    title: str,
    duration: float,
    thumbnail: Optional[str],
    views: int,
    likes: int,
    comments: int,
    shares: Optional[int],
    fixability_score: float,
    selected: bool,
) -> str:
    client = _get_client()
    args: Dict[str, Any] = {
        "jobId": job_id,
        "platform": platform,
        "videoId": video_id,
        "originalUrl": original_url,
        "title": title,
        "duration": duration,
        "thumbnail": thumbnail or "",
        "views": views,
        "likes": likes,
        "comments": comments,
        "fixabilityScore": fixability_score,
        "selected": selected,
    }
    if shares is not None:
        args["shares"] = shares
    vid = client.mutation("videos:create", args)
    logger.debug(f"[Editr Convex] Created video {vid} for job {job_id}")
    return vid


def update_video_edit_status(
    video_doc_id: str,
    edit_status: str,
    edit_level: Optional[str] = None,
    edited_video_url: Optional[str] = None,
):
    client = _get_client()
    args: Dict[str, Any] = {
        "id": video_doc_id,
        "editStatus": edit_status,
    }
    if edit_level is not None:
        args["editLevel"] = edit_level
    if edited_video_url is not None:
        args["editedVideoUrl"] = edited_video_url
    client.mutation("videos:updateEditStatus", args)
    logger.debug(f"[Editr Convex] Video {video_doc_id} -> {edit_status}")
