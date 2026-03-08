"""
Editr Render Models

Models for the FFmpeg render pipeline — operations applied to the video.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CaptionOp:
    start: float
    end: float
    text: str
    style: str = "default"    # "default", "emphasis", "hook"
    position: str = "bottom"  # "bottom", "center", "top"


@dataclass
class PopupOp:
    timestamp: float
    duration: float
    image_path: str           # local path to PNG in sandbox
    position: str = "top_right"
    scale: float = 0.2


@dataclass
class ZoomOp:
    start: float
    end: float
    target_x: float           # 0-1 normalized
    target_y: float           # 0-1 normalized
    zoom_factor: float = 1.5


@dataclass
class AudioOp:
    start: float
    end: float
    audio_path: str           # local path in sandbox
    volume: float = 0.3       # mix level relative to original
    fade_in: float = 0.5
    fade_out: float = 0.5


@dataclass
class EditPlan:
    video_id: str
    duration: float
    captions: List[CaptionOp] = field(default_factory=list)
    popups: List[PopupOp] = field(default_factory=list)
    zooms: List[ZoomOp] = field(default_factory=list)
    audio_ops: List[AudioOp] = field(default_factory=list)
    output_resolution: str = "1080x1920"
    preset: str = "fast"
