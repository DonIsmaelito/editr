# Findr — Video Moment Discovery Engine

## What is Findr?

Findr takes a natural language query and returns timestamped video moments
from YouTube, TikTok, and X/Twitter. Users get either structured collapsable
sections (for learning/tutorials) or direct embeds (for simple lookups).

## Running Locally

### Backend (FastAPI SSE server on :8001)

```bash
# Install Python dependencies
pip install -r requirements.txt

# Copy and fill env vars
cp .env.example .env

# Run the SSE server
uvicorn src.api.server:app --reload --port 8001
```

### Frontend (Next.js on :3000)

```bash
cd frontend
npm install
npm run dev
```

### Convex (deploy schema + functions)

```bash
# First time — authenticate and link to project:
npx convex dev

# Or deploy directly (needs CONVEX_DEPLOY_KEY):
npx convex deploy
```

The frontend proxies `/api/findr/*` to `localhost:8001/*` via next.config.ts rewrites.

## Architecture

```
Frontend (Next.js :3000)            Backend (FastAPI :8001)
┌──────────────────────────┐       ┌──────────────────────────┐
│ page.tsx                 │       │ api/server.py            │
│   → useFindrSearch()     │       │   POST /api/search       │
│   → findr-api.ts (SSE)   │──────→│   → run_pipeline()       │
│                          │  SSE  │     → classify            │
│ next.config.ts rewrites  │◄──────│     → youtube search      │
│ /api/findr/* → :8001/*   │events │     → transcript/embed    │
└──────────────────────────┘       │     → vector search       │
                                   │     → moment finder       │
                                   └──────────────────────────┘
```

## SSE Event Types

| Event | Data | When |
|-------|------|------|
| status | `{ stage: "classifying" \| "searching" \| "processing" \| "finding" }` | Each pipeline stage |
| clarification | `{ questions: [{ question, options? }] }` | Classifier needs more info |
| moment | `{ videoName, videoId, embedUrl, start, end, title, description, order }` | Each moment found |
| done | `{ query, outputFormat, platform, momentCount }` | Pipeline complete |
| error | `{ message: str }` | Pipeline failure |

## Key Files

### Backend (`src/`)

| File | Purpose |
|------|---------|
| `api/server.py` | FastAPI SSE server — POST /api/search streams events |
| `api/transforms.py` | Response helpers (group_moments_by_video, format_timestamp) |
| `pipeline.py` | Main orchestrator — wires all steps, on_progress callback |
| `config.py` | Environment vars + constants |
| `classifier/query_classifier.py` | LLM query classification + sub-query decomposition |
| `search/youtube.py` | yt-dlp search + transcript-verified results |
| `search/tiktok.py` | TikTok search via Browser Use Skills |
| `search/twitter.py` | X/Twitter search via Browser Use Skills |
| `transcript/segment_processor.py` | 5-min segments + OpenAI embeddings |
| `moment_finder/finder.py` | LLM exact timestamp extraction |
| `db/convex_store.py` | Convex Python client (mutations, vector search, caching) |
| `models/schemas.py` | All Pydantic data models |
| `agents/visual_verify.py` | YouTube screenshot verification (Daytona) |
| `agents/browser_skills.py` | Browser Use Skills manager |
| `main.py` | Legacy HTTP server (replaced by api/server.py) |

### Frontend (`frontend/src/`)

| File | Purpose |
|------|---------|
| `app/page.tsx` | Main chat UI — handles messages, collapsibles, embeds |
| `hooks/useFindrSearch.ts` | React hook wrapping SSE client |
| `lib/findr-api.ts` | SSE client — POST + stream parsing |

### Convex (`convex/`)

| File | Purpose |
|------|---------|
| `schema.ts` | DB schema (vector index on transcriptSegments) |
| `segments.ts` | Vector search action + segment CRUD + cleanup |
| `results.ts` | Progressive result delivery |
| `searches.ts` | Search session management |
| `transcriptCache.ts` | Transcript caching |

## Output Formats

**Structured** — For learning, tutorials, multi-step queries.
Each sub-query result becomes a collapsable section with a title.
Videos processed sequentially so they stream in order.

**Direct** — For simple lookups, reactions, single moments.
Sub-queries processed in parallel. 1-3 embeds rendered inline.

## Import Convention

All internal imports use `from src.* import ...` (package root is `src/`).
Run the server from the repo root so Python resolves `src` correctly.
