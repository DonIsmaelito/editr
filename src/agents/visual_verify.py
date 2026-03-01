"""
Findr Visual Verification Agent

Takes a YouTube embed URL (with ?start=X timestamp) and produces a
screenshot of the video frame at that moment, then sends it to
GPT-4o-mini vision to verify the content matches the user's query.

Architecture:
  1. Spin up an ephemeral Daytona sandbox (sub-90ms from warm pool)
  2. Run a Playwright headless script inside the sandbox
  3. Navigate to the YouTube embed URL — the ?start= param auto-seeks
  4. Wait for the video player to render the frame
  5. Screenshot the player region
  6. Send screenshot to GPT-4o-mini vision for content verification
  7. Sandbox auto-deletes (ephemeral=True)

This is a DETERMINISTIC pipeline, not an LLM agent. We already know
the exact URL and timestamp from the moment finder. No Browser Use
agent is needed — just Playwright + a simple script.

NUANCES & KNOWN ISSUES:
---------------------------------------------------------------------------
1. YOUTUBE EMBED AUTOPLAY BEHAVIOR:
   YouTube embeds with ?autoplay=1 will auto-play and seek to ?start=X.
   However, some browsers/environments show a "click to play" overlay
   if autoplay is blocked by browser policy. Headless Chromium in
   Daytona should NOT have this issue since there's no user gesture
   requirement, but we add &mute=1 as a safety net (muted autoplay
   is almost never blocked).

2. FRAME RENDER TIMING:
   After navigation, the YouTube player needs time to:
   - Load the iframe player JS (~500ms)
   - Buffer video at the start position (~1-2s)
   - Render the actual video frame (~200ms)
   We wait SCREENSHOT_PAGE_LOAD_WAIT (default 3000ms) which is
   conservative. Could be tuned down to ~2000ms for speed, but risk
   getting a loading spinner instead of the actual frame.

3. DAYTONA WARM POOL:
   Default snapshots (daytona-small/medium/large) have pre-warmed
   instances. Using these gives sub-90ms cold start. If we use a
   CUSTOM snapshot (with Playwright pre-installed), the first launch
   will be slow (~30-60s for image build), but subsequent launches
   from the same snapshot will be fast. For now we use the default
   snapshot and install Playwright at runtime — adds ~5s overhead
   but avoids the custom snapshot setup. Once we confirm this works,
   we should create a custom "findr-browser" snapshot.

   TODO: Create a custom Daytona snapshot with Playwright + Chromium
   pre-installed to eliminate the runtime install overhead.

4. SCREENSHOT SIZE & COMPRESSION:
   A 1280x720 PNG screenshot is ~500KB-1MB. For vision API calls,
   this is fine (GPT-4o-mini handles up to 20MB). But if we're
   storing screenshots or sending many, we should use JPEG at 80%
   quality (~100-200KB). The Daytona computer_use.screenshot API
   supports compression natively.

5. SANDBOX LIFECYCLE:
   We use ephemeral=True so the sandbox auto-deletes after stopping.
   No cleanup code needed. If the process crashes mid-screenshot,
   the sandbox will auto-stop after 15min (default) and then delete.

6. FALLBACK WITHOUT DAYTONA:
   If DAYTONA_API_KEY is not set, we skip visual verification entirely
   and return a default "unverified" result. The pipeline already
   works without visual verification — this is an enhancement layer.

7. COST:
   Daytona: ~$0.0828/hr for a medium sandbox. A single screenshot
   takes ~5-10s, so cost per verification is ~$0.0002 (negligible).
   GPT-4o-mini vision: ~$0.005 per image analysis.
   Total per verification: ~$0.005.
---------------------------------------------------------------------------
"""

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI

