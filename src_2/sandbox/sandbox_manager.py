"""
Editr Sandbox Manager

Manages Daytona sandbox lifecycle for video processing.
Reuses patterns from src/agents/visual_verify.py:352-460.

Each sandbox processes one video: download → scenedetect → render.
Uses a custom snapshot with FFmpeg, yt-dlp, and scenedetect pre-installed.
"""

import asyncio
import json
import logging
import time
from typing import Optional

from src_2.config import (
    DAYTONA_API_KEY,
    DAYTONA_EDITOR_SNAPSHOT,
    DAYTONA_SANDBOX_TIMEOUT,
    DAYTONA_TARGET,
)
from src_2.scorer.scorer_models import EditAnalysis
from src_2.sandbox.scripts import (
    DOWNLOAD_SCRIPT,
    SCENEDETECT_SCRIPT,
)

logger = logging.getLogger(__name__)


class SandboxManager:
    """Manages a single Daytona sandbox for video processing."""

    def __init__(self):
        self._sandbox = None
        self._daytona = None

    async def create(self):
        """Create an ephemeral Daytona sandbox with the editr snapshot."""
        try:
            from daytona import (
                Daytona,
                DaytonaConfig,
                CreateSandboxFromSnapshotParams,
            )
        except ImportError:
            raise RuntimeError("daytona package not installed. Run: pip install daytona")

        if not DAYTONA_API_KEY:
            raise RuntimeError("DAYTONA_API_KEY not configured")

        t0 = time.perf_counter()
        logger.info("[Sandbox] Creating ephemeral sandbox...")

        config = DaytonaConfig(
            api_key=DAYTONA_API_KEY,
            target=DAYTONA_TARGET,
        )
        self._daytona = Daytona(config)

        params = CreateSandboxFromSnapshotParams(
            snapshot=DAYTONA_EDITOR_SNAPSHOT,
            language="python",
            ephemeral=True,
            auto_stop_interval=5,  # 5 min safety net
        )

        self._sandbox = await asyncio.to_thread(
            self._daytona.create, params, DAYTONA_SANDBOX_TIMEOUT
        )

        elapsed = time.perf_counter() - t0
        logger.info(
            f"[Sandbox] Created in {elapsed:.2f}s | "
            f"id={self._sandbox.id} | snapshot={DAYTONA_EDITOR_SNAPSHOT}"
        )

    async def download_video(self, url: str, video_id: str) -> str:
        """
        Download a video into the sandbox.
        Returns the local path inside the sandbox.
        """
        if not self._sandbox:
            raise RuntimeError("Sandbox not created")

        output_path = f"/tmp/{video_id}.mp4"
        script = DOWNLOAD_SCRIPT.format(url=url, output_path=output_path)

        t0 = time.perf_counter()
        result = await asyncio.to_thread(
            self._sandbox.process.code_run, script, timeout=60
        )

        if result.exit_code != 0:
            raise RuntimeError(
                f"Video download failed (exit {result.exit_code}): "
                f"{result.result[:300]}"
            )

        elapsed = time.perf_counter() - t0
        logger.info(f"[Sandbox] Video downloaded in {elapsed:.2f}s -> {output_path}")
        return output_path

    async def run_scenedetect(self, video_path: str) -> EditAnalysis:
        """
        Run PySceneDetect on a video file inside the sandbox.
        Returns EditAnalysis with scene metrics.
        """
        if not self._sandbox:
            raise RuntimeError("Sandbox not created")

        script = SCENEDETECT_SCRIPT.format(video_path=video_path)

        t0 = time.perf_counter()
        result = await asyncio.to_thread(
            self._sandbox.process.code_run, script, timeout=30
        )

        if result.exit_code != 0:
            logger.warning(
                f"[Sandbox] SceneDetect failed (exit {result.exit_code}): "
                f"{result.result[:200]}"
            )
            return EditAnalysis()

        elapsed = time.perf_counter() - t0
        logger.info(f"[Sandbox] SceneDetect completed in {elapsed:.2f}s")

        # Parse JSON output from script
        try:
            output_lines = result.result.strip().split("\n")
            data = json.loads(output_lines[-1])
            return EditAnalysis(
                scene_count=data.get("scene_count", 0),
                cuts_per_minute=data.get("cuts_per_minute", 0),
                avg_scene_duration=data.get("avg_scene_duration", 0),
                edit_level=data.get("edit_level", "unknown"),
                max_scene_gap=data.get("max_scene_gap", 0),
            )
        except (json.JSONDecodeError, IndexError) as e:
            logger.warning(f"[Sandbox] Failed to parse SceneDetect output: {e}")
            return EditAnalysis()

    async def exec_script(self, script: str, timeout: int = 60) -> str:
        """Run an arbitrary Python script in the sandbox and return stdout."""
        if not self._sandbox:
            raise RuntimeError("Sandbox not created")

        result = await asyncio.to_thread(
            self._sandbox.process.code_run, script, timeout=timeout
        )

        if result.exit_code != 0:
            raise RuntimeError(
                f"Script failed (exit {result.exit_code}): {result.result[:300]}"
            )

        return result.result

    async def exec_command(self, command: str, timeout: int = 120) -> str:
        """Run a shell command in the sandbox and return stdout."""
        if not self._sandbox:
            raise RuntimeError("Sandbox not created")

        result = await asyncio.to_thread(
            self._sandbox.process.exec, command, timeout=timeout
        )

        if result.exit_code != 0:
            raise RuntimeError(
                f"Command failed (exit {result.exit_code}): {result.result[:300]}"
            )

        return result.result

    async def upload_file(self, local_content: bytes, remote_path: str):
        """Write file content into the sandbox."""
        if not self._sandbox:
            raise RuntimeError("Sandbox not created")

        import base64
        b64 = base64.b64encode(local_content).decode()
        script = f"""
import base64
data = base64.b64decode("{b64}")
with open("{remote_path}", "wb") as f:
    f.write(data)
print("OK")
"""
        await asyncio.to_thread(
            self._sandbox.process.code_run, script, timeout=30
        )

    async def read_file_b64(self, remote_path: str) -> str:
        """Read a file from the sandbox as base64."""
        if not self._sandbox:
            raise RuntimeError("Sandbox not created")

        script = f"""
import base64
with open("{remote_path}", "rb") as f:
    data = f.read()
print(base64.b64encode(data).decode())
"""
        result = await asyncio.to_thread(
            self._sandbox.process.code_run, script, timeout=60
        )

        if result.exit_code != 0:
            raise RuntimeError(f"File read failed: {result.result[:200]}")

        return result.result.strip().split("\n")[-1]

    async def cleanup(self):
        """Stop the sandbox (ephemeral auto-deletes)."""
        if self._sandbox:
            try:
                await asyncio.to_thread(self._sandbox.stop, 10)
                logger.debug(f"[Sandbox] {self._sandbox.id} stopped")
            except Exception as e:
                logger.debug(f"[Sandbox] Stop failed (will auto-delete): {e}")
            self._sandbox = None
