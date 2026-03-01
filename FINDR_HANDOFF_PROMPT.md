# Findr Backend — Complete Handoff Prompt

You are picking up development on Findr, a video moment discovery engine. The entire backend pipeline has been implemented and lives in this repository. I am going to explain everything that has been built, how every piece connects, the specific APIs and their limitations, how the data flows from a raw user query all the way to a timestamped video embed URL, and then I will tell you exactly what remains to be built. Read all of this carefully because the decisions made here are deliberate and interconnected.

## What Findr Is

Findr lets a user type a natural language query into a chat-like interface and receive video moments from across the internet. Not full videos. Specific timestamped moments within videos. The user says something like "I want to learn how to build a REST API with FastAPI" and Findr returns a set of collapsable sections, each one containing a YouTube embed that plays a precise 30-90 second window of a tutorial video covering that exact sub-topic. Or the user says "show me the best NBA dunks from last night" and Findr drops a single YouTube embed timestamped to the highlight reel. The key insight is that we never download videos. We return embed URLs with start and end parameters, so the video plays natively in the browser from the platform's own player.

The platform focus right now is YouTube because it has two critical free advantages: free search via yt-dlp and free timestamped transcripts via youtube-transcript-api. TikTok and X/Twitter are scoped for later but the data models and classifier already account for them.

## The Complete Flow — Query to Embed URL

Here is exactly what happens when a user submits a query. Every step described here is implemented and working.

The pipeline starts in `pipeline.py` which is the orchestrator. It calls into each service in sequence. The function is `run_pipeline(query, conversation_context, search_id)` and it returns a `FindrResult` object.

### Step 1 — Classification

The user's query goes to `classifier/query_classifier.py`. This sends the query to GPT-4o with a carefully designed system prompt. The classifier's job is to decompose the query into three components: platform (youtube, tiktok, or x), action (what the user wants to do), and video context (what content they are looking for). If any of these three components is genuinely ambiguous or missing, the classifier returns clarifying questions instead of proceeding. This is important — we want the minimum viable context from the user before we start burning API calls. The classifier is conservative about asking questions though. If it can reasonably infer the platform or action, it does not ask.

The classifier also makes two critical decisions. First, it decides the output format. "Structured" means the user gets collapsable step-by-step sections, each hiding a video embed. This is for learning, tutorials, how-tos, multi-part queries, anything where sequential consumption matters. "Direct" means simple inline embeds, one or a few, for quick lookups like "show me funny cooking fails" or "what did Elon say about AI today." TikTok and X results will almost always be direct format.

Second, the classifier decomposes the video context into an ordered list of sub-queries. Each sub-query has two fields: a `proposed_video_query` which is an optimized search string for the platform API, and a `reasoning` field which is the agent's explanation of why this sub-query exists and what it should find. The reasoning field is not just for logging. It gets reused downstream as the vector similarity search query. This is a deliberate design choice because the reasoning trace is more semantically precise than the raw user query. For example, if the user asks "I want to learn React to build a dashboard," one sub-query might have `proposed_video_query: "React hooks useState useEffect tutorial"` and `reasoning: "Hooks are essential for modern React dashboard state management — need to understand useState for local state and useEffect for side effects like API calls."` That reasoning string, when embedded, produces a much better vector match against the actual transcript content than "React hooks tutorial" would.

The classifier uses GPT-4o (not mini) because this is the most consequential LLM call in the pipeline. A bad classification cascades through everything. It uses JSON mode for structured output, temperature 0.3 for consistency, and caches results for 3 minutes keyed on query hash to avoid repeat calls for identical queries. If the LLM call fails entirely, the fallback is to treat the raw query as a single direct YouTube search.

The output is a `ClassifierOutput` Pydantic model containing `needs_clarification` (bool), `clarifying_questions` (list), `platform`, `output_format`, and `sub_queries` (list of SubQuery objects).

### Step 2 — Platform Search

For each sub-query, the pipeline searches YouTube via `search/youtube.py`. This uses yt-dlp, a free open-source tool that wraps YouTube's search without requiring an API key, has no quota limits, and returns rich metadata. The search call runs in a thread via `asyncio.to_thread` to avoid blocking the event loop since yt-dlp is synchronous.

