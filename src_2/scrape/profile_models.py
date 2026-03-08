"""
Editr Profile Models

Pydantic models for TikTok profile scraping output.
"""

from typing import List, Optional

from pydantic import BaseModel


class VideoMetric(BaseModel):
    video_id: str
    desc: str
    play_count: int
    digg_count: int
    comment_count: int
    share_count: int
    collect_count: int = 0
    duration: int
    create_time: int
    play_addr: str = ""
    cover_url: str = ""
    music_title: str = ""
    challenges: List[str] = []


class ProfileData(BaseModel):
    username: str
    follower_count: int = 0
    following_count: int = 0
    total_likes: int = 0
    bio: str = ""
    verified: bool = False
    avatar_url: str = ""
    videos: List[VideoMetric] = []