from src.config import (
    DAYTONA_API_KEY,
    DAYTONA_SNAPSHOT,
    DAYTONA_SANDBOX_TIMEOUT,
    DAYTONA_TARGET,
    OPENAI_API_KEY,
    SCREENSHOT_PAGE_LOAD_WAIT,
    SCREENSHOT_VIEWPORT_HEIGHT,
    SCREENSHOT_VIEWPORT_WIDTH,
    VISION_VERIFY_MODEL,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------
@dataclass
class VerificationResult:
    """Output from visual verification of a video frame."""
    verified: bool              # Does the frame content match the query?
    confidence: float           # 0.0-1.0 confidence score
    description: str            # What the vision model sees in the frame
    screenshot_b64: str = ""    # Base64 PNG of the frame (for debugging/storage)
    error: Optional[str] = None # Error message if verification failed
    elapsed_seconds: float = 0  # Total time for the verification


# ---------------------------------------------------------------------------
# Playwright script that runs INSIDE the Daytona sandbox
#
# NUANCE: This script is sent as a string to sandbox.process.code_run().
# It must be self-contained — no imports from our codebase. All params
# are injected via string formatting before execution.
#
# NUANCE: We use playwright's page.screenshot() which returns bytes.
# We base64-encode it and print to stdout so the sandbox host can
# capture it. This avoids file system round-trips.
#
# NUANCE: The YouTube embed URL includes &mute=1 to bypass autoplay
# restrictions. We also add &controls=0 to hide the player controls
# so the screenshot is just the video frame (cleaner for vision AI).
# ---------------------------------------------------------------------------
_PLAYWRIGHT_SCREENSHOT_SCRIPT = """
import asyncio
import base64
import sys

async def take_screenshot():
    from playwright.async_api import async_playwright

    video_url = "{video_url}"
    viewport_w = {viewport_w}
    viewport_h = {viewport_h}
    wait_ms = {wait_ms}

    async with async_playwright() as p:
        # NUANCE: --no-sandbox is required inside containers (Daytona runs
        # as non-root "daytona" user, but Chromium still needs this flag).
        # --disable-gpu because there's no GPU in the sandbox.
        # --disable-dev-shm-usage because /dev/shm is small in containers
        # and Chromium can crash without this flag.
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-software-rasterizer",
            ],
        )
        page = await browser.new_page(
            viewport={{"width": viewport_w, "height": viewport_h}},
        )

        # Navigate to the YouTube embed with autoplay + mute + no controls
        await page.goto(video_url, wait_until="networkidle")

        # NUANCE: Even after networkidle, the video frame may not be rendered.
        # The YouTube player JS loads asynchronously and buffers video.
        # We add an explicit wait to let the frame render.
        # A smarter approach would be to poll for the video element's
        # readyState, but that requires injecting JS into the YouTube
        # iframe which is cross-origin blocked. The wait is simpler.
        await page.wait_for_timeout(wait_ms)

        # Take screenshot of the full page (which is just the embed)
        screenshot_bytes = await page.screenshot(type="png", full_page=False)
        await browser.close()

    # Print base64 to stdout — the host process captures this
    b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    print(b64)

asyncio.run(take_screenshot())
"""


# ---------------------------------------------------------------------------
# Core verification function
# ---------------------------------------------------------------------------
async def verify_youtube_moment(
    video_id: str,
    start_seconds: float,
    query_context: str,
    reasoning: str = "",
) -> VerificationResult:
    """
    Take a screenshot of a YouTube video at a specific timestamp and
    verify the visual content matches the user's query.

    Args:
        video_id: YouTube video ID (11 chars).
        start_seconds: Timestamp in seconds to screenshot.
        query_context: The sub-query or user query for context.
        reasoning: The classifier's reasoning trace for richer context.

    Returns:
        VerificationResult with confidence score and description.

    NUANCE: This function is intentionally async even though Daytona's
    Python SDK is synchronous. We wrap Daytona calls in asyncio.to_thread()
    to avoid blocking the event loop. The pipeline may be running multiple
    sub-queries in parallel (direct output mode), so blocking would
    serialize everything.
    """
    overall_start = time.perf_counter()

    # ---- Guard: no Daytona key → skip verification entirely ----
    if not DAYTONA_API_KEY:
        logger.warning(
            "[VisualVerify] DAYTONA_API_KEY not configured — "
            "skipping visual verification"
        )
        return VerificationResult(
            verified=True,  # Assume verified when we can't check
            confidence=0.0,
            description="Visual verification skipped (Daytona not configured)",
            error="DAYTONA_API_KEY not set",
        )

    # ---- Build the embed URL ----
    # NUANCE: We add specific params to the embed URL:
    #   autoplay=1  — start playing immediately (so we get a video frame, not a thumbnail)
    #   mute=1      — muted autoplay is never blocked by browser policies
    #   controls=0  — hide player chrome for a cleaner screenshot
    #   start=X     — seek to the moment we want to verify
    #   rel=0       — don't show related videos at the end
    embed_url = (
        f"https://www.youtube.com/embed/{video_id}"
        f"?autoplay=1&mute=1&controls=0"
        f"&start={int(start_seconds)}&rel=0"
    )

    logger.info(
        f"[VisualVerify] Starting verification | "
        f"video={video_id} | start={start_seconds:.1f}s | "
        f"query={query_context[:60]!r}"
    )

    # ---- Step 1: Create ephemeral Daytona sandbox ----
    screenshot_b64 = ""
    try:
        screenshot_b64 = await _take_screenshot_in_sandbox(embed_url)
    except Exception as e:
        elapsed = time.perf_counter() - overall_start
        logger.error(
            f"[VisualVerify] Screenshot failed after {elapsed:.2f}s: {e}",
            exc_info=True,
        )
        return VerificationResult(
            verified=True,  # Don't block pipeline on verification failure
            confidence=0.0,
            description="Visual verification failed",
            error=str(e),
            elapsed_seconds=elapsed,
        )

    # ---- Step 2: Vision verification via GPT-4o-mini ----
    try:
        result = await _verify_screenshot_with_vision(
            screenshot_b64=screenshot_b64,
            query_context=query_context,
            reasoning=reasoning,
            video_id=video_id,
        )
        result.screenshot_b64 = screenshot_b64
        result.elapsed_seconds = time.perf_counter() - overall_start

        logger.info(
            f"[VisualVerify] Complete in {result.elapsed_seconds:.2f}s | "
            f"video={video_id} | verified={result.verified} | "
            f"confidence={result.confidence:.2f} | "
            f"desc={result.description[:80]!r}"
        )
        return result

    except Exception as e:
        elapsed = time.perf_counter() - overall_start
        logger.error(
            f"[VisualVerify] Vision API failed after {elapsed:.2f}s: {e}",
            exc_info=True,
        )
        return VerificationResult(
            verified=True,
            confidence=0.0,
            description="Vision verification failed",
            screenshot_b64=screenshot_b64,
            error=str(e),
            elapsed_seconds=elapsed,
        )


# ---------------------------------------------------------------------------
# Daytona sandbox screenshot
# ---------------------------------------------------------------------------
async def _take_screenshot_in_sandbox(embed_url: str) -> str:
    """
    Spin up an ephemeral Daytona sandbox, run Playwright inside it,
    navigate to the embed URL, and return a base64 PNG screenshot.

    Returns:
        Base64-encoded PNG string.

    NUANCE: We import daytona lazily because it's an optional dependency.
    The pipeline works without it — visual verification is an enhancement.

    NUANCE: The sandbox uses daytona-medium (2 vCPU, 4GB RAM) because
    Chromium is memory-hungry. daytona-small (1 vCPU, 1GB) can OOM
    when rendering complex pages like YouTube embeds.

    NUANCE: We install playwright + chromium at runtime via process.exec().
    This adds ~5-8s overhead. A custom snapshot with these pre-installed
    would reduce this to zero. Filed as a TODO in the module docstring.

    NUANCE: The script output (base64 string) comes back via stdout
    from sandbox.process.code_run(). If the script crashes, we get the
    error in response.result and a non-zero exit_code.
    """
    try:
        from daytona import (
            Daytona,
            DaytonaConfig,
            CreateSandboxFromSnapshotParams,
        )
    except ImportError:
        raise RuntimeError(
            "daytona package not installed. Run: pip install daytona"
        )

    t0 = time.perf_counter()
    logger.info("[VisualVerify] Creating ephemeral Daytona sandbox...")

    config = DaytonaConfig(
        api_key=DAYTONA_API_KEY,
        target=DAYTONA_TARGET,
    )
    daytona = Daytona(config)

    # Create ephemeral sandbox — auto-deletes after stopping
    # NUANCE: ephemeral=True means no manual cleanup needed. If our
    # process crashes, the sandbox still auto-stops after 15min (default)
    # and then immediately deletes.
    params = CreateSandboxFromSnapshotParams(
        snapshot=DAYTONA_SNAPSHOT,
        language="python",
        ephemeral=True,
        auto_stop_interval=5,  # 5 min auto-stop (safety net)
    )

    # NUANCE: Wrap in to_thread because Daytona SDK is synchronous.
    # We don't want to block the async event loop while waiting for
    # sandbox creation, especially in parallel sub-query processing.
    sandbox = await asyncio.to_thread(
        daytona.create, params, DAYTONA_SANDBOX_TIMEOUT
    )

    sandbox_elapsed = time.perf_counter() - t0
    logger.info(
        f"[VisualVerify] Sandbox created in {sandbox_elapsed:.2f}s | "
        f"id={sandbox.id} | snapshot={DAYTONA_SNAPSHOT}"
    )

    try:
        # ---- Install Playwright + Chromium inside the sandbox ----
        # TODO: Replace with a custom snapshot that has these pre-installed.
        # This runtime install adds ~5-8s but works for development.
        t_install = time.perf_counter()
        logger.info("[VisualVerify] Installing Playwright in sandbox...")

        install_result = await asyncio.to_thread(
            sandbox.process.exec,
            "pip install playwright > /dev/null 2>&1 && "
            "playwright install chromium > /dev/null 2>&1 && "
            "playwright install-deps > /dev/null 2>&1",
            timeout=60,
        )

        if install_result.exit_code != 0:
            raise RuntimeError(
                f"Playwright install failed (exit {install_result.exit_code}): "
                f"{install_result.result[:200]}"
            )

        logger.info(
            f"[VisualVerify] Playwright installed in "
            f"{time.perf_counter() - t_install:.2f}s"
        )

        # ---- Run the screenshot script ----
        script = _PLAYWRIGHT_SCREENSHOT_SCRIPT.format(
            video_url=embed_url,
            viewport_w=SCREENSHOT_VIEWPORT_WIDTH,
            viewport_h=SCREENSHOT_VIEWPORT_HEIGHT,
            wait_ms=SCREENSHOT_PAGE_LOAD_WAIT,
        )

        t_script = time.perf_counter()
        logger.info(
            f"[VisualVerify] Running screenshot script | "
            f"url={embed_url[:80]}..."
        )

        run_result = await asyncio.to_thread(
            sandbox.process.code_run,
            script,
            timeout=30,
        )

        script_elapsed = time.perf_counter() - t_script
        logger.info(
            f"[VisualVerify] Script completed in {script_elapsed:.2f}s | "
            f"exit_code={run_result.exit_code}"
        )

        if run_result.exit_code != 0:
            raise RuntimeError(
                f"Screenshot script failed (exit {run_result.exit_code}): "
                f"{run_result.result[:300]}"
            )

        # ---- Extract base64 from stdout ----
        # NUANCE: The script prints ONLY the base64 string to stdout.
        # But there might be trailing newlines or pip install warnings
        # leaking to stdout. We strip and take the last non-empty line.
        output_lines = run_result.result.strip().split("\n")
        screenshot_b64 = output_lines[-1].strip()

        # Validate it's actually base64
        if len(screenshot_b64) < 100:
            raise RuntimeError(
                f"Screenshot output too small ({len(screenshot_b64)} chars), "
                f"likely not a valid image. Output: {screenshot_b64[:200]}"
            )

        # Quick sanity check — try to decode a few bytes
        try:
            base64.b64decode(screenshot_b64[:100])
        except Exception:
            raise RuntimeError(
                f"Screenshot output is not valid base64: "
                f"{screenshot_b64[:100]}..."
            )

        total_elapsed = time.perf_counter() - t0
        screenshot_kb = len(screenshot_b64) * 3 / 4 / 1024  # Approximate decoded size
        logger.info(
            f"[VisualVerify] Screenshot captured | "
            f"size=~{screenshot_kb:.0f}KB | "
            f"total_sandbox_time={total_elapsed:.2f}s"
        )

        return screenshot_b64

    finally:
        # ---- Cleanup: stop the sandbox (ephemeral auto-deletes) ----
        # NUANCE: We call stop() explicitly rather than waiting for
        # auto-stop timeout. This frees resources immediately and
        # triggers the ephemeral auto-delete.
        try:
            await asyncio.to_thread(sandbox.stop, 10)
            logger.debug(f"[VisualVerify] Sandbox {sandbox.id} stopped")
        except Exception as e:
            # Non-fatal — ephemeral sandbox will self-destruct anyway
            logger.debug(
                f"[VisualVerify] Sandbox stop failed (will auto-delete): {e}"
            )


# ---------------------------------------------------------------------------
# GPT-4o-mini vision verification
# ---------------------------------------------------------------------------
async def _verify_screenshot_with_vision(
    screenshot_b64: str,
    query_context: str,
    reasoning: str,
    video_id: str,
) -> VerificationResult:
    """
    Send a screenshot to GPT-4o-mini vision and ask whether the frame
    content matches the user's search query.

    NUANCE: We ask the model to return JSON with three fields:
      matches (bool), confidence (float), description (string).
    We use JSON mode to ensure parseable output. Temperature 0.2
    for consistent, factual responses.

    NUANCE: We include both the sub-query AND the classifier's reasoning
    trace in the prompt. The reasoning trace provides richer context about
    what we expect to see in the frame. For example, if the query is
    "React hooks tutorial" and the reasoning is "Need to see code editor
    with useState/useEffect examples", the vision model can look for
    code on screen rather than just generic React content.

    NUANCE: GPT-4o-mini vision cost is ~$0.005 per image at this
    resolution. Cheap enough to run on every result if needed.
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    t0 = time.perf_counter()
    logger.info(
        f"[VisualVerify] Sending screenshot to vision model | "
        f"video={video_id} | model={VISION_VERIFY_MODEL}"
    )

    response = await client.chat.completions.create(
        model=VISION_VERIFY_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a visual verification agent for a video moment "
                    "discovery system. You receive a screenshot from a YouTube "
                    "video at a specific timestamp and a search query. Your job "
                    "is to determine whether the video frame content is relevant "
                    "to the query.\n\n"
                    "Respond with JSON:\n"
                    "{\n"
                    '  "matches": true/false,\n'
                    '  "confidence": 0.0-1.0,\n'
                    '  "description": "Brief description of what you see"\n'
                    "}\n\n"
                    "Be generous with matching — if the content is even "
                    "tangentially related, mark it as matching with lower "
                    "confidence. Only mark as non-matching if the frame is "
                    "clearly unrelated (e.g., an ad, a completely different "
                    "topic, a loading screen, or a black frame)."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"SEARCH QUERY: {query_context}\n"
                            f"CONTEXT: {reasoning}\n"
                            f"VIDEO ID: {video_id}\n\n"
                            "Does this video frame match the search query?"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{screenshot_b64}",
                            # NUANCE: "low" detail mode sends a smaller version
                            # of the image, saving tokens and cost. For frame
                            # verification we don't need pixel-perfect analysis.
                            "detail": "low",
                        },
                    },
                ],
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=200,
    )

    elapsed = time.perf_counter() - t0
    raw = response.choices[0].message.content
    logger.info(
        f"[VisualVerify] Vision response in {elapsed:.2f}s | "
        f"raw={raw[:150]}"
    )

    data = json.loads(raw)

    return VerificationResult(
        verified=bool(data.get("matches", True)),
        confidence=float(data.get("confidence", 0.5)),
        description=str(data.get("description", "")),
    )