There are two important filters applied during search. First, a duration cap of 1800 seconds (30 minutes). We skip videos longer than this because processing their transcripts takes too long and the moment finding becomes less precise with massive transcripts. The search over-fetches by requesting 2x the needed results to compensate for the filtering. Second, and this is the nuance that matters most, we use `search_with_transcript()` which is a wrapper that searches YouTube and then immediately verifies each result has an available transcript before returning it. It over-fetches 3x from yt-dlp, iterates through the results, calls `get_transcript()` on each one, and drops any video that does not have a transcript. Only videos with verified transcripts make it through. This is critical because without a transcript, the entire downstream pipeline (embedding, vector search, moment finding) cannot function for that video. We would rather return fewer results than return videos we cannot analyze.

The transcript fetching itself has a two-tier fallback. Tier 1 is youtube-transcript-api, which is fast (about 200ms) and pulls directly from YouTube's transcript data. It returns a list of segment dicts each with text, start time, end time, and duration. Tier 2, if the first fails, is yt-dlp subtitle extraction. This downloads the json3 format subtitle file and parses it into the same segment format. The json3 parsing handles YouTube's auto-generated captions which come as events with start timestamps in milliseconds and nested UTF-8 text segments that need to be concatenated.

The YouTubeTranscriptService class lives in `services/youtube_transcript.py`. This is a self-contained copy with zero external dependencies beyond youtube-transcript-api. It handles URL parsing for all YouTube URL formats (watch?v=, youtu.be/, embed/) and extracts the 11-character video ID. The `get_transcript` static method is the main entry point. It returns None on any error so callers can gracefully fall back.

For structured output, sub-queries are processed sequentially so results stream to the user in order (collapsable 1 appears, then 2, then 3). For direct output, sub-queries are processed in parallel via `asyncio.gather` for speed.

### Step 3 — Transcript Processing and Embedding

Once we have a transcript for a video, it goes through `transcript/segment_processor.py` which does three things in sequence.

First, consolidation. YouTube transcripts come as fine-grained word-level or short-phrase segments, often hundreds of them. The consolidation function merges these into sentence-level chunks of roughly 5 seconds each. It walks through the segments, accumulates text, and flushes whenever the time span exceeds 5 seconds. This is lossless — all text is preserved, just grouped. A transcript of 350 word-level segments typically consolidates down to 50-80 sentence-level segments. This reduction is important because it means the LLM in the moment-finding step sees coherent sentences instead of individual words.

Second, macro-segment splitting. The consolidated segments are grouped into 5-minute windows. Each window collects all transcript text whose start time falls within it. For a 20-minute video, you get 4 macro-segments. For a 10-minute video, 2. These macro-segments are the units that get embedded and stored in the vector database. The 5-minute duration was chosen as a balance — small enough that vector search meaningfully filters (if the relevant moment is in minute 14, you only need to scan the 10:00-15:00 segment, not the whole transcript), but large enough that each segment has sufficient text for a meaningful embedding.

Third, OpenAI embedding generation. All macro-segments are embedded in a single batch API call using text-embedding-3-small at 1536 dimensions. This is the same dimensionality that the Convex vector index is configured for. The batch call is efficient — embedding 4-6 segments costs a fraction of a cent and takes under a second.

The output is a list of `EmbeddedSegment` objects, each with video_id, segment_index, start_time, end_time, the full text, and the 1536-dimensional embedding vector.

### Step 4 — Convex Storage and Vector Search

The embedded segments are stored in Convex via `db/convex_store.py`. Convex is a reactive database with real-time WebSocket subscriptions. The Python client calls mutations and actions over HTTP.

The Convex schema (defined in `convex/schema.ts`) has four tables. The `transcriptSegments` table stores the embedded segments and has a vector index called "by_embedding" configured for 1536 dimensions with a filter field on videoId. The `searches` table tracks search sessions with status progression (classifying, searching, analyzing, complete, error). The `searchResults` table stores the final moments found, indexed by searchId and order so the frontend can subscribe and see results appear progressively. The `transcriptCache` table stores raw transcripts keyed by videoId to avoid re-fetching transcripts for videos we have already processed.

