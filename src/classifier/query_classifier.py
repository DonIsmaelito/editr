"""
Findr Query Classifier

Takes a user's natural language query and produces:
1. Clarifying questions (if platform, action, or video context is missing)
2. Output format (structured vs direct)
3. Sub-queries with reasoning (for parallel API search)

The classifier decomposes complex queries into ordered sub-items,
each with an optimized platform search string and a reasoning trace
that is reused downstream for vector similarity filtering.
"""

import hashlib
import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from src.config import CLASSIFIER_MODEL, OPENAI_API_KEY
from src.models.schemas import (
    ClassifierOutput,
    ClarifyingQuestion,
    OutputFormat,
    Platform,
    SubQuery,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache — avoid repeat LLM calls for identical queries
# ---------------------------------------------------------------------------
_CACHE: Dict[str, Tuple[ClassifierOutput, float]] = {}
_CACHE_TTL = 180  # 3 minutes


def _cache_key(query: str, conversation: str) -> str:
    raw = f"{query}||{conversation}"
    return hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
CLASSIFIER_SYSTEM_PROMPT = """\
You are Findr's query classifier. Your job is to analyze a user's request
and produce a structured JSON response that drives how videos are searched,
found, and presented in the UI.

Every user query has three components:
1. PLATFORM — where to search (youtube, tiktok, x)
2. ACTION — what the user wants to do (learn, watch, find, review, how-to, etc.)
3. VIDEO CONTEXT — the actual content they're looking for

RULES:
- ALMOST NEVER return clarifying_questions. Your job is to SEARCH, not to
  interrogate the user. The only time you should ask for clarification is
  when the query is so vague that you literally cannot construct ANY
  meaningful search string (e.g., "find something" with zero topic context).

- NEVER ask for clarification if the user has provided:
  - A topic or subject (even vague ones like "news", "funny", "latest stuff")
  - A platform (youtube, tiktok, x/twitter)
  - Any describable content ("videos about X", "show me Y", "Z on TikTok")

- When in doubt, ALWAYS proceed with your best interpretation. Make a
  reasonable assumption and search. A slightly imperfect search result is
  infinitely better than asking the user to clarify. Users came here to
  find videos, not answer questions.

- If a query could be interpreted multiple ways, pick the MOST LIKELY
  interpretation and run with it. Do NOT ask "did you mean A or B?" —
  just pick the most probable one and search.

- Platform defaults:
  - YouTube: long-form (tutorials, lectures, streams, podcasts, music)
  - TikTok: short-form trends, challenges, reactions, viral moments
  - X/Twitter: news, commentary, public figure statements, clips shared on X
  - If unclear, default to YouTube.

OUTPUT FORMAT DECISION:
- "structured": Use when the query implies LEARNING, step-by-step processes,
  multi-part content, courses, tutorials, how-tos, comparisons, or anything
  where the user benefits from sequential consumption.

  HOW STRUCTURED OUTPUT WORKS IN THE UI:
  Each sub-query becomes a COLLAPSABLE SECTION in the chat interface. The
  section header shows the sub-query's "title" field. When the user clicks
  to expand it, they see the embedded video playing the exact moment found.
  Sections appear one by one as the pipeline processes them sequentially.

  ORDER MATTERS FOR STRUCTURED OUTPUT. The sub-queries must be in
  pedagogical or logical order — the sequence a learner would follow.
  For example, fundamentals before advanced concepts, setup before
  implementation, theory before practice. The user will consume these
  top to bottom as collapsable sections, so each section should build
  on the previous one.

- "direct": Use for simple lookups, single moments, "show me X", viral clips,
  reactions, or any request where 1-3 embeds satisfy the user.
  TikTok and X results ALWAYS use direct format (short-form content does
  not benefit from collapsable sections).

SUB-QUERY DECOMPOSITION:
Break the video context into ordered sub-queries. Each sub-query represents
one focused video moment to find.

Each sub-query has four fields:
- "proposed_video_query": Optimized search string for the platform API.
  This is what gets typed into YouTube/TikTok/X search.
- "reasoning": WHY this sub-query matters and what the user should learn
  from it. This reasoning trace is reused downstream as the vector
  similarity search query, so make it semantically rich and specific.
- "title": A concise, human-readable section title (5-12 words).
  For structured output, this becomes the COLLAPSABLE HEADER the user sees.
  Make it descriptive of the topic, not the search mechanics.
  Good: "Setting Up Your React Development Environment"
  Bad: "React setup tutorial video"
  For direct output, this is a label above the embed.
- "order": Zero-indexed sequence number. For structured output, this
  determines the collapsable section order from top to bottom.

PLATFORM-SPECIFIC NOTES:
- YouTube: Full pipeline — search, transcript fetch, segment embedding,
  vector similarity search, exact moment finding. The embed will play
  a specific timestamp range (e.g., 2:30 to 4:15 of a 20-minute video).
- TikTok: No transcripts, no timestamps. The entire short-form video IS
  the result. Search and metadata matching only. Always direct format.
- X/Twitter: Text-first platform. Posts (with or without video) are the
  result. No transcripts, no moment finding. Always direct format.

Examples:
  User: "I want to learn React for building a dashboard app"
  → structured format, platform: youtube, sub-queries:
    1. title: "React Fundamentals: Components, JSX & Props"
       query: "React fundamentals tutorial beginner components JSX"
       reasoning: "User needs core React concepts first — components, JSX, state, props. These are foundational for any React project."

    2. title: "State Management with Hooks"
       query: "React hooks useState useEffect tutorial"
       reasoning: "Hooks are essential for modern React — useState for local state, useEffect for side effects. Dashboard will need both."

    3. title: "Building a Dashboard: Putting It All Together"
       query: "React dashboard project tutorial build"
       reasoning: "Practical dashboard build that ties together components, hooks, and layout. Should show routing, data fetching, charts."

  User: "show me the best NBA dunks from last night"
  → direct format, platform: youtube, sub-queries:
    1. title: "Best NBA Dunks — Last Night's Highlights"
       query: "NBA best dunks highlights today"
       reasoning: "User wants recent dunk highlights, likely a compilation or top plays video"

  User: "what is everyone saying about the new iPhone on twitter"
  → direct format, platform: x, sub-queries:
    1. title: "New iPhone Reactions on X"
       query: "new iPhone review reaction"
       reasoning: "User wants public discourse and reactions about the latest iPhone announcement"

  User: "trending dance challenges"
  → direct format, platform: tiktok, sub-queries:
    1. title: "Trending Dance Challenges"
       query: "trending dance challenge 2025"
       reasoning: "User wants popular/viral dance challenge TikToks"

RESPONSE FORMAT (strict JSON):
{
  "needs_clarification": false,
  "clarifying_questions": [],
  "platform": "youtube",
  "output_format": "structured" or "direct",
  "sub_queries": [
    {
      "proposed_video_query": "...",
      "reasoning": "...",
      "title": "...",
      "order": 0
    }
  ]
}

IMPORTANT: needs_clarification should be false for 99% of queries.
Only set it to true if the query is completely empty or nonsensical
(e.g., "find me something" with absolutely no topic, or a random string).
If there is ANY interpretable content in the query, proceed with sub_queries.
"""


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class QueryClassifier:
    def __init__(self):
        self._client = None

    def _get_client(self) -> AsyncOpenAI:
        """Lazy client init — reads OPENAI_API_KEY at call time, not import time."""
        if self._client is None:
            key = OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
            if not key:
                raise RuntimeError("OPENAI_API_KEY not configured")
            self._client = AsyncOpenAI(api_key=key)
        return self._client

    async def classify(
        self,
        query: str,
        conversation_context: str = "",
    ) -> ClassifierOutput:
        """
        Classify a user query into platform, output format, and sub-queries.

        Args:
            query: The user's natural language request.
            conversation_context: Prior messages for multi-turn context
                                  (e.g., answers to clarifying questions).

        Returns:
            ClassifierOutput with either clarifying_questions or resolved results.
        """
        client = self._get_client()

        # Check cache
        ck = _cache_key(query, conversation_context)
        if ck in _CACHE:
            cached, ts = _CACHE[ck]
            if time.time() - ts < _CACHE_TTL:
                logger.info("[Classifier] Cache hit")
                return cached

        user_message = query
        if conversation_context:
            user_message = (
                f"Previous conversation:\n{conversation_context}\n\n"
                f"Current query: {query}"
            )

        logger.info(f"[Classifier] Classifying: {query[:80]}...")

        t0 = time.perf_counter()
        try:
            response = await client.chat.completions.create(
                model=CLASSIFIER_MODEL,
                messages=[
                    {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=2000,
            )
            elapsed = time.perf_counter() - t0

            raw = response.choices[0].message.content
            data = json.loads(raw)
            result = self._parse_response(data)

            # Cache
            _CACHE[ck] = (result, time.time())

            logger.info(
                f"[Classifier] LLM call took {elapsed:.2f}s | "
                f"model={CLASSIFIER_MODEL} | "
                f"platform={result.platform.value}, "
                f"format={result.output_format.value}, "
                f"sub_queries={len(result.sub_queries)}, "
                f"needs_clarification={result.needs_clarification}"
            )
            for i, sq in enumerate(result.sub_queries):
                logger.info(
                    f"[Classifier]   sub_query[{i}]: "
                    f"query={sq.proposed_video_query!r:.60} | "
                    f"reasoning={sq.reasoning[:80]}..."
                )
            return result

        except json.JSONDecodeError as e:
            elapsed = time.perf_counter() - t0
            logger.error(
                f"[Classifier] JSON parse failed after {elapsed:.2f}s: {e}"
            )
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.error(f"[Classifier] Failed after {elapsed:.2f}s: {e}")
            # Fallback: treat as simple YouTube search
            return ClassifierOutput(
                platform=Platform.YOUTUBE,
                output_format=OutputFormat.DIRECT,
                sub_queries=[
                    SubQuery(
                        proposed_video_query=query,
                        reasoning=f"Fallback: using raw query as search term",
                        order=0,
                    )
                ],
            )

    def _parse_response(self, data: dict) -> ClassifierOutput:
        """Parse LLM JSON into ClassifierOutput with safe defaults."""

        needs_clarification = data.get("needs_clarification", False)

        clarifying_questions = []
        for q in data.get("clarifying_questions", []):
            clarifying_questions.append(ClarifyingQuestion(
                question=q.get("question", ""),
                options=q.get("options"),
            ))

        # Parse platform
        platform_str = data.get("platform", "youtube").lower()
        try:
            platform = Platform(platform_str)
        except ValueError:
            platform = Platform.YOUTUBE

        # Parse output format
        fmt_str = data.get("output_format", "direct").lower()
        try:
            output_format = OutputFormat(fmt_str)
        except ValueError:
            output_format = OutputFormat.DIRECT

        # Parse sub-queries
        sub_queries = []
        for i, sq in enumerate(data.get("sub_queries", [])):
            sub_queries.append(SubQuery(
                proposed_video_query=sq.get("proposed_video_query", ""),
                reasoning=sq.get("reasoning", ""),
                title=sq.get("title", f"Result {i + 1}"),
                order=sq.get("order", i),
            ))

        return ClassifierOutput(
            needs_clarification=needs_clarification,
            clarifying_questions=clarifying_questions,
            platform=platform,
            output_format=output_format,
            sub_queries=sub_queries,
        )
