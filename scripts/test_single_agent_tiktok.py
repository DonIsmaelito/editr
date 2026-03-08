"""
Test Script: Single Browser Use Agent → TikTok Profile → Find Underperformers → Download

This script runs the FULL single-agent flow:
  1. Browser Use navigates to tiktok.com/@username
  2. Scrapes all videos + view counts from profile grid
  3. Computes median views, flags underperformers (< 75% of median)
  4. For each underperformer, calls tikwm.com API for metadata + no-edit scoring
  5. Downloads top candidates as MP4 (no watermark) via tikwm
  6. Saves JSON manifest with all metadata

Usage:
    python scripts/test_single_agent_tiktok.py malharpandy
    python scripts/test_single_agent_tiktok.py <any_username>

Outputs:
    downloads/  — MP4 files named {video_id}_{views}views.mp4
    downloads/manifest.json — full metadata for all downloaded videos
"""

import asyncio
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

BROWSER_USE_API_KEY = os.getenv("BROWSER_USE_API_KEY", "")

# ---------------------------------------------------------------------------
# The prompt we send to Browser Use to scrape the profile grid
# We ask it to return structured JSON with all videos + view counts
# ---------------------------------------------------------------------------
SCRAPE_PROFILE_PROMPT = """
Navigate to https://www.tiktok.com/@{username}

Wait for the page to fully load (at least 3 seconds).
Dismiss any popups, cookie banners, or login modals if they appear.

Scroll down slowly to make sure all videos are loaded (TikTok lazy-loads).

For EVERY video visible on the profile grid, extract:
1. The video URL (from the href of the video link — looks like /video/XXXXX or full URL)
2. The view count shown on the thumbnail (e.g. "1.2K", "247.5K", "1.2M")

Also extract the creator's follower count from the profile header.

Return your results as a JSON object with this EXACT structure:
{{
    "username": "{username}",
    "follower_count": "the follower count as shown (e.g. '12.5K')",
    "videos": [
        {{
            "url": "full video URL or path",
            "views_text": "view count as shown on thumbnail (e.g. '1.2K')",
            "position": 1
        }},
        ...
    ]
}}

IMPORTANT: Include ALL videos you can see. Scroll down if needed to load more.
Return ONLY the JSON, no other text.
"""


def parse_view_count_text_to_integer(views_text: str) -> int:
    """Convert TikTok view count text like '247.5K' or '1.2M' to integer."""
    v = views_text.strip().replace(",", "")
    try:
        if "M" in v:
            return int(float(v.replace("M", "")) * 1_000_000)
        if "K" in v:
            return int(float(v.replace("K", "")) * 1_000)
        return int(float(v))
    except ValueError:
        return 0


def compute_no_edit_score_from_tikwm_metadata(tikwm_data: dict, username: str) -> dict:
    """
    Score a video 0-5 for how likely it is to be unedited/raw.
    Higher score = more likely raw content that needs editing.

    Signals checked:
    1. Original sound (not a trending audio)
    2. No edit-related hashtags
    3. Short duration (<= 35s)
    4. Dated series format (caption starts with month)
    5. Topic keywords suggesting talking-head style
    """
    title = tikwm_data.get("title", "").lower()
    music_author = tikwm_data.get("music_info", {}).get("author", "")
    music_title = tikwm_data.get("music_info", {}).get("title", "").lower()
    duration = tikwm_data.get("duration", 999)

    signals = {}
    score = 0

    # Signal 1: Original sound (not trending audio)
    is_original = (
        music_author.lower() == username.lower()
        or "original sound" in music_title
    )
    signals["is_original_sound"] = is_original
    if is_original:
        score += 1

    # Signal 2: No edit-related hashtags in caption
    edit_hashtags = ["#edit", "#transition", "#greenscreen", "#capcut", "#aftereffects", "#premiere"]
    has_edit_tags = any(tag in title for tag in edit_hashtags)
    signals["no_edit_hashtags"] = not has_edit_tags
    if not has_edit_tags:
        score += 1

    # Signal 3: Short duration (simple content tends to be shorter)
    signals["short_duration"] = duration <= 35
    if duration <= 35:
        score += 1

    # Signal 4: Dated series format ("nov 26:", "dec 12:", etc.)
    month_prefixes = ["jan ", "feb ", "mar ", "apr ", "may ", "jun ",
                       "jul ", "aug ", "sep ", "oct ", "nov ", "dec "]
    is_series = any(title.startswith(m) or title.startswith("*" + m) for m in month_prefixes)
    signals["daily_series"] = is_series
    if is_series:
        score += 1

    # Signal 5: Talking-head topic keywords (general, not finance-specific)
    topic_keywords = ["update", "breakdown", "story", "vlog", "day in", "grwm",
                       "rant", "opinion", "thoughts", "reaction", "review", "advice",
                       "tip", "hack", "pov", "reply", "responding"]
    has_topic = any(kw in title for kw in topic_keywords)
    signals["topic_keywords"] = has_topic
    if has_topic:
        score += 1

    return {
        "no_edit_score": score,
        "likely_no_edit": score >= 3,
        "signals": signals,
    }