After storing the segments, the pipeline generates an embedding for the classifier's reasoning trace (not the raw user query, not the search string — the reasoning trace specifically) and calls the Convex vector search action. The action (`convex/segments.ts` `searchSimilar`) runs `ctx.vectorSearch` on the transcriptSegments table, filtered by videoId, and returns the top 2 most similar segments along with their similarity scores. The action then fetches the full segment data via an internal query and returns everything together.

This vector search step is the key optimization. A 30-minute video has 6 macro-segments. Without vector search, the moment-finding LLM would need to read all 6 segments. With vector search, it reads only the 1-2 most relevant. This saves LLM tokens, reduces latency, and improves precision because the LLM is not distracted by irrelevant transcript sections.

If Convex is unavailable (not configured, network error), the pipeline falls back to sending all segments (capped at 3) directly to the moment finder. This means the system works without Convex, just slower and less precisely.

### Step 5 — Moment Finding

The filtered segments go to `moment_finder/finder.py`. The MomentFinder class sends them to GPT-4o-mini with a system prompt that instructs the model to find the exact timestamp range within the transcript that matches the user's query. The model receives the video title, the sub-query, the classifier's reasoning (for additional context), and the transcript text from the filtered segments with their global timestamps in MM:SS format.

The model returns JSON with a moments array. Each moment has start, end, title (3-8 words), and description (one sentence). The finder validates the response — checks that end is after start, enforces a minimum 15-second duration, caps at 120 seconds. It then constructs the embed URL: `https://www.youtube.com/embed/{video_id}?start={int(start)}&end={int(end)}&autoplay=0&rel=0`. That URL, when rendered in an iframe, plays exactly that segment of the video.

The output is a list of `FoundMoment` objects. Each one gets written to Convex via a mutation, which means the frontend (subscribed via `useQuery("results:bySearch", {searchId})`) sees it appear in real-time without polling.

### Step 6 — Progressive Delivery

For structured output, the pipeline processes sub-queries sequentially. After each sub-query completes and its moments are found, those moments are immediately written to Convex. The frontend is subscribed to the results table filtered by searchId. As each mutation fires, Convex pushes the new data over WebSocket, and the frontend re-renders. The user sees collapsable sections appear one by one. This is the core UX differentiator — the user does not wait for all results before seeing anything.

For direct output, all sub-queries are processed in parallel and results are written as they complete. The user sees results appear as fast as the pipeline can produce them.

## The File Structure

Here is every file in the codebase and what it does.

`config.py` holds all environment variables and constants. OPENAI_API_KEY and CONVEX_URL are the two required ones. It also defines which models to use for classification (gpt-4o), moment finding (gpt-4o-mini), and embeddings (text-embedding-3-small at 1536 dimensions). The search defaults are max 5 results per sub-query, 30-minute duration cap, 5-minute segment duration, and top 2 segments after vector filtering.

`models/schemas.py` contains every Pydantic model in the system. Platform and OutputFormat enums. ClarifyingQuestion for when the classifier needs more info. SubQuery with proposed_video_query, reasoning, and order. ClassifierOutput that wraps all classifier results. VideoSearchResult for YouTube metadata. TranscriptSegment and EmbeddedSegment for transcript processing. FoundMoment for the final discovered moments. FindrResult for the complete pipeline response.

`classifier/query_classifier.py` is the LLM-based classifier described in Step 1. The system prompt is embedded directly in the file as a constant. The classifier has a 3-minute in-memory cache.

`search/youtube.py` contains YouTubeSearchService with three methods: `search_videos` (yt-dlp search), `get_transcript` (two-tier fallback), and `search_with_transcript` (combined search + transcript verification).

`services/youtube_transcript.py` is the self-contained YouTubeTranscriptService class. This was originally from a sister project and has been duplicated here so the codebase has zero external code dependencies.

`transcript/fetcher.py` is a thin async wrapper around YouTubeTranscriptService for convenience.

`transcript/segment_processor.py` handles consolidation, macro-segment splitting, and OpenAI embedding generation.

