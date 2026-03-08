"""
EDITR — Full End-to-End Pipeline Test

One command: username → scrape → score → download → edit → output

Usage:
    python scripts/test_full_pipeline.py malharpandy
    python scripts/test_full_pipeline.py malharpandy --max-videos 1

This runs LOCALLY (no Daytona sandbox, no Convex, no server).
It tests the complete pipeline: Browser Use scraping → tikwm download →
Gemini video understanding → Lyria music → Nano Banana overlays → FFmpeg render.

Outputs go to: outputs/{username}/
"""

import asyncio
import json
import os
import sys
import time

# Load env FIRST before any src_2 imports
from dotenv import load_dotenv
load_dotenv()

# Now safe to import src_2
from src_2.config import GOOGLE_CLOUD_API_KEY, BROWSER_USE_API_KEY

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("editr.test")


# ---------------------------------------------------------------------------
# STEP 1: Scrape TikTok profile via Browser Use
# (reused from test_single_agent_tiktok.py)
# ---------------------------------------------------------------------------

SCRAPE_PROFILE_PROMPT = """
Navigate to https://www.tiktok.com/@{username}

Wait for the page to fully load (at least 3 seconds).
Dismiss any popups, cookie banners, or login modals if they appear.
Scroll down slowly to make sure all videos are loaded.

For EVERY video visible on the profile grid, extract:
1. The video URL (from the href)
2. The view count shown on the thumbnail (e.g. "1.2K", "247.5K")

Also extract the creator's follower count from the profile header.

Return ONLY a JSON object:
{{
    "username": "{username}",
    "follower_count": "follower count as shown",
    "videos": [
        {{"url": "full video URL or path", "views_text": "view count as shown", "position": 1}}
    ]
}}
"""


def parse_view_count_text(views_text: str) -> int:
    v = views_text.strip().replace(",", "")
    try:
        if "M" in v: return int(float(v.replace("M", "")) * 1_000_000)
        if "K" in v: return int(float(v.replace("K", "")) * 1_000)
        return int(float(v))
    except ValueError:
        return 0


async def scrape_tiktok_profile_with_browser_use(username: str) -> dict:
    from browser_use_sdk import AsyncBrowserUse

    prompt = SCRAPE_PROFILE_PROMPT.format(username=username)
    logger.info(f"[SCRAPE] Launching Browser Use agent for @{username}")
    t0 = time.perf_counter()

    client = AsyncBrowserUse(api_key=BROWSER_USE_API_KEY)
    created = await client.tasks.create(task=prompt)
    task_id = str(created.id)
    logger.info(f"[SCRAPE] Task created: {task_id} | Watch: https://cloud.browser-use.com")

    result = await client.tasks.wait(task_id, timeout=120, interval=3)
    elapsed = time.perf_counter() - t0
    logger.info(f"[SCRAPE] Agent finished in {elapsed:.1f}s | output_type={type(result.output).__name__}")

    raw = result.output

    if isinstance(raw, str):
        json_start = raw.find("{")
        json_end = raw.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            raw = json.loads(raw[json_start:json_end])

    if not raw or not isinstance(raw, dict):
        logger.error(f"[SCRAPE] Failed to parse agent output: {raw}")
        return {"videos": []}

    logger.info(f"[SCRAPE] Found {len(raw.get('videos', []))} videos for @{username}")
    return raw


# ---------------------------------------------------------------------------
# STEP 2: Find underperformers + download via tikwm
# ---------------------------------------------------------------------------

