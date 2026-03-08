"""
Editr Pipeline Orchestrator

The REAL end-to-end flow that runs when the user submits a @username:
  1. Browser Use agent scrapes the TikTok profile grid (video URLs + view counts)
  2. Python computes median views, flags underperformers
  3. tikwm.com API fetches metadata + no-edit scoring + downloads MP4s
  4. For each downloaded video: Gemini analysis → Lyria music → NB2 overlays → FFmpeg render
  5. Upload to GCS → return signed URL

Progress events are dual-written to:
  - SSE stream (on_progress callback for real-time UI)
  - Convex (job events table for persistent UI state)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

import requests

from src_2.config import (
    BROWSER_USE_API_KEY,
    GCS_BUCKET,
    GOOGLE_CLOUD_API_KEY,
    MAX_VIDEOS_DEFAULT,
)
from src_2.db import convex_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Browser Use profile scraping prompt
# ---------------------------------------------------------------------------

SCRAPE_PROFILE_PROMPT = """
Navigate to https://www.tiktok.com/@{username}

Wait for the page to fully load (3 seconds). Dismiss any popups or login modals.

I need the URLs of the first 15 RECENT (non-pinned) videos from this profile.

IMPORTANT: TikTok pins some videos at the top of the grid — they have a small pin icon. SKIP those pinned videos entirely. Start collecting from the first NON-PINNED video.

Use JavaScript to extract the video URLs from the grid:
- Each video card links to a URL like /video/XXXXX
- I only need the href URLs, nothing else

Return a JSON object with ONLY the video URLs:
{{
    "username": "{username}",
    "videos": [
        {{"url": "https://www.tiktok.com/@{username}/video/XXXXX"}}
    ]
}}

