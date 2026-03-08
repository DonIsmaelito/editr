"""
Editr Sandbox Scripts

Self-contained Python scripts executed inside Daytona sandboxes.
Each script must be fully self-contained (no imports from our codebase).
Parameters are injected via string formatting before execution.
"""

# ---------------------------------------------------------------------------
# Video Download Script
# Uses yt-dlp as primary, falls back to direct HTTP download.
# ---------------------------------------------------------------------------
DOWNLOAD_SCRIPT = """
import subprocess
import sys
import os
import urllib.request

url = "{url}"
output_path = "{output_path}"

# Try direct HTTP download first (CDN URLs are usually direct)
try:
    urllib.request.urlretrieve(url, output_path)
    if os.path.getsize(output_path) > 1000:
        print(f"Downloaded via HTTP: {{os.path.getsize(output_path)}} bytes")
        sys.exit(0)
    os.remove(output_path)
except Exception as e:
    print(f"HTTP download failed: {{e}}, trying yt-dlp...")

# Fallback: yt-dlp
try:
    result = subprocess.run(
        ["yt-dlp", "-o", output_path, "--no-playlist", url],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode == 0 and os.path.exists(output_path):
        print(f"Downloaded via yt-dlp: {{os.path.getsize(output_path)}} bytes")
        sys.exit(0)
    print(f"yt-dlp error: {{result.stderr[:200]}}")
except Exception as e:
    print(f"yt-dlp failed: {{e}}")

print("ERROR: All download methods failed")
sys.exit(1)
"""

# ---------------------------------------------------------------------------
# PySceneDetect Analysis Script
# Outputs JSON with scene metrics to stdout.
# ---------------------------------------------------------------------------
SCENEDETECT_SCRIPT = """
import json
import sys

video_path = "{video_path}"

try:
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector

    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=27.0))
    scene_manager.detect_scenes(video)
    scene_list = scene_manager.get_scene_list()

    duration = video.duration.get_seconds()
    scene_count = len(scene_list)

    if scene_count > 0:
        cuts_per_minute = scene_count / (duration / 60) if duration > 0 else 0
        avg_scene_duration = duration / scene_count
        scene_durations = [
            (end - start).get_seconds()
            for start, end in scene_list
        ]
        max_scene_gap = max(scene_durations) if scene_durations else duration
    else:
        cuts_per_minute = 0
        avg_scene_duration = duration
        max_scene_gap = duration

    # Classify edit level
    if cuts_per_minute < 2:
        edit_level = "raw"
    elif cuts_per_minute < 6:
        edit_level = "light"
    elif cuts_per_minute < 15:
        edit_level = "moderate"
    else:
        edit_level = "heavy"

    result = {{
        "scene_count": scene_count,
        "cuts_per_minute": round(cuts_per_minute, 2),
        "avg_scene_duration": round(avg_scene_duration, 2),
        "edit_level": edit_level,
        "max_scene_gap": round(max_scene_gap, 2),
        "duration": round(duration, 2),
    }}
    print(json.dumps(result))

except Exception as e:
    print(json.dumps({{"error": str(e), "scene_count": 0, "cuts_per_minute": 0,
                       "avg_scene_duration": 0, "edit_level": "unknown",
                       "max_scene_gap": 0}}))
    sys.exit(0)
"""

# ---------------------------------------------------------------------------
# FFmpeg render command template
# Single-pass render with captions, zooms, popups, and audio mix.
# ---------------------------------------------------------------------------
FFMPEG_RENDER_TEMPLATE = """
import subprocess
import sys
import json

input_path = "{input_path}"
output_path = "{output_path}"
filter_complex = '''{filter_complex}'''
audio_inputs = {audio_inputs}

cmd = ["ffmpeg", "-y", "-i", input_path]

# Add audio input files
for audio_path in audio_inputs:
    cmd.extend(["-i", audio_path])

if filter_complex.strip():
    cmd.extend(["-filter_complex", filter_complex])
    cmd.extend(["-map", "[vout]", "-map", "[aout]"])
else:
    cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "23"])
    cmd.extend(["-c:a", "aac", "-b:a", "128k"])

cmd.extend([
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "23",
    "-c:a", "aac",
    "-b:a", "128k",
    "-movflags", "+faststart",
    output_path
])

print(f"Running: {{' '.join(cmd[:10])}}")
result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

if result.returncode != 0:
    print(f"FFmpeg error: {{result.stderr[:500]}}")
    sys.exit(1)

import os
print(f"Rendered: {{os.path.getsize(output_path)}} bytes")
"""