async def step_1_scrape_profile_via_browser_use(username: str) -> dict:
    """Use Browser Use agent to navigate to TikTok profile and scrape video grid."""
    try:
        from browser_use_sdk import AsyncBrowserUse
    except ImportError:
        print("ERROR: browser-use-sdk not installed. Run: pip install browser-use-sdk")
        sys.exit(1)

    prompt = SCRAPE_PROFILE_PROMPT.format(username=username)

    print(f"[Step 1] Launching Browser Use agent to scrape @{username}...")
    t0 = time.perf_counter()

    client = AsyncBrowserUse(api_key=BROWSER_USE_API_KEY)
    created = await client.tasks.create(task=prompt)
    task_id = str(created.id)
    print(f"         Task ID: {task_id}")
    print(f"         Watch live: https://cloud.browser-use.com")

    result = await client.tasks.wait(task_id, timeout=120, interval=3)
    elapsed = time.perf_counter() - t0
    print(f"         Done in {elapsed:.1f}s")

    raw = result.output

    if isinstance(raw, str):
        # Try to extract JSON from the string
        json_start = raw.find("{")
        json_end = raw.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            raw = json.loads(raw[json_start:json_end])

    if not raw or not isinstance(raw, dict):
        print(f"ERROR: Could not parse agent output. Raw: {raw}")
        sys.exit(1)

    return raw


def step_2_find_underperformers_by_median_threshold(videos: list) -> list:
    """Flag videos below 75% of the median view count as underperformers."""
    import statistics

    # Parse view counts to integers
    for v in videos:
        v["views"] = parse_view_count_text_to_integer(v.get("views_text", "0"))

    view_counts = [v["views"] for v in videos if v["views"] > 0]
    if not view_counts:
        print("WARNING: No valid view counts found")
        return []

    median = statistics.median(view_counts)
    mean = statistics.mean(view_counts)
    threshold = median * 0.75

    print(f"\n[Step 2] View count analysis:")
    print(f"         Total videos: {len(videos)}")
    print(f"         Mean views: {mean:,.0f}")
    print(f"         Median views: {median:,.0f}")
    print(f"         Threshold (75% of median): {threshold:,.0f}")

    underperformers = [v for v in videos if v["views"] < threshold and v["views"] > 0]
    underperformers.sort(key=lambda x: x["views"])

    print(f"         Underperformers found: {len(underperformers)}")
    for v in underperformers:
        print(f"           - {v['views']:,} views: {v.get('url', '?')}")

    return underperformers