def find_underperformers_and_download(videos: list, username: str, output_dir: str, max_videos: int) -> list:
    import statistics
    import requests

    # Parse view counts
    for v in videos:
        v["views"] = parse_view_count_text(v.get("views_text", "0"))

    view_counts = [v["views"] for v in videos if v["views"] > 0]
    if not view_counts:
        logger.warning("[SCORE] No valid view counts")
        return []

    median = statistics.median(view_counts)
    threshold = median * 0.75

    logger.info(f"[SCORE] Median views: {median:,.0f} | Threshold: {threshold:,.0f}")

    underperformers = sorted(
        [v for v in videos if v["views"] < threshold and v["views"] > 0],
        key=lambda x: x["views"]
    )
    logger.info(f"[SCORE] {len(underperformers)} underperformers below threshold")

    os.makedirs(output_dir, exist_ok=True)
    downloaded = []

    for i, video in enumerate(underperformers[:max_videos * 2]):  # fetch extra in case some fail
        if len(downloaded) >= max_videos:
            break

        url = video.get("url", "")
        if not url.startswith("http"):
            url = f"https://www.tiktok.com{url}"
        video_id = url.rstrip("/").split("/")[-1]

        logger.info(f"[DOWNLOAD] [{i+1}] Fetching tikwm metadata for {video_id} ({video['views']:,} views)")
        time.sleep(1.5)  # tikwm rate limit

        try:
            resp = requests.get(f"https://www.tikwm.com/api/?url={url}", timeout=15)
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"[DOWNLOAD] tikwm error: {data.get('msg')}")
                continue
            vdata = data["data"]
        except Exception as e:
            logger.error(f"[DOWNLOAD] tikwm API failed: {e}")
            continue

        # No-edit scoring
        title = vdata.get("title", "").lower()
        music_author = vdata.get("music_info", {}).get("author", "").lower()
        music_title = vdata.get("music_info", {}).get("title", "").lower()
        duration = vdata.get("duration", 999)

        score = 0
        if music_author == username.lower() or "original sound" in music_title: score += 1
        edit_tags = ["#edit", "#transition", "#greenscreen", "#capcut"]
        if not any(t in title for t in edit_tags): score += 1
        if duration <= 35: score += 1

        logger.info(
            f"[DOWNLOAD] [{i+1}] title='{vdata.get('title','')[:50]}' | "
            f"duration={duration}s | score={score}/3"
        )

        if score < 2:
            logger.info(f"[DOWNLOAD] [{i+1}] Skipping (low no-edit score)")
            continue

        # Download MP4
        mp4_url = vdata.get("play", "")
        if not mp4_url:
            logger.warning(f"[DOWNLOAD] [{i+1}] No MP4 URL from tikwm")
            continue

        time.sleep(1.5)
        try:
            logger.info(f"[DOWNLOAD] [{i+1}] Downloading MP4...")
            mp4_bytes = requests.get(
                mp4_url, headers={"Referer": "https://www.tiktok.com/"}, timeout=30
            ).content

            filename = f"{video_id}_{video['views']}views.mp4"
            filepath = os.path.join(output_dir, filename)
            with open(filepath, "wb") as f:
                f.write(mp4_bytes)

            logger.info(f"[DOWNLOAD] [{i+1}] Saved: {filename} ({len(mp4_bytes)/1024:.0f}KB)")

            downloaded.append({
                "video_id": video_id,
                "path": filepath,
                "views": video["views"],
                "title": vdata.get("title", ""),
                "duration": duration,
                "url": url,
            })
        except Exception as e:
            logger.error(f"[DOWNLOAD] [{i+1}] Download failed: {e}")

    logger.info(f"[DOWNLOAD] Downloaded {len(downloaded)} videos to {output_dir}")
    return downloaded


# ---------------------------------------------------------------------------
# STEP 3: Edit each downloaded video
# ---------------------------------------------------------------------------