`moment_finder/finder.py` is the MomentFinder class that takes filtered segments and produces exact timestamps.

`db/convex_store.py` is the Python Convex client wrapper with functions for creating searches, updating status, adding results, storing segments, running vector search, and transcript caching.

`pipeline.py` is the orchestrator that calls everything in sequence.

`convex/schema.ts` defines the Convex database schema with four tables and a vector index.

`convex/segments.ts` has the segment insert mutation, the internal getById query, the searchSimilar vector search action, and the deleteByVideo cleanup mutation.

`convex/searches.ts` has create and updateStatus mutations and a get query for search sessions.

`convex/results.ts` has addResult mutation and bySearch query for progressive result delivery.

`convex/transcriptCache.ts` has getByVideoId query and insert mutation (with upsert logic) for transcript caching.

`CLAUDE.md` is a reference document for future sessions with the architecture diagram and key design decisions.

## API Details and Limitations

YouTube search via yt-dlp uses the `ytsearchN:query` syntax. It is free, has no API key, has no quota, and returns full metadata including duration, title, channel, view count, description, and thumbnails. The limitation is speed — each search takes 2-4 seconds because yt-dlp makes real HTTP requests to YouTube. It runs in a thread to avoid blocking async. Another limitation is that yt-dlp search results are not as refined as YouTube's actual search algorithm, so relevance can be slightly off. We compensate by over-fetching.

youtube-transcript-api is free, requires no API key, and returns timestamped transcript segments in about 200ms. The limitation is coverage — not all YouTube videos have transcripts. Auto-generated captions exist for most English-language content but can be disabled by uploaders. Some content types (music, non-speech) have no useful transcript. This is why `search_with_transcript()` exists — to filter out videos we cannot process.

OpenAI embeddings via text-embedding-3-small cost about $0.02 per million tokens. For Findr's use case (embedding a few hundred words of transcript per segment, plus one reasoning trace), each search costs a fraction of a cent. The 1536 dimension count is fixed and matches the Convex vector index configuration. If you change the embedding model, you must also change the Convex schema dimensions.

Convex vector search has a critical limitation: it can only run in actions, not queries or mutations. Actions are not reactive (unlike queries), which is why the vector search is called from the Python backend and not from the frontend. The vectorSearch API returns _id and _score (similarity from -1 to 1, higher is better). The limit parameter accepts 1-256. Filter expressions support equality checks on fields declared in filterFields.

The Convex Python client (pip install convex) is the official client. It calls mutations and actions over HTTP. It is synchronous, which is why the pipeline calls it from synchronous wrapper functions rather than using async patterns.

## What Remains to Be Built

### The FastAPI Application

There is no HTTP server yet. The pipeline exists as a Python module that can be called programmatically, but there is no FastAPI app wrapping it. You need to create an API layer with these endpoints:

