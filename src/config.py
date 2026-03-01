"""
Findr Configuration

All environment variables and constants for the Findr pipeline.
"""

import os

from dotenv import load_dotenv

load_dotenv()  # Load .env from project root

# ---------------------------------------------------------------------------
# API Keys — Core
# ---------------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")

# ---------------------------------------------------------------------------
# API Keys — Browser Infrastructure
# ---------------------------------------------------------------------------
DAYTONA_API_KEY = os.getenv("DAYTONA_API_KEY", "")
DAYTONA_TARGET = os.getenv("DAYTONA_TARGET", "us")  # "us" or "eu"
BROWSER_USE_API_KEY = os.getenv("BROWSER_USE_API_KEY", "")
BROWSER_USE_PROFILE_ID = os.getenv("BROWSER_USE_PROFILE_ID", "")
BROWSER_USE_OP_VAULT_ID = os.getenv("BROWSER_USE_OP_VAULT_ID", "")

# ---------------------------------------------------------------------------
# Convex
# ---------------------------------------------------------------------------
CONVEX_URL = os.getenv("CONVEX_URL", "")

# ---------------------------------------------------------------------------
# Search Defaults
# ---------------------------------------------------------------------------
MAX_SEARCH_RESULTS = 5           # Top N videos from platform search per sub-query
MAX_VIDEO_DURATION = 1800        # 30 min cap — skip longer videos for speed
SEGMENT_DURATION_SEC = 300       # 5-minute transcript segments for embedding
TOP_SEGMENTS_AFTER_FILTER = 2    # Keep top N segments from vector similarity

# ---------------------------------------------------------------------------
# LLM Models
# ---------------------------------------------------------------------------
CLASSIFIER_MODEL = "gpt-4o"          # Strong reasoning for classification
MOMENT_FINDER_MODEL = "gpt-4o-mini"  # Fast + cheap for transcript scanning
VISION_VERIFY_MODEL = "gpt-4o-mini"  # Vision model for screenshot verification
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536

# ---------------------------------------------------------------------------
# Output Format Constants
# ---------------------------------------------------------------------------
OUTPUT_STRUCTURED = "structured"  # Collapsable, sequential, learning-oriented
OUTPUT_DIRECT = "direct"          # Simple embeds, single/few results

# ---------------------------------------------------------------------------
# Daytona Sandbox Defaults
# ---------------------------------------------------------------------------
DAYTONA_SNAPSHOT = "daytona-medium"  # 2 vCPU, 4 GB RAM — enough for headless Chromium
DAYTONA_SANDBOX_TIMEOUT = 60        # Max seconds to wait for sandbox creation
SCREENSHOT_PAGE_LOAD_WAIT = 3000    # ms to wait after navigating before screenshotting
SCREENSHOT_VIEWPORT_WIDTH = 1280
SCREENSHOT_VIEWPORT_HEIGHT = 720

# ---------------------------------------------------------------------------
# Browser Use Defaults
# ---------------------------------------------------------------------------
BROWSER_USE_MODEL = "browser-use-2.0"  # ~3s/step, $0.006/step — fastest option
BROWSER_USE_MAX_STEPS = 15             # Cap agent steps to control cost + time
BROWSER_USE_TASK_TIMEOUT = 75          # Hard timeout for task-mode fallback
BROWSER_USE_TASK_POLL_INTERVAL = 2     # Seconds between task status polls
TIKTOK_SEARCH_RESULTS = 5             # Top N TikTok videos per search
TWITTER_SEARCH_RESULTS = 5            # Top N X/Twitter posts per search

# ---------------------------------------------------------------------------
# Browser Use Skill IDs (populated after first creation, then reused)
#
# NUANCE: Skills are created once ($2 each) and then executed ($0.02/call).
# After creating a skill, store its ID here or in .env so we never re-create.
# If these are empty, the skill manager will create them on first use.
# ---------------------------------------------------------------------------
TIKTOK_SEARCH_SKILL_ID = os.getenv("TIKTOK_SEARCH_SKILL_ID", "")
TWITTER_SEARCH_SKILL_ID = os.getenv("TWITTER_SEARCH_SKILL_ID", "")

# Optional X cookie values for skills that require authenticated search.
# These correspond to x.com cookies.
TWITTER_AUTH_TOKEN = os.getenv("TWITTER_AUTH_TOKEN", "")
TWITTER_CT0 = os.getenv("TWITTER_CT0", "")
