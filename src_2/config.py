"""
Editr Configuration

Environment variables and constants for the Editr pipeline.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# API Keys — Google Cloud
# ---------------------------------------------------------------------------
GOOGLE_CLOUD_API_KEY = os.getenv("GOOGLE_CLOUD_API_KEY", "")

# ---------------------------------------------------------------------------
# Google Cloud Storage
# ---------------------------------------------------------------------------
GCS_BUCKET = os.getenv("GCS_BUCKET", "viralfix-renders")

# ---------------------------------------------------------------------------
# Convex
# ---------------------------------------------------------------------------
CONVEX_URL = os.getenv("CONVEX_URL", "")

# ---------------------------------------------------------------------------
# Daytona — Sandboxes for video processing
# ---------------------------------------------------------------------------
DAYTONA_API_KEY = os.getenv("DAYTONA_API_KEY", "")
DAYTONA_TARGET = os.getenv("DAYTONA_TARGET", "us")
DAYTONA_EDITOR_SNAPSHOT = os.getenv("DAYTONA_EDITOR_SNAPSHOT", "editr")
DAYTONA_SANDBOX_TIMEOUT = 120

# ---------------------------------------------------------------------------
# Browser Use — Profile scraping
# ---------------------------------------------------------------------------
BROWSER_USE_API_KEY = os.getenv("BROWSER_USE_API_KEY", "")
BROWSER_USE_PROFILE_ID = os.getenv("BROWSER_USE_PROFILE_ID", "")
TIKTOK_PROFILE_SKILL_ID = os.getenv("TIKTOK_PROFILE_SKILL_ID", "")

# ---------------------------------------------------------------------------
# Pipeline Defaults
# ---------------------------------------------------------------------------
MAX_VIDEOS_DEFAULT = 3
MAX_VIDEO_DURATION = 150          # seconds — skip videos longer than 2.5 min
MAX_PARALLEL_SANDBOXES = 3
MIN_ENGAGEMENT_RATE = 0.001       # below this = bad topic, skip

# ---------------------------------------------------------------------------
# Gemini Models (correct IDs from official docs)
# ---------------------------------------------------------------------------
GEMINI_MODEL = "gemini-3.1-pro-preview"              # Most capable, $2/M tokens
GEMINI_ANALYSIS_MODEL = "gemini-3-flash-preview"      # Cheaper for 4 parallel agents, $0.50/M tokens

# ---------------------------------------------------------------------------
# Asset Generation (correct IDs from official docs)
# ---------------------------------------------------------------------------
NANO_BANANA_MODEL = "gemini-3.1-flash-image-preview"  # Image gen via generate_content + image_config
LYRIA_MODEL = "lyria-realtime-exp"                     # Music gen, outputs raw 16-bit PCM @ 48kHz stereo
MUSIC_SEGMENT_DURATION = 15                            # shorter seed segment, looped locally for speed
LONG_VIDEO_OVERLAY_THRESHOLD = 45                      # seconds
LONG_VIDEO_MAX_OVERLAYS = 3