A POST /search endpoint that accepts a JSON body with query and optional conversation_context fields. It should create a Convex search record, kick off `run_pipeline()` as a background task (using FastAPI's BackgroundTasks or asyncio.create_task), and return the search_id immediately. The frontend uses this search_id to subscribe to results via Convex.

A POST /search/{search_id}/clarify endpoint for the multi-turn clarification flow. When the classifier returns clarifying questions, the frontend displays them, the user answers, and this endpoint takes those answers and re-runs the classifier with the conversation_context updated.

A GET /health endpoint.

CORS middleware configured to allow the frontend origin (localhost:3000 for dev).

This is roughly 80-100 lines of standard FastAPI code. The important part is that `run_pipeline` must run as a background task so the POST /search endpoint returns immediately.

### Convex Project Initialization

The TypeScript files in `convex/` are written but the Convex project has not been initialized. You need to run `npx convex init` in the repo root, which creates the `convex/_generated/` directory with type definitions. Then copy or move the .ts files from `findr_src/convex/` into the project's `convex/` directory. Then run `npx convex dev` to deploy the schema and functions to a Convex development instance. This will give you the CONVEX_URL to put in the .env file.

The TypeScript files import from `"./_generated/server"` and `"./_generated/api"` which only exist after Convex initialization. Until then, the TypeScript files will show import errors, but that is expected.

### Transcript Cache Integration

The convex_store has `get_cached_transcript()` and `cache_transcript()` functions already written, and the Convex transcriptCache table and TypeScript functions already exist. But the pipeline does not use them yet. In `pipeline.py`'s `_process_single_youtube_subquery` function, you need to add a cache check before calling `get_transcript()` and a cache write after a successful transcript fetch. This is about 8 lines of code. Check the cache first with the video_id, and if found, skip the transcript fetch entirely. If not found, fetch the transcript normally and then cache it.

### TikTok Search Service

The `search/` directory needs a `tiktok.py` file. The Apify TikTok scraper actor (clockworks/free-tiktok-scraper or equivalent) takes a search keyword and returns video metadata including descriptions, hashtags, engagement metrics, and sometimes subtitle text. The implementation would use the apify-client Python package, run the actor call in a thread, and return a list of VideoSearchResult objects with platform set to Platform.TIKTOK. The metadata from Apify is different from yt-dlp — TikTok results have hashtags and engagement counts but no duration in the same format. You will need to normalize the output. The APIFY_API_TOKEN environment variable is already defined in config.py. This is roughly 60-80 lines.

### X/Twitter Search Service

Same pattern as TikTok but using the Apify X/Twitter scraper actor. Returns post metadata including text content, author, media URLs, and whether the post has video. The key difference from YouTube and TikTok is that X posts are primarily text-based, so the "moment finding" for X is really just relevance filtering based on the post text rather than transcript analysis. This is roughly 60-80 lines.

### TikTok/X Pipeline Integration

The pipeline.py currently has a placeholder branch for non-YouTube platforms that falls back to YouTube. This needs to be replaced with actual TikTok and X processing flows. The TikTok flow would be: search via Apify, get metadata, if subtitle text is available from the scraper then use it as a lightweight transcript for moment finding, otherwise use visual verification. The X flow would be: search via Apify, filter for relevance based on post text, return matching posts as direct embeds.

The embed URL construction for TikTok and X is already implemented in the MomentFinder's `_build_embed_url` method. TikTok uses `https://www.tiktok.com/player/v1/{video_id}` (no timestamp parameter — TikTok does not support URL-based timestamps, only programmatic seeking via postMessage after iframe load). X uses `https://x.com/i/status/{post_id}` (rendered via the react-tweet frontend component, not an iframe).

### Visual Verification Agent

For TikTok and X, we cannot rely on transcripts because they often do not exist. The visual verification approach is: navigate to the post URL with a headless browser, take a screenshot, send the screenshot to GPT-4o-mini's vision capability, and ask "does this content match the query?" This returns a confidence score and a brief description. The browser-use framework and patterns for this exist in a sibling experimental directory and have been tested, but the Findr-specific adaptation has not been written.

This is the most complex remaining piece. It requires browser-use and a headless Chromium installation. For production, it would run in Modal sandboxes (ephemeral containers). For local development, it runs against a local Chromium. The implementation would be roughly 150-200 lines.

### Segment Cleanup

After a search completes and all moments have been found, the transcript segments stored in Convex should be deleted to avoid accumulating stale data. The `segments.ts` already has a `deleteByVideo` mutation. The pipeline just needs to call `convex_store` with a cleanup function at the end of each sub-query processing. This is 3-5 lines.

### Package Renaming

The internal import paths all use `findr_src.*` because this code was initially developed inside another project's repository. When you move this to its own repo, you will want to rename the package to just `findr` or whatever the top-level package name should be. This is a bulk find-and-replace of `findr_src` across all Python files. There are exactly 14 internal cross-references to update.

### Requirements File

The dependencies are: openai, pydantic, youtube-transcript-api, yt-dlp, requests, convex, fastapi, and uvicorn. For TikTok/X support add apify-client. For visual verification add browser-use and playwright.

### Environment Configuration

The .env file needs OPENAI_API_KEY (required for all LLM calls and embeddings), CONVEX_URL (required for progressive delivery and vector search), and optionally GROQ_API_KEY (for transcription fallback) and APIFY_API_TOKEN (for TikTok/X search). The config.py already reads all of these.
