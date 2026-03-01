"""
Findr SSE Server

FastAPI app that exposes a single POST /api/search endpoint.
The response is a Server-Sent Events (SSE) stream — the frontend reads
typed events as the pipeline progresses:

  event: status
  data: {"stage": "classifying"}

  event: moment
  data: {"videoName": "...", "videoId": "...", "embedUrl": "...", ...}

  event: done
  data: {"query": "...", "outputFormat": "structured", "momentCount": 3}

WHY SSE:
  The pipeline takes 10-30s. Without streaming, the user stares at a dead
  spinner. With SSE, they see live stage updates ("Searching YouTube...",
  "Analyzing transcript...") and moments appear progressively as they're
  found.

ARCHITECTURE:
  Frontend (Next.js :3000)
    page.tsx → useFindrSearch() hook
      → POST /api/findr/api/search  (Next.js rewrites to :8001)
        → SSE stream back
          event: status  → update loading text
          event: moment  → append to collapsibles
          event: done    → mark complete

  next.config.ts rewrites /api/findr/* → :8001/*
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.config import CONVEX_URL, OPENAI_API_KEY
from src.db import convex_store
from src.pipeline import run_pipeline

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("findr.api")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("Findr SSE Server starting up")
    logger.info(f"  OpenAI key configured: {bool(OPENAI_API_KEY)}")
    logger.info(f"  Convex URL configured:  {bool(CONVEX_URL)}")
    logger.info("=" * 60)
    yield
    logger.info("Findr SSE Server shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Findr API",
    description="Video moment discovery — SSE streaming",
    version="0.2.0",
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
# Request model
# ---------------------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    conversation_context: Optional[str] = ""


class SearchStartResponse(BaseModel):
    search_id: str
    status: str


_running_tasks: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# SSE endpoint
# ---------------------------------------------------------------------------
async def _search_sse_impl(req: SearchRequest):
    """
    Stream pipeline progress as Server-Sent Events.

    Event types:
      status        → { stage: "classifying" | "searching" | "processing" | "finding" }
      trace         → { message, ...context }
      clarification → { questions: [{ question, options? }] }
      moment        → { videoName, videoId, embedUrl, start, end, title, description, order }
      done          → { query, outputFormat, platform, momentCount }
      error         → { message: str }
    """
    # asyncio.Queue bridges the pipeline callback → SSE generator
    queue: asyncio.Queue = asyncio.Queue()

    async def on_progress(event_type: str, data: Any):
        """Pipeline calls this at each stage — we push to the SSE queue."""
        await queue.put((event_type, data))

    async def run_and_signal_done():
        """Run the pipeline, then push a sentinel so the generator stops."""
        try:
            await run_pipeline(
                query=req.query,
                conversation_context=req.conversation_context or "",
                on_progress=on_progress,
            )
        except Exception as e:
            logger.error(f"[SSE] Pipeline exception: {e}", exc_info=True)
            await queue.put(("error", {"message": str(e)}))
        finally:
            await queue.put(None)  # Sentinel: stream is done

    async def event_generator():
        """Yield SSE-formatted lines from the queue."""
        # Start the pipeline as a background task
        task = asyncio.create_task(run_and_signal_done())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break  # Pipeline finished

                event_type, data = item
                payload = json.dumps(data, default=str)
                yield f"event: {event_type}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            task.cancel()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@app.post("/api/search")
async def search_sse(req: SearchRequest):
    return await _search_sse_impl(req)


@app.post("/api/search/stream")
async def search_sse_explicit(req: SearchRequest):
    return await _search_sse_impl(req)


async def _run_background_search(search_id: str, req: SearchRequest):
    try:
        await run_pipeline(
            query=req.query,
            conversation_context=req.conversation_context or "",
            search_id=search_id,
        )
    except Exception as e:
        logger.error(f"[search:{search_id}] Background pipeline failed: {e}", exc_info=True)
    finally:
        _running_tasks.pop(search_id, None)


@app.post("/search", response_model=SearchStartResponse)
async def start_search(req: SearchRequest):
    """
    Start a search and return the Convex search_id immediately.
    UI can subscribe to Convex events/results for live progress.
    """
    try:
        search_id = str(convex_store.create_search(req.query, []))
    except Exception as e:
        logger.error(f"[search] Convex create_search failed: {e}")
        raise HTTPException(
            status_code=503,
            detail="Convex is unavailable; cannot start realtime search session.",
        )

    task = asyncio.create_task(_run_background_search(search_id, req))
    _running_tasks[search_id] = task
    return SearchStartResponse(search_id=search_id, status="processing")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "0.2.0",
        "openai_configured": bool(OPENAI_API_KEY),
        "convex_configured": bool(CONVEX_URL),
    }
