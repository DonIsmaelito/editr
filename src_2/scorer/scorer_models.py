"""
Editr Scorer Models

Dataclasses for video scoring and edit analysis.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EditAnalysis:
    scene_count: int = 0
    cuts_per_minute: float = 0.0
    avg_scene_duration: float = 0.0
    edit_level: str = "unknown"   # raw | light | moderate | heavy
    max_scene_gap: float = 0.0


@dataclass
class ScoredVideo:
    video_id: str
    desc: str
    play_count: int
    digg_count: int
    comment_count: int
    share_count: int
    collect_count: int
    duration: int
    create_time: int
    play_addr: str
    cover_url: str
    music_title: str
    challenges: list = field(default_factory=list)

    # Computed metrics
    engagement_rate: float = 0.0
    views_ratio: float = 0.0
    content_resonance: float = 0.0
    fixability_score: float = 0.0
    selected: bool = False

    # Post-download analysis (filled in sandbox)
    edit_analysis: Optional[EditAnalysis] = None