def step_3_score_and_download_via_tikwm(underperformers: list, username: str, output_dir: str) -> list:
    """
    For each underperformer:
    1. Call tikwm.com API for metadata
    2. Score for no-edit signals
    3. Download the MP4 if score >= 3
    """
    import requests

    os.makedirs(output_dir, exist_ok=True)
    results = []

    print(f"\n[Step 3] Scoring + downloading {len(underperformers)} underperformers via tikwm...")

    for i, video in enumerate(underperformers):
        # Build the full TikTok URL
        url = video.get("url", "")
        if not url.startswith("http"):
            url = f"https://www.tiktok.com{url}"

        # Extract video_id from URL
        video_id = url.rstrip("/").split("/")[-1]

        print(f"\n  [{i+1}/{len(underperformers)}] Video {video_id} ({video['views']:,} views)")

        # Rate limit: tikwm free tier = 1 req/sec
        time.sleep(1.5)

        # Call tikwm API for metadata + download URL
        try:
            api_url = f"https://www.tikwm.com/api/?url={url}"
            resp = requests.get(api_url, timeout=15)
            data = resp.json()

            if data.get("code") != 0:
                print(f"    tikwm API error: {data.get('msg', 'unknown')}")
                continue

            video_data = data["data"]
        except Exception as e:
            print(f"    tikwm API failed: {e}")
            continue

        # Score for no-edit signals
        scoring = compute_no_edit_score_from_tikwm_metadata(video_data, username)
        score = scoring["no_edit_score"]
        likely_no_edit = scoring["likely_no_edit"]

        print(f"    Title: {video_data.get('title', '?')[:60]}")
        print(f"    Duration: {video_data.get('duration', '?')}s")
        print(f"    Audio: {video_data.get('music_info', {}).get('title', '?')}")
        print(f"    No-edit score: {score}/5 {'*** TARGET ***' if likely_no_edit else ''}")

        # Build result entry
        entry = {
            "video_id": video_id,
            "url": url,
            "views": video["views"],
            "title": video_data.get("title", ""),
            "duration": video_data.get("duration", 0),
            "audio": video_data.get("music_info", {}).get("title", ""),
            "is_original_sound": scoring["signals"]["is_original_sound"],
            "no_edit_score": score,
            "likely_no_edit": likely_no_edit,
            "signals": scoring["signals"],
        }

        # Download MP4 if it's a target (score >= 3)
        if likely_no_edit:
            mp4_url = video_data.get("play", "")
            if mp4_url:
                try:
                    time.sleep(1.5)  # rate limit
                    print(f"    Downloading MP4...")
                    mp4_bytes = requests.get(
                        mp4_url,
                        headers={"Referer": "https://www.tiktok.com/"},
                        timeout=30,
                    ).content

                    filename = f"{video_id}_{video['views']}views.mp4"
                    filepath = os.path.join(output_dir, filename)
                    with open(filepath, "wb") as f:
                        f.write(mp4_bytes)

                    size_kb = len(mp4_bytes) / 1024
                    print(f"    Saved: {filename} ({size_kb:.0f} KB)")
                    entry["file"] = filepath
                    entry["size_kb"] = int(size_kb)
                except Exception as e:
                    print(f"    Download failed: {e}")
                    entry["file"] = None
            else:
                print(f"    No MP4 URL from tikwm")
                entry["file"] = None
        else:
            print(f"    Skipping download (score too low)")
            entry["file"] = None

        results.append(entry)

    return results


async def run_full_pipeline(username: str):
    """Run the complete single-agent pipeline."""

    if not BROWSER_USE_API_KEY:
        print("ERROR: BROWSER_USE_API_KEY not set in .env")
        sys.exit(1)

    output_dir = f"downloads/{username}"

    print(f"\n{'='*60}")
    print(f"  EDITR — Single Agent TikTok Pipeline")
    print(f"  Target: @{username}")
    print(f"  Output: {output_dir}/")
    print(f"{'='*60}")

    pipeline_start = time.perf_counter()

    # Step 1: Browser Use scrapes the profile
    profile_data = await step_1_scrape_profile_via_browser_use(username)

    videos = profile_data.get("videos", [])
    follower_count = profile_data.get("follower_count", "?")
    print(f"\n         @{username} | {follower_count} followers | {len(videos)} videos found")

    if not videos:
        print("ERROR: No videos found on profile")
        return

    # Step 2: Find underperformers
    underperformers = step_2_find_underperformers_by_median_threshold(videos)

    if not underperformers:
        print("\nNo underperforming videos found. This account performs consistently!")
        return

    # Step 3: Score + download via tikwm
    results = step_3_score_and_download_via_tikwm(underperformers, username, output_dir)

    # Save manifest
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(results, f, indent=2)

    # Summary
    total_time = time.perf_counter() - pipeline_start
    downloaded = [r for r in results if r.get("file")]
    targets = [r for r in results if r["likely_no_edit"]]

    print(f"\n{'='*60}")
    print(f"  PIPELINE COMPLETE")
    print(f"  Time: {total_time:.1f}s")
    print(f"  Videos scanned: {len(videos)}")
    print(f"  Underperformers: {len(underperformers)}")
    print(f"  Targets (score >= 3): {len(targets)}")
    print(f"  Downloaded: {len(downloaded)}")
    print(f"  Manifest: {manifest_path}")
    print(f"{'='*60}\n")

    if targets:
        print("  TOP RE-EDIT CANDIDATES:")
        for r in sorted(targets, key=lambda x: x["no_edit_score"], reverse=True)[:5]:
            print(f"    [{r['no_edit_score']}/5] {r['views']:,} views — {r['title'][:50]}")
            if r.get("file"):
                print(f"           File: {r['file']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_single_agent_tiktok.py <username>")
        print("Example: python scripts/test_single_agent_tiktok.py malharpandy")
        sys.exit(1)

    username = sys.argv[1].lstrip("@").strip()
    asyncio.run(run_full_pipeline(username))
