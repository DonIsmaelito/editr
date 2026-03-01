"""
Findr Browser Use Skills Manager

Manages Browser Use skill lifecycle for TikTok and X/Twitter search.
Skills are the key abstraction here — they turn a multi-step browser
interaction into a single API call.

HOW SKILLS WORK:
  1. CREATE ($2): You describe the goal + provide a demo of one iteration.
     Browser Use records the network requests and extrapolates the pattern.
     Takes ~30 seconds. Only done ONCE per skill.
  2. EXECUTE ($0.02): Call the skill with parameters. It replays the
     learned pattern with your inputs. Fast and reliable.
  3. REFINE (free): Tweak the skill's behavior with feedback.

For Findr, we need two skills:
  - TikTok Search: "Search TikTok for Q, return top N results with metadata"
  - X/Twitter Search: "Search X for Q, return top N results with metadata"

SKILL PERSISTENCE:
---------------------------------------------------------------------------
Skills are created once and then reused forever. The skill IDs should be
stored in environment variables (TIKTOK_SEARCH_SKILL_ID, TWITTER_SEARCH_SKILL_ID)
after the first creation. If the env vars are empty, this manager creates
the skills on first use and logs the IDs so you can add them to .env.

NUANCE: We do NOT create skills on module import or app startup. Skill
creation costs $2 and takes 30s — we only want to do it when actually
needed. The create-on-first-use pattern means the first TikTok/X search
will be slow (~30s extra), but every subsequent search is fast.

NUANCE: Skills are account-scoped, not session-scoped. Once created
under your BROWSER_USE_API_KEY, they persist indefinitely. Even if
the app restarts, the skill IDs in .env point to the same skills.

NUANCE: Browser Use also has a Skills Marketplace where community
skills can be cloned. If someone has already created a TikTok search
skill, we could clone it instead of creating from scratch. We don't
use this yet but it's a possible optimization.

COST ANALYSIS:
  - Skill creation: $2 x 2 skills = $4 (one-time)
  - Per search: $0.02 (skill execution)
  - At 1000 searches/month: $20/month for TikTok/X search
  - Compare to Apify: ~$49/month for similar volume
  - Skills are cheaper AND don't need API tokens from third parties.
---------------------------------------------------------------------------
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from src.config import (
    BROWSER_USE_API_KEY,
    BROWSER_USE_MAX_STEPS,
    BROWSER_USE_MODEL,
    BROWSER_USE_OP_VAULT_ID,
    BROWSER_USE_TASK_POLL_INTERVAL,
    BROWSER_USE_TASK_TIMEOUT,
    BROWSER_USE_PROFILE_ID,
    TIKTOK_SEARCH_RESULTS,
    TIKTOK_SEARCH_SKILL_ID,
    TWITTER_AUTH_TOKEN,
    TWITTER_CT0,
    TWITTER_SEARCH_RESULTS,
    TWITTER_SEARCH_SKILL_ID,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory skill ID cache — avoids re-reading env vars on every call
# and stores skill IDs created during this process lifetime.
#
# NUANCE: This cache is per-process. In a multi-worker deployment
# (e.g., uvicorn with multiple workers), each worker has its own cache.
# This is fine because skill IDs are immutable — the worst case is
# that two workers both try to create the same skill simultaneously,
# which just wastes $2. The env var approach prevents this in production.
# ---------------------------------------------------------------------------
_skill_cache: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Lazy client initialization
# ---------------------------------------------------------------------------
_client = None


def _get_client():
    """
    Lazily initialize the Browser Use async client.

    NUANCE: We use AsyncBrowserUse (not sync BrowserUse) because the
    Findr pipeline is fully async. The Browser Use SDK has both sync
    and async variants — always use async in an async codebase to avoid
    blocking the event loop.

    NUANCE: The client uses BROWSER_USE_API_KEY from the environment.
    The SDK also accepts it via the api_key constructor param, but
    env var is more standard for server deployments.
    """
    global _client
    if _client is None:
        try:
            from browser_use_sdk import AsyncBrowserUse
        except ImportError:
            raise RuntimeError(
                "browser-use-sdk not installed. Run: pip install browser-use-sdk"
            )
        if not BROWSER_USE_API_KEY:
            raise RuntimeError(
                "BROWSER_USE_API_KEY not configured. "
                "Get one at https://cloud.browser-use.com"
            )
        _client = AsyncBrowserUse(api_key=BROWSER_USE_API_KEY)
        logger.info("[BrowserSkills] AsyncBrowserUse client initialized")
    return _client


# ---------------------------------------------------------------------------
# Skill definitions — goal + demo prompt for each platform
#
# NUANCE: The "goal" is what the skill does (WHAT).
# The "agent_prompt" is a demonstration of HOW to do it (one iteration).
# Browser Use extrapolates from the demo to handle N iterations.
#
# NUANCE: The goal must be extremely specific about the output schema.
# Vague goals produce unreliable outputs. We explicitly list every field
# we want returned and their types.
#
# NUANCE: The agent_prompt should include scrolling behavior because
# TikTok/X load results lazily. Without a scroll demo, the skill might
# only capture the first few results visible in the viewport.
# ---------------------------------------------------------------------------

TIKTOK_SKILL_DEFINITION = {
    "goal": (
        "Search TikTok for a given query Q and return the top N video results. "
        "For each result, return a JSON object with these fields: "
        "title (string, the video caption/description), "
        "creator (string, the @username), "
        "views (string, e.g. '1.2M'), "
        "likes (string, e.g. '45K'), "
        "url (string, the direct video URL like https://www.tiktok.com/@user/video/ID), "
        "hashtags (list of strings). "
        "Q and N are input parameters. "
        "Return results as a JSON array."
    ),
    "agent_prompt": (
        "Go to https://www.tiktok.com/search?q=example. "
        "Wait for the search results page to fully load. "
        "Scroll down slowly to load more results. "
        "For each video result visible, extract the caption, creator username, "
        "view count, like count, video URL, and any hashtags shown. "
        "Scroll down again to load additional results if needed. "
        "Continue until you have enough results or no more load."
    ),
}

TWITTER_SKILL_DEFINITION = {
    "goal": (
        "Search X (Twitter) for a given query Q and return the top N post results. "
        "For each result, return a JSON object with these fields: "
        "author (string, the @handle), "
        "display_name (string, the user's display name), "
        "text (string, the full post text), "
        "url (string, the direct post URL like https://x.com/user/status/ID), "
        "has_video (boolean, whether the post contains a video), "
        "has_image (boolean, whether the post contains an image), "
        "likes (string, engagement count), "
        "retweets (string, repost count), "
        "timestamp (string, when the post was made). "
        "Q and N are input parameters. "
        "Return results as a JSON array."
    ),
    "agent_prompt": (
        "Go to https://x.com/search?q=example&src=typed_query. "
        "Wait for the search results to fully load. "
        "Switch to the 'Latest' tab if available (for recency). "
        "For each post visible, extract the author handle, display name, "
        "full post text, direct URL, whether it has video/image media, "
        "engagement counts, and timestamp. "
        "Scroll down to load more results. "
        "Continue until you have enough results."
    ),
}


# ---------------------------------------------------------------------------
# Skill creation + caching
# ---------------------------------------------------------------------------
async def _ensure_skill(
    platform: str,
    skill_definition: Dict[str, str],
    env_skill_id: str,
) -> str:
    """
    Ensure a skill exists for the given platform. Check in order:
      1. In-memory cache
      2. Environment variable
      3. Create new skill (costs $2, takes ~30s)

    Returns the skill ID.

    NUANCE: Skill creation is idempotent in terms of functionality
    (creating the same skill twice just gives you two skills that do
    the same thing), but it costs $2 each time. We guard against
    accidental double-creation with the cache + env var check.

    NUANCE: If creation fails (API error, insufficient balance), we
    raise immediately. The caller (search service) should handle this
    and fall back to alternative search methods.
    """
    # 1. Check in-memory cache
    if platform in _skill_cache:
        logger.debug(f"[BrowserSkills] Skill cache hit for {platform}")
        return _skill_cache[platform]

    # 2. Check environment variable
    if env_skill_id:
        logger.info(
            f"[BrowserSkills] Using {platform} skill from env: "
            f"{env_skill_id[:20]}..."
        )
        _skill_cache[platform] = env_skill_id
        return env_skill_id

    # 3. Create new skill
    client = _get_client()

    logger.info(
        f"[BrowserSkills] Creating {platform} skill "
        f"(one-time, ~30s, costs $2)..."
    )
    t0 = time.perf_counter()

    skill = await client.skills.create(
        goal=skill_definition["goal"],
        agent_prompt=skill_definition["agent_prompt"],
    )

    elapsed = time.perf_counter() - t0
    skill_id = str(skill.id)

    logger.info(
        f"[BrowserSkills] {platform} skill created in {elapsed:.1f}s | "
        f"id={skill_id}\n"
        f"  >>> ADD TO .env: {platform.upper()}_SEARCH_SKILL_ID={skill_id}"
    )

    # The Browser Use API returns 202 Accepted immediately — the skill
    # takes ~30s to actually be ready. Wait for it to become executable.
    if elapsed < 10:
        wait_time = 35
        logger.info(
            f"[BrowserSkills] Skill creation was async (returned in {elapsed:.1f}s). "
            f"Waiting {wait_time}s for skill to become ready..."
        )
        await asyncio.sleep(wait_time)
        logger.info(f"[BrowserSkills] Wait complete, skill should be ready now")

    _skill_cache[platform] = skill_id
    return skill_id


async def get_tiktok_skill_id() -> str:
    """Get or create the TikTok search skill."""
    return await _ensure_skill(
        platform="tiktok",
        skill_definition=TIKTOK_SKILL_DEFINITION,
        env_skill_id=TIKTOK_SEARCH_SKILL_ID,
    )


async def get_twitter_skill_id() -> str:
    """Get or create the X/Twitter search skill."""
    return await _ensure_skill(
        platform="twitter",
        skill_definition=TWITTER_SKILL_DEFINITION,
        env_skill_id=TWITTER_SEARCH_SKILL_ID,
    )


# ---------------------------------------------------------------------------
# Skill execution
# ---------------------------------------------------------------------------
async def execute_skill(
    skill_id: str,
    parameters: Dict[str, Any],
    platform: str = "unknown",
) -> Optional[List[Dict[str, Any]]]:
    """
    Execute a Browser Use skill and return the parsed results.

    Args:
        skill_id: The Browser Use skill ID.
        parameters: Input params (e.g., {"Q": "query", "N": 5}).
        platform: Platform name for logging.

    Returns:
        List of result dicts, or None on failure.

    NUANCE: The skill execution is an async API call to Browser Use Cloud.
    The agent runs on Browser Use's infrastructure (co-located with the
    browser for minimal latency). We just wait for the result.

    NUANCE: Skill executions cost $0.02 each regardless of how many
    steps the agent takes internally. This is a fixed cost, not per-step.

    NUANCE: The result comes back as a TaskResult object. The actual
    data we want is in result.output, which should be a JSON string
    or structured data matching our skill's goal description.

    NUANCE: Skill executions can fail if:
      - The target website is down or has changed its layout
      - Anti-bot detection blocks the agent
      - The skill needs refinement (layout changed since creation)
      - Browser Use Cloud is experiencing issues
    We handle all of these by returning None and letting the caller
    decide how to proceed (fallback search, retry, etc.).
    """
    client = _get_client()
    skill_id = str(skill_id)  # Ensure string (skill.id may be UUID object)

    t0 = time.perf_counter()
    logger.info(
        f"[BrowserSkills] Executing {platform} skill | "
        f"skill_id={skill_id[:20]}... | "
        f"params={parameters}"
    )

    def _build_parameter_variants(
        original: Dict[str, Any],
        error_text: str,
    ) -> List[Dict[str, Any]]:
        """
        Build fallback parameter payloads for skills with different schemas.

        Why this exists:
          Some deployed skills were trained with keys like Q/N while others
          require query/max_results. We retry with alternate key names when
          Browser Use reports missing required parameters.
        """
        match = re.search(
            r"missing required parameters:\s*(.+)$",
            error_text,
            flags=re.IGNORECASE,
        )
        if not match:
            return []

        required_raw = match.group(1)
        required_keys = [
            key.strip().strip("`'\"")
            for key in required_raw.split(",")
            if key.strip()
        ]
        if not required_keys:
            return []

        query_value = (
            original.get("query")
            or original.get("Q")
            or original.get("q")
        )
        limit_value = (
            original.get("max_results")
            or original.get("limit")
            or original.get("N")
            or original.get("n")
        )

        # First, satisfy exactly what the API says is required.
        exact_match_payload: Dict[str, Any] = {}
        for key in required_keys:
            lowered = key.lower()
            if lowered in {"query", "q"} and query_value is not None:
                exact_match_payload[key] = query_value
            elif lowered in {"n", "limit", "max_results"} and limit_value is not None:
                exact_match_payload[key] = limit_value
            elif lowered in {"auth_token", "authtoken"} and TWITTER_AUTH_TOKEN:
                exact_match_payload[key] = TWITTER_AUTH_TOKEN
            elif lowered == "ct0" and TWITTER_CT0:
                exact_match_payload[key] = TWITTER_CT0

        variants: List[Dict[str, Any]] = []
        if exact_match_payload and exact_match_payload != original:
            variants.append(exact_match_payload)

        # Then try common schema variants observed across skill revisions.
        if query_value is not None and limit_value is not None:
            variants.extend([
                {"query": query_value, "max_results": limit_value},
                {"query": query_value, "limit": limit_value},
                {"query": query_value, "n": limit_value},
                {"Q": query_value, "N": limit_value},
            ])
            if TWITTER_AUTH_TOKEN and TWITTER_CT0:
                variants.extend([
                    {
                        "query": query_value,
                        "limit": limit_value,
                        "auth_token": TWITTER_AUTH_TOKEN,
                        "ct0": TWITTER_CT0,
                    },
                    {
                        "query": query_value,
                        "auth_token": TWITTER_AUTH_TOKEN,
                        "ct0": TWITTER_CT0,
                    },
                ])

        deduped: List[Dict[str, Any]] = []
        for candidate in variants:
            if candidate not in deduped and candidate != original:
                deduped.append(candidate)
        return deduped

    def _is_transient_failure_text(text: str) -> bool:
        """
        Heuristic for flaky Browser Use / upstream network failures worth retrying.
        """
        if not text:
            return False
        lowered = text.lower()
        transient_markers = (
            "remotedisconnected",
            "connection aborted",
            "connection reset",
            "timed out",
            "timeout",
            "temporary",
            "temporarily",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "econnreset",
            "etimedout",
        )
        return any(marker in lowered for marker in transient_markers)

    def _result_to_payload(result_obj: Any) -> Dict[str, Any]:
        if hasattr(result_obj, "model_dump"):
            return result_obj.model_dump()
        if hasattr(result_obj, "__dict__"):
            return dict(result_obj.__dict__)
        return {}

    # Retry logic for "Skill is not finished or not enabled" errors.
    # This can happen if the skill was just created and isn't ready yet.
    max_retries = 3
    retry_delay = 15  # seconds between retries

    try:
        async def _create_profile_session_id() -> Optional[str]:
            if not BROWSER_USE_PROFILE_ID:
                return None
            try:
                session = await client.sessions.create(
                    profile_id=BROWSER_USE_PROFILE_ID,
                    keep_alive=False,
                )
                sid = str(session.id)
                logger.info(
                    f"[BrowserSkills] Using Browser Use profile session "
                    f"{sid[:12]}... for {platform}"
                )
                return sid
            except Exception as session_err:
                logger.warning(
                    f"[BrowserSkills] Failed to create profile session for {platform}: "
                    f"{session_err}"
                )
                return None

        session_id = await _create_profile_session_id()

        async def _execute_with_retries(params: Dict[str, Any]):
            for attempt in range(max_retries + 1):
                try:
                    execute_kwargs: Dict[str, Any] = {"parameters": params}
                    if session_id:
                        execute_kwargs["session_id"] = session_id
                    return await client.skills.execute(skill_id, **execute_kwargs)
                except Exception as exec_err:
                    if "not finished" in str(exec_err).lower() and attempt < max_retries:
                        logger.warning(
                            f"[BrowserSkills] {platform} skill not ready yet "
                            f"(attempt {attempt + 1}/{max_retries + 1}), "
                            f"retrying in {retry_delay}s..."
                        )
                        await asyncio.sleep(retry_delay)
                        continue
                    raise

        result = None
        last_error: Optional[Exception] = None
        executed_params: Dict[str, Any] = parameters

        parameter_attempts = [parameters]
        attempted_payloads: List[Dict[str, Any]] = []

        while parameter_attempts:
            current_params = parameter_attempts.pop(0)
            attempted_payloads.append(current_params)
            try:
                result = await _execute_with_retries(current_params)
                executed_params = current_params
                break
            except Exception as exec_err:
                last_error = exec_err
                error_text = str(exec_err)
                fallback_variants = _build_parameter_variants(current_params, error_text)

                # Queue untried fallback variants.
                for variant in fallback_variants:
                    if variant not in attempted_payloads and variant not in parameter_attempts:
                        parameter_attempts.append(variant)

                if parameter_attempts:
                    logger.warning(
                        f"[BrowserSkills] {platform} skill rejected params {current_params}; "
                        f"retrying with alternate schema"
                    )
                    continue

                raise last_error

        if result is None:
            if last_error:
                raise last_error
            return None

        # Normalize response shape across browser_use_sdk versions.
        # Older/newer SDKs differ:
        #   - Some return TaskResult-like objects with .status and .output
        #   - Current v2 model returns ExecuteSkillResponse with
        #     .success, .result, .error, .stderr, .latency_ms
        result_payload = _result_to_payload(result)

        success = result_payload.get("success")
        status = result_payload.get("status")
        if status is None:
            status = "success" if success is True else "failed" if success is False else "unknown"

        elapsed = time.perf_counter() - t0
        logger.info(
            f"[BrowserSkills] {platform} skill executed in {elapsed:.2f}s | "
            f"status={status}"
        )

        # If SDK gives explicit failure, retry transient errors before giving up.
        if success is False:
            transient_retries = 2
            for retry_idx in range(transient_retries + 1):
                nested_result = result_payload.get("result")
                nested_error = None
                if isinstance(nested_result, dict):
                    nested_error = nested_result.get("error")
                nested_error_message = None
                if isinstance(nested_error, dict):
                    nested_error_message = nested_error.get("message")
                elif isinstance(nested_error, str):
                    nested_error_message = nested_error

                err = (
                    result_payload.get("error")
                    or nested_error_message
                    or "Skill execution failed"
                )
                stderr = result_payload.get("stderr")
                failure_text = f"{err} {stderr or ''}"

                if retry_idx < transient_retries and _is_transient_failure_text(failure_text):
                    wait_seconds = 2 * (retry_idx + 1)
                    logger.warning(
                        f"[BrowserSkills] {platform} transient skill failure: {err} "
                        f"| retrying ({retry_idx + 1}/{transient_retries}) in {wait_seconds}s"
                    )
                    await asyncio.sleep(wait_seconds)
                    try:
                        retry_result = await _execute_with_retries(executed_params)
                    except Exception as retry_err:
                        retry_text = str(retry_err)
                        if retry_idx < transient_retries and _is_transient_failure_text(retry_text):
                            logger.warning(
                                f"[BrowserSkills] {platform} transient execute exception: "
                                f"{retry_text}"
                            )
                            continue
                        logger.error(
                            f"[BrowserSkills] {platform} retry execution failed: {retry_err}",
                            exc_info=True,
                        )
                        return None

                    result_payload = _result_to_payload(retry_result)
                    success = result_payload.get("success")
                    status = result_payload.get("status")
                    if status is None:
                        status = (
                            "success" if success is True
                            else "failed" if success is False
                            else "unknown"
                        )
                    logger.info(
                        f"[BrowserSkills] {platform} retry completed | status={status}"
                    )
                    if success is not False:
                        break
                    continue

                if isinstance(nested_error, dict) and nested_error.get("code"):
                    logger.error(
                        f"[BrowserSkills] {platform} skill error code: "
                        f"{nested_error.get('code')}"
                    )
                if stderr:
                    logger.error(f"[BrowserSkills] {platform} skill stderr: {stderr[:500]}")
                logger.error(f"[BrowserSkills] {platform} skill returned failure: {err}")
                return None

        # NUANCE: result.output could be a string (JSON), a list, or a dict
        # depending on how the skill was defined and what the agent returned.
        # We normalize to a list of dicts.
        output = (
            result_payload.get("output")
            if "output" in result_payload
            else result_payload.get("result")
        )

        # Last-resort fallback for unexpected wrappers.
        if output is None and result_payload:
            for key in ("data", "items", "results"):
                candidate = result_payload.get(key)
                if candidate is not None:
                    output = candidate
                    break

        if output is None:
            logger.warning(
                f"[BrowserSkills] {platform} skill returned no output payload"
            )
            return None

        if isinstance(output, str):
            import json
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                logger.error(
                    f"[BrowserSkills] {platform} skill returned non-JSON output: "
                    f"{output[:200]}"
                )
                return None

        if isinstance(output, dict):
            # Sometimes wrapped in a top-level key like {"results": [...]}
            # Try common patterns
            for key in ("results", "data", "items", "posts", "videos"):
                if key in output and isinstance(output[key], list):
                    output = output[key]
                    break
            else:
                # Single result dict — wrap in list
                output = [output]

        if not isinstance(output, list):
            logger.warning(
                f"[BrowserSkills] {platform} skill returned unexpected type: "
                f"{type(output).__name__}"
            )
            return None

        logger.info(
            f"[BrowserSkills] {platform} skill returned {len(output)} results"
        )
        return output

    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error(
            f"[BrowserSkills] {platform} skill execution failed "
            f"after {elapsed:.2f}s: {e}",
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Convenience wrappers for each platform
# ---------------------------------------------------------------------------

async def search_tiktok(
    query: str,
    max_results: int = TIKTOK_SEARCH_RESULTS,
) -> Optional[List[Dict[str, Any]]]:
    """
    Search TikTok using Browser Use skill.

    NUANCE: TikTok is notoriously aggressive with anti-bot detection.
    Browser Use Cloud handles this (fingerprinting, CAPTCHA solving),
    but searches can still fail occasionally. The skill approach is
    more reliable than raw Playwright because the skill "learned" the
    correct interaction pattern during creation.

    NUANCE: TikTok results don't have traditional timestamps like
    YouTube. Videos are short (15s-3min) and don't support URL-based
    seeking. For Findr, TikTok results are always "direct" format —
    we embed the full video, not a specific moment within it.
    """
    if not BROWSER_USE_API_KEY:
        logger.warning(
            "[BrowserSkills] BROWSER_USE_API_KEY not configured — "
            "cannot search TikTok"
        )
        return None

    # Skills are intentionally bypassed in naive mode.
    logger.info("[BrowserSkills] TikTok skill path disabled — using task mode")
    return await _search_tiktok_via_task(query=query, max_results=max_results)


async def search_twitter(
    query: str,
    max_results: int = TWITTER_SEARCH_RESULTS,
) -> Optional[List[Dict[str, Any]]]:
    """
    Search X/Twitter using Browser Use skill.

    NUANCE: X/Twitter search has multiple tabs (Top, Latest, People,
    Media, Lists). Our skill targets the "Latest" tab for recency,
    which matters for queries like "what did Elon say about AI today."

    NUANCE: X posts can contain video, images, or just text. For Findr,
    we're primarily interested in posts with video content, but text-only
    posts can still be relevant (e.g., commentary with embedded video
    from another user). The search service layer handles this filtering.

    NUANCE: X/Twitter embeds use react-tweet on the frontend — they
    are NOT iframes. The embed URL (x.com/i/status/{id}) is used by
    the frontend component to fetch and render the post natively.
    This means no timestamp support, just the full post.
    """
    if not BROWSER_USE_API_KEY:
        logger.warning(
            "[BrowserSkills] BROWSER_USE_API_KEY not configured — "
            "cannot search X/Twitter"
        )
        return None

    # Skills are intentionally bypassed in naive mode.
    logger.info("[BrowserSkills] X skill path disabled — using task mode")
    return await _search_twitter_via_task(query=query, max_results=max_results)


def _extract_json_payload(text: str) -> Optional[Any]:
    """
    Extract JSON from raw agent output text.
    Supports:
      - Plain JSON string
      - ```json fenced blocks
      - Text with first JSON object/array embedded
    """
    if not text:
        return None

    text = text.strip()
    if not text:
        return None

    # 1) Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) Markdown fenced JSON block
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 3) First JSON array/object in free text
    obj_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if obj_match:
        candidate = obj_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    return None


async def _search_tiktok_via_task(
    query: str,
    max_results: int,
) -> Optional[List[Dict[str, Any]]]:
    """
    Fallback TikTok search path using Browser Use Tasks API.
    """
    client = _get_client()
    t0 = time.perf_counter()
    task_id: Optional[str] = None
    task_prompt = (
        f"Search TikTok for: '{query}'. "
        "If a login wall/modal appears, authenticate first using available 1Password "
        "vault credentials, then continue to the Videos results. "
        f"Return ONLY valid JSON as an object with keys 'results' and 'count'. "
        f"'results' must be an array of up to {max_results} objects with keys: "
        "title, creator, views, likes, url, hashtags. "
        "If blocked by login or no results, return {\"results\": [], \"count\": 0, "
        "\"error\": \"brief reason\"}."
    )

    try:
        session_id: Optional[str] = None
        if BROWSER_USE_PROFILE_ID:
            try:
                session = await client.sessions.create(
                    profile_id=BROWSER_USE_PROFILE_ID,
                    keep_alive=False,
                )
                session_id = str(session.id)
                logger.info(
                    f"[BrowserSkills] TikTok fallback using profile session "
                    f"{session_id[:12]}..."
                )
            except Exception as session_err:
                logger.warning(
                    f"[BrowserSkills] Could not create profile session for TikTok fallback: "
                    f"{session_err}"
                )

        created = await client.tasks.create(
            task_prompt,
            llm=BROWSER_USE_MODEL,
            max_steps=max(BROWSER_USE_MAX_STEPS, 12),
            allowed_domains=["tiktok.com", "www.tiktok.com", "vm.tiktok.com"],
            session_id=session_id,
            op_vault_id=BROWSER_USE_OP_VAULT_ID or None,
        )
        task_id = str(created.id)
        logger.info(
            f"[BrowserSkills] TikTok task fallback started | task_id={task_id} | "
            f"op_vault={'yes' if bool(BROWSER_USE_OP_VAULT_ID) else 'no'} | "
            f"profile={'yes' if bool(session_id) else 'no'}"
        )

        done = await client.tasks.wait(
            task_id,
            timeout=BROWSER_USE_TASK_TIMEOUT,
            interval=BROWSER_USE_TASK_POLL_INTERVAL,
        )
        task_payload = done.model_dump() if hasattr(done, "model_dump") else done.__dict__
        output_raw = task_payload.get("output")

        if not output_raw:
            logger.warning(
                "[BrowserSkills] TikTok task fallback returned empty output "
                f"| is_success={task_payload.get('is_success')}"
            )
            return None

        parsed = _extract_json_payload(output_raw)
        if parsed is None:
            logger.warning(
                "[BrowserSkills] TikTok task fallback output was not JSON parseable"
            )
            return None

        # Normalize to list of objects.
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            items = (
                parsed.get("results")
                or parsed.get("items")
                or parsed.get("data")
                or []
            )
            if not items and parsed.get("error"):
                logger.warning(
                    "[BrowserSkills] TikTok task fallback reported: "
                    f"{str(parsed.get('error'))[:300]}"
                )
        else:
            return None

        if not isinstance(items, list):
            return None

        elapsed = time.perf_counter() - t0
        logger.info(
            f"[BrowserSkills] TikTok task fallback completed in {elapsed:.2f}s | "
            f"results={len(items)}"
        )
        return items

    except TimeoutError:
        elapsed = time.perf_counter() - t0
        logger.warning(
            f"[BrowserSkills] TikTok task fallback timed out after {elapsed:.2f}s "
            f"(timeout={BROWSER_USE_TASK_TIMEOUT}s)"
        )
        if task_id:
            try:
                await client.tasks.stop_task_and_session(task_id)
                logger.info(
                    f"[BrowserSkills] TikTok timed-out task stopped | task_id={task_id}"
                )
            except Exception as stop_err:
                logger.debug(
                    f"[BrowserSkills] Could not stop timed-out TikTok task: {stop_err}"
                )
        return None
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error(
            f"[BrowserSkills] TikTok task fallback failed after {elapsed:.2f}s: {e}",
            exc_info=True,
        )
        return None


async def _search_twitter_via_task(
    query: str,
    max_results: int,
) -> Optional[List[Dict[str, Any]]]:
    """
    Naive X/Twitter search using Browser Use Tasks API.
    """
    client = _get_client()
    t0 = time.perf_counter()
    task_id: Optional[str] = None
    task_prompt = (
        f"Search X (Twitter) for: '{query}'. "
        "If login/auth prompts appear, authenticate first using available 1Password "
        "vault credentials, then continue with search. "
        f"Return ONLY valid JSON as an object with keys 'results' and 'count'. "
        f"'results' must be an array of up to {max_results} objects with keys: "
        "author, display_name, text, url, has_video, has_image, likes, retweets, timestamp. "
        "Prefer the Latest tab when available. "
        "If blocked by login or no results, return "
        "{\"results\": [], \"count\": 0, \"error\": \"brief reason\"}."
    )

    try:
        session_id: Optional[str] = None
        if BROWSER_USE_PROFILE_ID:
            try:
                session = await client.sessions.create(
                    profile_id=BROWSER_USE_PROFILE_ID,
                    keep_alive=False,
                )
                session_id = str(session.id)
                logger.info(
                    f"[BrowserSkills] X task using profile session "
                    f"{session_id[:12]}..."
                )
            except Exception as session_err:
                logger.warning(
                    f"[BrowserSkills] Could not create profile session for X task: "
                    f"{session_err}"
                )

        secrets: Optional[Dict[str, str]] = None
        if TWITTER_AUTH_TOKEN and TWITTER_CT0:
            secrets = {
                "auth_token": TWITTER_AUTH_TOKEN,
                "ct0": TWITTER_CT0,
            }

        created = await client.tasks.create(
            task_prompt,
            llm=BROWSER_USE_MODEL,
            max_steps=max(BROWSER_USE_MAX_STEPS, 12),
            allowed_domains=["x.com", "twitter.com", "www.x.com", "www.twitter.com"],
            session_id=session_id,
            secrets=secrets,
            op_vault_id=BROWSER_USE_OP_VAULT_ID or None,
        )
        task_id = str(created.id)
        logger.info(
            f"[BrowserSkills] X task started | task_id={task_id} | "
            f"op_vault={'yes' if bool(BROWSER_USE_OP_VAULT_ID) else 'no'} | "
            f"profile={'yes' if bool(session_id) else 'no'}"
        )

        done = await client.tasks.wait(
            task_id,
            timeout=BROWSER_USE_TASK_TIMEOUT,
            interval=BROWSER_USE_TASK_POLL_INTERVAL,
        )
        task_payload = done.model_dump() if hasattr(done, "model_dump") else done.__dict__
        output_raw = task_payload.get("output")

        if not output_raw:
            logger.warning(
                "[BrowserSkills] X task returned empty output "
                f"| is_success={task_payload.get('is_success')}"
            )
            return None

        parsed = _extract_json_payload(output_raw)
        if parsed is None:
            logger.warning(
                "[BrowserSkills] X task output was not JSON parseable"
            )
            return None

        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            items = (
                parsed.get("results")
                or parsed.get("items")
                or parsed.get("data")
                or []
            )
            if not items and parsed.get("error"):
                logger.warning(
                    "[BrowserSkills] X task reported: "
                    f"{str(parsed.get('error'))[:300]}"
                )
        else:
            return None

        if not isinstance(items, list):
            return None

        elapsed = time.perf_counter() - t0
        logger.info(
            f"[BrowserSkills] X task completed in {elapsed:.2f}s | "
            f"results={len(items)}"
        )
        return items

    except TimeoutError:
        elapsed = time.perf_counter() - t0
        logger.warning(
            f"[BrowserSkills] X task timed out after {elapsed:.2f}s "
            f"(timeout={BROWSER_USE_TASK_TIMEOUT}s)"
        )
        if task_id:
            try:
                await client.tasks.stop_task_and_session(task_id)
                logger.info(
                    f"[BrowserSkills] X timed-out task stopped | task_id={task_id}"
                )
            except Exception as stop_err:
                logger.debug(
                    f"[BrowserSkills] Could not stop timed-out X task: {stop_err}"
                )
        return None
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error(
            f"[BrowserSkills] X task failed after {elapsed:.2f}s: {e}",
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Skill management utilities
# ---------------------------------------------------------------------------

async def refine_skill(
    skill_id: str,
    feedback: str,
    platform: str = "unknown",
) -> bool:
    """
    Refine an existing skill with feedback. Free of charge.

    Use this when a skill's output quality degrades (e.g., TikTok
    changed their layout and the skill is extracting wrong fields).

    NUANCE: Refinement modifies the skill in-place. If the refinement
    makes things worse, you can rollback with rollback_skill().

    NUANCE: Refinement is free but takes ~30s (same as creation).
    Don't call this in the hot path — it's an admin/maintenance operation.
    """
    client = _get_client()

    skill_id = str(skill_id)
    logger.info(
        f"[BrowserSkills] Refining {platform} skill | "
        f"id={skill_id[:20]}... | feedback={feedback[:80]!r}"
    )
    t0 = time.perf_counter()

    try:
        await client.skills.refine(skill_id, feedback=feedback)
        elapsed = time.perf_counter() - t0
        logger.info(
            f"[BrowserSkills] {platform} skill refined in {elapsed:.1f}s"
        )
        return True
    except Exception as e:
        logger.error(f"[BrowserSkills] Refinement failed: {e}")
        return False


async def rollback_skill(skill_id: str, platform: str = "unknown") -> bool:
    """
    Rollback the last refinement on a skill.

    NUANCE: Only the most recent refinement can be rolled back.
    Multiple rollbacks don't stack — you can only go back one step.
    """
    client = _get_client()
    skill_id = str(skill_id)
    logger.info(f"[BrowserSkills] Rolling back {platform} skill {skill_id[:20]}...")

    try:
        await client.skills.rollback(skill_id)
        logger.info(f"[BrowserSkills] {platform} skill rolled back")
        return True
    except Exception as e:
        logger.error(f"[BrowserSkills] Rollback failed: {e}")
        return False
