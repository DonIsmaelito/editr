"""
Findr FastAPI Application

HTTP layer wrapping the Findr pipeline. Three endpoints:
  POST /search          — kick off a search, returns search_id immediately
  POST /search/{id}/clarify — answer clarifying questions, re-run classifier
  GET  /health          — liveness check

The pipeline runs as a background task so /search returns instantly.
The frontend subscribes to results via Convex useQuery (WebSocket push).
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.config import CONVEX_URL, OPENAI_API_KEY
from src.db import convex_store
from src.pipeline import run_pipeline

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("findr.api")


# ---------------------------------------------------------------------------
# Lifespan — startup/shutdown logging
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("Findr API starting up")
    logger.info(f"  OpenAI key configured: {bool(OPENAI_API_KEY)}")
    logger.info(f"  Convex URL configured:  {bool(CONVEX_URL)}")
    logger.info("=" * 60)
    yield
    logger.info("Findr API shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Findr API",
    description="Video moment discovery engine",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# In-flight task tracking (search_id → asyncio.Task)
# ---------------------------------------------------------------------------
_running_tasks: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    conversation_context: str = ""


class SearchResponse(BaseModel):
    search_id: str
    status: str


class ClarifyRequest(BaseModel):
    answers: list[str] = Field(..., min_length=1)


class HealthResponse(BaseModel):
    status: str
    version: str
    openai_configured: bool
    convex_configured: bool


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------
async def _run_pipeline_background(
    search_id: str,
    query: str,
    conversation_context: str,
):
    """Wraps run_pipeline with logging + cleanup of the task tracker."""
    start = time.perf_counter()
    logger.info(f"[search:{search_id}] Pipeline started | query={query!r:.80}")
    try:
        result = await run_pipeline(
            query=query,
            conversation_context=conversation_context,
            search_id=search_id,
        )
        elapsed = time.perf_counter() - start
        logger.info(
            f"[search:{search_id}] Pipeline finished in {elapsed:.2f}s | "
            f"status={result.status} | moments={len(result.moments)}"
        )
    except Exception:
        elapsed = time.perf_counter() - start
        logger.exception(
            f"[search:{search_id}] Pipeline failed after {elapsed:.2f}s"
        )
    finally:
        _running_tasks.pop(search_id, None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/search", response_model=SearchResponse)
async def create_search(req: SearchRequest):
    """
    Start a new Findr search. Returns a search_id immediately.
    The pipeline runs in the background — results are pushed to
    Convex in real-time and the frontend subscribes via useQuery.
    """
    try:
        search_id = str(convex_store.create_search(req.query, []))
    except Exception as e:
        logger.error(f"[search] Convex create_search failed: {e}")
        raise HTTPException(
            status_code=503,
            detail="Convex is unavailable; cannot start realtime search session.",
        )

    logger.info(
        f"[search:{search_id}] POST /search | "
        f"query={req.query!r:.80} | "
        f"has_context={bool(req.conversation_context)}"
    )

    task = asyncio.create_task(
        _run_pipeline_background(
            search_id=search_id,
            query=req.query,
            conversation_context=req.conversation_context,
        )
    )
    _running_tasks[search_id] = task

    return SearchResponse(search_id=search_id, status="processing")


@app.post("/search/{search_id}/clarify", response_model=SearchResponse)
async def clarify_search(search_id: str, req: ClarifyRequest):
    """
    Continue a search that needs clarification. The user's answers
    are appended to the conversation context and the pipeline re-runs.
    """
    logger.info(
        f"[search:{search_id}] POST /clarify | "
        f"answers={len(req.answers)}"
    )

    # Build conversation context from the answers
    context = "\n".join(
        f"User answer: {answer}" for answer in req.answers
    )

    # Cancel any existing task for this search
    existing = _running_tasks.get(search_id)
    if existing and not existing.done():
        existing.cancel()
        logger.info(f"[search:{search_id}] Cancelled previous pipeline run")

    task = asyncio.create_task(
        _run_pipeline_background(
            search_id=search_id,
            query="",  # Original query is in the conversation context
            conversation_context=context,
        )
    )
    _running_tasks[search_id] = task

    return SearchResponse(search_id=search_id, status="processing")


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Liveness check with dependency status."""
    return HealthResponse(
        status="ok",
        version="0.1.0",
        openai_configured=bool(OPENAI_API_KEY),
        convex_configured=bool(CONVEX_URL),
    )