async def edit_single_video(video_info: dict, output_dir: str) -> dict:
    from src_2.editor.editor_pipeline import run_editor_pipeline_for_single_video_locally

    input_path = video_info["path"]
    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{base}_edited.mp4")

    logger.info(
        f"[EDIT] Starting edit for {video_info['video_id']} | "
        f"duration={video_info['duration']}s | path={input_path}"
    )

    async def on_progress(event_type, data):
        step = data.get("step", "?")
        status = data.get("status", "?")
        detail = data.get("detail", "")
        logger.info(f"[EDIT:{video_info['video_id']}] {step} → {status} {detail}")

    try:
        result = await run_editor_pipeline_for_single_video_locally(
            video_path=input_path,
            output_path=output_path,
            video_duration_hint=video_info.get("duration", 0),
            on_progress=on_progress,
        )

        if result.skipped:
            logger.info(f"[EDIT] SKIPPED {video_info['video_id']}: {result.skip_reason}")
            return {**video_info, "edited": False, "skip_reason": result.skip_reason}

        logger.info(
            f"[EDIT] DONE {video_info['video_id']} in {result.total_seconds:.1f}s | "
            f"overlays={result.overlays_applied} | music={result.has_music} | "
            f"output={output_path}"
        )

        return {
            **video_info,
            "edited": True,
            "edited_path": output_path,
            "overlays": result.overlays_applied,
            "has_music": result.has_music,
            "edit_time": result.total_seconds,
            "summary": result.understanding.video_summary,
            "cue_moments": len(result.understanding.cue_moments),
        }

    except Exception as e:
        logger.error(f"[EDIT] FAILED {video_info['video_id']}: {e}", exc_info=True)
        return {**video_info, "edited": False, "error": str(e)}


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_full_pipeline.py <username> [--max-videos N]")
        sys.exit(1)

    username = sys.argv[1].lstrip("@").strip()
    max_videos = 1  # default: process 1 video for testing
    if "--max-videos" in sys.argv:
        idx = sys.argv.index("--max-videos")
        max_videos = int(sys.argv[idx + 1])

    # Validate env
    if not GOOGLE_CLOUD_API_KEY:
        logger.error("GOOGLE_CLOUD_API_KEY not set in .env")
        sys.exit(1)
    if not BROWSER_USE_API_KEY:
        logger.error("BROWSER_USE_API_KEY not set in .env")
        sys.exit(1)

    output_dir = f"outputs/{username}"
    download_dir = f"downloads/{username}"

    print(f"\n{'='*70}")
    print(f"  EDITR — Full Pipeline Test")
    print(f"  Username: @{username}")
    print(f"  Max videos: {max_videos}")
    print(f"  Downloads: {download_dir}/")
    print(f"  Outputs:   {output_dir}/")
    print(f"{'='*70}\n")

    pipeline_start = time.perf_counter()

    # --- PHASE 1: Scrape ---
    logger.info("=" * 50)
    logger.info("PHASE 1: SCRAPE TIKTOK PROFILE")
    logger.info("=" * 50)
    profile = await scrape_tiktok_profile_with_browser_use(username)
    videos = profile.get("videos", [])

    if not videos:
        logger.error("No videos found — aborting")
        return

    # --- PHASE 2: Score + Download ---
    logger.info("=" * 50)
    logger.info("PHASE 2: SCORE + DOWNLOAD UNDERPERFORMERS")
    logger.info("=" * 50)
    downloaded = find_underperformers_and_download(videos, username, download_dir, max_videos)

    if not downloaded:
        logger.error("No videos downloaded — aborting")
        return

    # --- PHASE 3: Edit ---
    logger.info("=" * 50)
    logger.info("PHASE 3: EDIT VIDEOS")
    logger.info("=" * 50)
    os.makedirs(output_dir, exist_ok=True)

    results = []
    for video_info in downloaded:
        result = await edit_single_video(video_info, output_dir)
        results.append(result)

    # --- SUMMARY ---
    total_time = time.perf_counter() - pipeline_start
    edited = [r for r in results if r.get("edited")]
    failed = [r for r in results if not r.get("edited")]

    print(f"\n{'='*70}")
    print(f"  PIPELINE COMPLETE — {total_time:.1f}s total")
    print(f"  Videos scraped: {len(videos)}")
    print(f"  Downloaded: {len(downloaded)}")
    print(f"  Edited: {len(edited)}")
    print(f"  Failed/skipped: {len(failed)}")
    print(f"")
    for r in edited:
        print(f"  ✓ {r['video_id']} — {r.get('summary', '?')[:50]}")
        print(f"    {r.get('edited_path', '?')}")
        print(f"    overlays={r.get('overlays', 0)} music={r.get('has_music', False)} time={r.get('edit_time', 0):.1f}s")
    for r in failed:
        reason = r.get("skip_reason") or r.get("error", "unknown")
        print(f"  ✗ {r['video_id']} — {reason}")
    print(f"{'='*70}\n")

    # Save manifest
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Manifest saved to {manifest_path}")


if __name__ == "__main__":
    asyncio.run(main())
