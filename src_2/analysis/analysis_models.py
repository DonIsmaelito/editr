"""
Editr Analysis Models

Data models for the 4 Gemini agent outputs and the merged EditPlan.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CaptionSegment:
    start: float
    end: float
    text: str
    emphasis: bool = False


@dataclass
class TranscriptAnalysis:
    captions: List[CaptionSegment] = field(default_factory=list)
    key_moments: List[float] = field(default_factory=list)
    hook_timestamp: float = 0.0


@dataclass
class VisualCue:
    timestamp: float
    cue_type: str         # "reaction", "transition", "highlight", "product"
    description: str
    zoom_suggested: bool = False
    zoom_target: str = ""  # "face", "object", "text"


@dataclass
class VisualCueAnalysis:
    cues: List[VisualCue] = field(default_factory=list)


@dataclass
class MusicSuggestion:
    genre: str
    mood: str
    bpm: int
    prompt: str
    start: float = 0.0
    end: float = 0.0


@dataclass
class MusicAnalysis:
    suggestions: List[MusicSuggestion] = field(default_factory=list)
    original_has_music: bool = True
    replace_music: bool = False


@dataclass
class EditMechanicsSuggestion:
    timestamp: float
    mechanic_type: str    # "jump_cut", "zoom_in", "zoom_out", "popup", "text_overlay"
    description: str
    duration: float = 1.0


@dataclass
class EditMechanics:
    suggestions: List[EditMechanicsSuggestion] = field(default_factory=list)
    pacing_score: float = 0.0  # 0-1, how well-paced the original is
