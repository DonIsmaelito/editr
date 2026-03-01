"""
Findr Data Models

All Pydantic schemas for the query-to-moment pipeline.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Platform(str, Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    X = "x"


class OutputFormat(str, Enum):
    """
    STRUCTURED: Collapsable step-by-step sections. Each section holds a
                hidden video embed that plays a specific timeframe.
                For learning, tutorials, multi-part queries.

    DIRECT:     Simple embed(s) rendered inline. One or a few results.
                For simple requests, TikTok/X posts, single moments.
    """
    STRUCTURED = "structured"
    DIRECT = "direct"


# ---------------------------------------------------------------------------
# Classifier I/O
# ---------------------------------------------------------------------------

class ClarifyingQuestion(BaseModel):
    """A question the system asks when it lacks platform, action, or context."""
    question: str
    options: Optional[List[str]] = None  # Suggested answers (optional)


class SubQuery(BaseModel):
    """
    One decomposed sub-item from the user's query.
    Each sub-query becomes a separate platform API search.

    For STRUCTURED output, the `title` becomes the collapsable section header
    that the user sees. When they click/expand it, the embed plays the moment.
    For DIRECT output, the title is still useful as a label above the embed.
    """
    proposed_video_query: str = Field(
        ..., description="Optimized search string for the platform API"
    )
    reasoning: str = Field(
        ..., description="Agent's reasoning for why this sub-query. "
                         "Used later as the similarity-search anchor."
    )
    title: str = Field(
        default="",
        description="Human-readable title for this sub-query. "
                    "Becomes the collapsable section header in structured output. "
                    "Example: 'React Fundamentals: Components & JSX'"
    )
    order: int = Field(
        default=0, description="Sequence order for structured output"
    )


class ClassifierOutput(BaseModel):
    """
    Full output from the query classifier.
    Either contains clarifying_questions (needs more info)
    or the resolved classification (ready to search).
    """
    # If set, the classifier needs more info before proceeding
    needs_clarification: bool = False
    clarifying_questions: List[ClarifyingQuestion] = Field(default_factory=list)

    # Resolved classification (populated when needs_clarification=False)
    platform: Platform = Platform.YOUTUBE
    output_format: OutputFormat = OutputFormat.DIRECT
    sub_queries: List[SubQuery] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Search Results
# ---------------------------------------------------------------------------

class VideoSearchResult(BaseModel):
    """Metadata returned from a platform search API."""
    video_id: str
    url: str
    title: str
    duration: float = 0  # seconds
    thumbnail: str = ""
    channel: str = ""
    view_count: int = 0
    description: str = ""
    platform: Platform = Platform.YOUTUBE
    has_transcript: bool = True  # For YouTube; assumed False for TikTok/X


# ---------------------------------------------------------------------------
# Transcript Segments
# ---------------------------------------------------------------------------

class TranscriptSegment(BaseModel):
    """A single transcript segment (word or sentence level)."""
    text: str
    start: float
    end: float
    duration: float = 0


class EmbeddedSegment(BaseModel):
    """A 5-minute transcript chunk ready for vector storage."""
    video_id: str
    segment_index: int
    start_time: float
    end_time: float
    text: str
    embedding: List[float] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Moment Finding
# ---------------------------------------------------------------------------

class FoundMoment(BaseModel):
    """A specific moment found within a video transcript."""
    video_id: str
    start: float           # Global timestamp in seconds
    end: float             # Global timestamp in seconds
    title: str             # Short description (3-8 words) of the moment itself
    description: str = ""  # Why this moment matches the query
    embed_url: str = ""    # Full embed URL with ?start=X&end=Y
    video_title: str = ""  # YouTube video title — displayed in collapsible headers
    platform: Platform = Platform.YOUTUBE
    sub_query_order: int = 0  # Which sub-query this answers
    sub_query_title: str = ""  # Collapsable section header (from classifier's sub-query title)


# ---------------------------------------------------------------------------
# Pipeline Result
# ---------------------------------------------------------------------------

class FindrResult(BaseModel):
    """
    Final result returned to the frontend.
    Contains either a structured (collapsable) or direct (inline) response.
    """
    search_id: str
    query: str
    output_format: OutputFormat
    platform: Platform
    moments: List[FoundMoment] = Field(default_factory=list)
    clarifying_questions: List[ClarifyingQuestion] = Field(default_factory=list)
    status: str = "processing"  # processing | complete | error
    error_message: Optional[str] = None