DO NOT scroll through the entire page. DO NOT extract view counts, follower counts, or any other metrics. Just get the first 15 non-pinned video URLs as fast as possible.
"""


# ---------------------------------------------------------------------------
# Extract username from natural language input
# ---------------------------------------------------------------------------

import re

def _extract_username_and_count(raw_input: str) -> tuple:
    """
    Parse natural language like:
      "Hi, my TikTok username is eunjoos.world and I want 3 videos to be edited"
      "eunjoos.world"
      "@eunjoos.world edit 5 videos"

    Returns (username, video_count). video_count is None if not specified.
    """
    text = raw_input.strip()

    # Try to find a number for video count
    count_match = re.search(r'(\d+)\s*(?:video|clip|tiktok)', text, re.IGNORECASE)
    count = int(count_match.group(1)) if count_match else None

    # Try to extract username — look for patterns like @handle or "is handle" or just the handle
    # Remove common filler words to isolate the username
    username = None

    # Pattern: @username
    at_match = re.search(r'@([\w.]+)', text)
    if at_match:
        username = at_match.group(1)
    else:
        # Pattern: "username is X" or "handle is X" or "tiktok is X"
        is_match = re.search(r'(?:username|handle|tiktok)\s+is\s+([\w.]+)', text, re.IGNORECASE)
        if is_match:
            username = is_match.group(1)
        else:
            # If input looks like just a username (single word with dots, no spaces or very few)
            words = text.split()
            if len(words) == 1:
                username = words[0].lstrip("@")
            else:
                # Last resort: find any word that looks like a tiktok handle (has a dot or is alphanumeric)
                for word in words:
                    clean = word.strip("@,!?.\"'")
                    if "." in clean and len(clean) > 3 and not clean.startswith("http"):
                        username = clean
                        break

    if not username:
        # Absolute fallback: treat the whole input as the username
        username = text.split()[0].lstrip("@") if text else text

    username = username.lstrip("@").strip().rstrip(".")
    logger.info(f"[Pipeline] Parsed input: username='{username}' count={count} | raw='{text[:60]}'")
    return username, count


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    username: str,
    platform: str = "tiktok",
    max_videos: int = MAX_VIDEOS_DEFAULT,
    on_progress: Optional[Callable[[str, Any], Awaitable[None]]] = None,
    job_id: Optional[str] = None,
):
    # Parse the username from natural language input
    parsed_username, parsed_count = _extract_username_and_count(username)
    username = parsed_username
    if parsed_count:
        max_videos = min(parsed_count, 10)

    # --- Setup Convex persistence ---
    convex_enabled = True
    if not job_id:
        try:
            job_id = str(convex_store.create_job(username, platform, max_videos))
            logger.info(f"[Pipeline] Convex job created: {job_id}")
        except Exception as e:
            logger.warning(f"[Pipeline] Convex unavailable, running without persistence: {e}")
            job_id = f"editr_{uuid.uuid4().hex[:12]}"
            convex_enabled = False
    elif job_id.startswith("editr_"):
        convex_enabled = False

    async def _emit(event_type: str, data: Any):
        """Dual-write: push to SSE stream + persist to Convex."""
        outgoing = data if isinstance(data, dict) else {"value": str(data)}
        if "jobId" not in outgoing:
            outgoing = {**outgoing, "jobId": job_id}

        # 1. SSE stream callback
        if on_progress:
            try:
                await on_progress(event_type, outgoing)
            except Exception as e:
                logger.debug(f"[Pipeline] SSE callback error: {e}")

        # 2. Convex persistence
        if convex_enabled:
            try:
                convex_store.add_job_event(
                    job_id=job_id,
                    event_type=event_type,
                    message=outgoing.get("message") if isinstance(outgoing, dict) else None,
                    data=outgoing,
                )
            except Exception as e:
                logger.debug(f"[Pipeline] Convex event write error: {e}")

    pipeline_start = time.perf_counter()
    username = username.lstrip("@").strip()

    try:
        # ==================================================================
        # STEP 1: Get recent video URLs from TikTok profile
        # ==================================================================
        await _emit("status", {"stage": "scraping", "message": f"Visiting @{username}'s TikTok..."})
        if convex_enabled:
            convex_store.update_job_status(job_id, "scraping")

        logger.info(f"[Pipeline] STEP 1: Getting recent video URLs for @{username}")
        await _emit("trace", {"message": f"Browser agent navigating to tiktok.com/@{username}..."})
        profile_data = await _scrape_profile_via_browser_use(username)
        videos_raw = profile_data.get("videos", [])

        logger.info(f"[Pipeline] Got {len(videos_raw)} video URLs from profile")
        await _emit("trace", {"message": f"Found {len(videos_raw)} recent videos on profile"})

        await _emit("profile", {
            "username": username,
            "videoCount": len(videos_raw),
        })

        if convex_enabled:
            convex_store.update_job_profile(job_id, json.dumps({
                "username": username, "videoCount": len(videos_raw),
            }))

        if not videos_raw:
            await _emit("error", {"message": f"No videos found on @{username}'s profile"})
            if convex_enabled:
                convex_store.update_job_status(job_id, "error", error_message="No videos found")
            return

        # ==================================================================
        # STEP 2: Filter + download via tikwm
        # Checks each video: duration >= 30s, original sound, no edit tags
        # ==================================================================
        await _emit("status", {"stage": "downloading", "message": "Finding editable videos..."})
        if convex_enabled:
            convex_store.update_job_status(job_id, "processing")

        logger.info(f"[Pipeline] STEP 2: Filtering + downloading via tikwm (need {max_videos})")
        await _emit("trace", {"message": f"Checking videos for duration, captions, and audio..."})

        download_dir = f"downloads/{username}/{job_id}"
        os.makedirs(download_dir, exist_ok=True)

        downloaded = await asyncio.to_thread(
            _filter_and_download_via_tikwm,
            videos_raw, username, download_dir, max_videos
        )

        if not downloaded:
            await _emit("error", {"message": "No editable videos found (all had captions, music, or were too short)"})
            if convex_enabled:
                convex_store.update_job_status(job_id, "error", error_message="No editable videos")
            return

        for dl in downloaded:
            await _emit("video_scored", {
                "videoId": dl["video_id"],
                "title": dl.get("title", "")[:80],
                "views": dl.get("views", 0),
                "duration": dl.get("duration", 0),
                "selected": True,
                "localPath": dl.get("path", ""),
                "originalUrl": dl.get("url", ""),
            })

        await _emit("trace", {"message": f"Downloaded {len(downloaded)} videos ready for editing"})
        logger.info(f"[Pipeline] Downloaded {len(downloaded)} editable videos")

        # ==================================================================
        # STEP 4: Edit each video (Gemini → Lyria → NB2 → FFmpeg)
        # ==================================================================
        await _emit("status", {"stage": "editing", "message": "Editing videos with AI..."})

        output_dir = f"outputs/{username}/{job_id}"
        os.makedirs(output_dir, exist_ok=True)

        logger.info(f"[Pipeline] STEP 3: Editing {len(downloaded)} videos IN PARALLEL")
        await _emit("trace", {"message": f"Editing {len(downloaded)} videos in parallel — analyzing content, generating overlays..."})

        from src_2.editor.editor_pipeline import run_editor_pipeline_for_single_video_locally

        async def _edit_one_video(dl: dict) -> bool:
            """Edit a single video. Returns True if successful."""
            vid = dl["video_id"]
            inp = dl["path"]
            out = os.path.join(output_dir, f"{vid}_edited.mp4")

            async def on_progress(event_type, data):
                payload = {"videoId": vid, "step": data.get("step", "editing"),
                           "status": data.get("status", ""), "message": data.get("detail", "")}
                for k, v in data.items():
                    if k not in {"step", "status", "detail"}:
                        payload[k] = v
                await _emit("video_progress", payload)

            try:
                result = await run_editor_pipeline_for_single_video_locally(
                    video_path=inp, output_path=out,
                    video_duration_hint=dl.get("duration", 0),
                    asset_dir=os.path.join(output_dir, "assets", vid),
                    on_progress=on_progress,
                )

                if result.skipped:
                    logger.info(f"[Pipeline] Video {vid} skipped: {result.skip_reason}")
                    return False

                # Upload to GCS
                url = ""
                if result.output_bytes and GOOGLE_CLOUD_API_KEY:
                    try:
                        url = await _upload_bytes_to_gcs(result.output_bytes, vid, job_id)
                    except Exception as e:
                        logger.error(f"[Pipeline] GCS upload failed for {vid}: {e}")

                await _emit("video_complete", {
                    "videoId": vid,
                    "editedUrl": url,
                    "localPath": out,
                    "overlays": result.overlays_applied,
                    "hasMusic": result.has_music,
                    "musicPreview": result.music_preview,
                    "captionCount": len(result.understanding.transcript),
                    "summary": result.understanding.video_summary,
                    "overlayPreviews": result.overlay_previews,
                })

                logger.info(f"[Pipeline] Video {vid} edited in {result.total_seconds:.1f}s | overlays={result.overlays_applied}")
                return True

            except Exception as e:
                logger.error(f"[Pipeline] Video {vid} edit FAILED: {e}", exc_info=True)
                await _emit("error", {"message": str(e), "videoId": vid})
                return False

        # Run all video edits in parallel
        results = await asyncio.gather(*[_edit_one_video(dl) for dl in downloaded])
        videos_processed = sum(1 for r in results if r)

        if convex_enabled:
            convex_store.update_job_videos_processed(job_id, videos_processed)

        # ==================================================================
        # STEP 5: Done
        # ==================================================================
        total_time = round(time.perf_counter() - pipeline_start, 2)
        await _emit("done", {
            "videosProcessed": videos_processed,
            "totalTime": total_time,
        })

        if convex_enabled:
            convex_store.update_job_status(job_id, "complete")

        logger.info(f"[Pipeline] COMPLETE | {videos_processed} videos edited in {total_time}s")

    except Exception as e:
        logger.error(f"[Pipeline] FATAL ERROR: {e}", exc_info=True)
        await _emit("error", {"message": str(e)})
        if convex_enabled:
            convex_store.update_job_status(job_id, "error", error_message=str(e))
        raise


# ---------------------------------------------------------------------------
# Step 1 helper: Browser Use scraping
# ---------------------------------------------------------------------------

async def _scrape_profile_via_browser_use(username: str) -> dict:
    """Launch a Browser Use agent to scrape the TikTok profile grid.

    SDK flow: tasks.create() returns TaskCreatedResponse with .id
    Then tasks.wait(task_id) polls until done, returns TaskView with .output
    """
    from browser_use_sdk import AsyncBrowserUse

    prompt = SCRAPE_PROFILE_PROMPT.format(username=username)
    client = AsyncBrowserUse(api_key=BROWSER_USE_API_KEY)

    logger.info(f"[Pipeline:Scrape] Creating Browser Use task for @{username}")
    t0 = time.perf_counter()

    # Step 1: Create the task — returns TaskCreatedResponse with task.id
    created = await client.tasks.create(task=prompt)
    task_id = str(created.id)
    logger.info(f"[Pipeline:Scrape] Task created: {task_id} | Watch: https://cloud.browser-use.com")

    # Step 2: Wait for completion — polls status every 2s, returns TaskView
    result = await client.tasks.wait(task_id, timeout=300, interval=3)
    elapsed = time.perf_counter() - t0

    logger.info(
        f"[Pipeline:Scrape] Agent done in {elapsed:.1f}s | "
        f"status={result.status} | is_success={result.is_success} | "
        f"output_type={type(result.output).__name__ if result.output else 'None'} | "
        f"steps={len(result.steps) if result.steps else 0}"
    )

    # Step 3: Parse the output field as JSON
    raw = result.output
    if raw is None:
        logger.error(f"[Pipeline:Scrape] Agent returned no output (status={result.status})")
        return {"videos": []}

    if isinstance(raw, str):
        logger.info(f"[Pipeline:Scrape] Raw output (first 300): {raw[:300]}")
        try:
            # Browser Use sometimes returns JSON with escaped quotes like {\"key\": \"val\"}
            # Try parsing as-is first, then try unescaping backslash-quotes
            clean = raw.strip()

            # Attempt 1: parse directly
            try:
                raw = json.loads(clean)
            except json.JSONDecodeError:
                # Attempt 2: unescape \" to " (Browser Use wraps output in escaped JSON)
                unescaped = clean.replace('\\"', '"').replace("\\'", "'")
                # Find the JSON object boundaries after unescaping
                json_start = unescaped.find("{")
                json_end = unescaped.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    raw = json.loads(unescaped[json_start:json_end])
                    logger.info(f"[Pipeline:Scrape] Parsed after unescaping backslash-quotes")
                else:
                    logger.error(f"[Pipeline:Scrape] No JSON object found after unescaping")
                    return {"videos": []}
        except json.JSONDecodeError as e:
            logger.error(
                f"[Pipeline:Scrape] JSON parse FAILED even after unescaping: {e}\n"
                f"  First 500 chars: {raw[:500]}"
            )
            return {"videos": []}

    if not isinstance(raw, dict):
        logger.error(f"[Pipeline:Scrape] Output is not a dict: {type(raw)} | {str(raw)[:300]}")
        return {"videos": []}

    return raw


# ---------------------------------------------------------------------------
# Step 3 helper: tikwm download
# ---------------------------------------------------------------------------

def _filter_and_download_via_tikwm(
    video_urls: list,
    username: str,
    output_dir: str,
    max_videos: int,
) -> list:
    """
    For each video URL, call tikwm API to get metadata and check 3 skip criteria:
    1. Duration < 30s → skip (too short for music overlay)
    2. Non-original sound (trending audio/music) → skip
    3. Edit-related hashtags → skip (already edited)

    Downloads videos that pass all checks. Stops after max_videos downloaded.
    """
    downloaded = []
    checked = 0

    for video in video_urls:
        if len(downloaded) >= max_videos:
            break

        url = video.get("url", "")
        if not url.startswith("http"):
            url = f"https://www.tiktok.com{url}"
        video_id = url.rstrip("/").split("/")[-1]

        checked += 1
        logger.info(f"[Pipeline:Filter] [{checked}] Checking {video_id}...")
        time.sleep(1.5)  # tikwm rate limit

        # Get metadata from tikwm
        try:
            resp = requests.get(f"https://www.tikwm.com/api/?url={url}", timeout=15)
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"[Pipeline:Filter] [{checked}] tikwm error: {data.get('msg')}")
                continue
            vdata = data["data"]
        except Exception as e:
            logger.error(f"[Pipeline:Filter] [{checked}] tikwm failed: {e}")
            continue

        title = vdata.get("title", "")
        duration = vdata.get("duration", 0)
        music_author = vdata.get("music_info", {}).get("author", "").lower()
        music_title = vdata.get("music_info", {}).get("title", "").lower()

        # --- SKIP CHECK 1: Duration out of range (30s–90s) ---
        if duration < 30:
            logger.info(f"[Pipeline:Filter] [{checked}] SKIP — too short ({duration}s < 30s)")
            continue
        if duration > 90:
            logger.info(f"[Pipeline:Filter] [{checked}] SKIP — too long ({duration}s > 90s cap)")
            continue

        # --- SKIP CHECK 2: Non-original sound (has trending audio/music) ---
        is_original_sound = (
            music_author == username.lower()
            or "original sound" in music_title
            or not music_title.strip()
        )
        if not is_original_sound:
            logger.info(
                f"[Pipeline:Filter] [{checked}] SKIP — has music: '{music_title[:40]}' by '{music_author}'"
            )
            continue

        # --- SKIP CHECK 3: Edit-related hashtags ---
        title_lower = title.lower()
        edit_tags = ["#edit", "#transition", "#greenscreen", "#capcut", "#aftereffects"]
        if any(tag in title_lower for tag in edit_tags):
            logger.info(f"[Pipeline:Filter] [{checked}] SKIP — has edit hashtags")
            continue

        # --- PASSED ALL CHECKS — download ---
        mp4_url = vdata.get("play", "")
        if not mp4_url:
            logger.warning(f"[Pipeline:Filter] [{checked}] No MP4 URL from tikwm")
            continue

        logger.info(
            f"[Pipeline:Filter] [{checked}] PASS — '{title[:50]}' | "
            f"duration={duration}s | original_sound=true"
        )

        time.sleep(1.5)  # rate limit before download
        try:
            mp4_bytes = requests.get(
                mp4_url, headers={"Referer": "https://www.tiktok.com/"}, timeout=30
            ).content

            filename = f"{video_id}.mp4"
            filepath = os.path.join(output_dir, filename)
            with open(filepath, "wb") as f:
                f.write(mp4_bytes)

            size_kb = len(mp4_bytes) / 1024
            logger.info(f"[Pipeline:Filter] [{checked}] Downloaded: {filename} ({size_kb:.0f}KB)")

            downloaded.append({
                "video_id": video_id,
                "path": filepath,
                "views": 0,  # we don't collect views anymore
                "title": title,
                "duration": duration,
                "url": url,
            })
        except Exception as e:
            logger.error(f"[Pipeline:Filter] [{checked}] Download failed: {e}")

    logger.info(
        f"[Pipeline:Filter] Checked {checked} videos, "
        f"downloaded {len(downloaded)}/{max_videos} requested"
    )
    return downloaded


# ---------------------------------------------------------------------------
# GCS upload helper
# ---------------------------------------------------------------------------

async def _upload_bytes_to_gcs(video_bytes: bytes, video_id: str, job_id: str) -> str:
    """Upload edited video bytes to GCS and return a public URL.

    Uses make_public() instead of signed URLs because ADC (gcloud auth)
    doesn't have a private key for signing. For production, use a service
    account key or switch to V4 signing with impersonation.
    """
    import google.cloud.storage as storage

    blob_name = f"edits/{job_id}/{video_id}_edited.mp4"
    logger.info(f"[Pipeline:Upload] Uploading {len(video_bytes)/(1024*1024):.1f}MB to gs://{GCS_BUCKET}/{blob_name}")

    client = await asyncio.to_thread(storage.Client)
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)

    await asyncio.to_thread(blob.upload_from_string, video_bytes, content_type="video/mp4")

    # Make the blob publicly readable so we can link to it without signing
    try:
        await asyncio.to_thread(blob.make_public)
        public_url = blob.public_url
        logger.info(f"[Pipeline:Upload] Uploaded + made public | url={public_url}")
        return public_url
    except Exception as e:
        # If make_public fails (bucket-level ACL restrictions), fall back to gs:// URI
        gs_url = f"https://storage.googleapis.com/{GCS_BUCKET}/{blob_name}"
        logger.warning(f"[Pipeline:Upload] make_public failed: {e} — using direct URL: {gs_url}")
        return gs_url
