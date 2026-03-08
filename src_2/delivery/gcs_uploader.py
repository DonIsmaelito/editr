"""
Editr GCS Uploader

Uploads rendered videos from the sandbox to Google Cloud Storage
and generates signed download URLs.
"""

import asyncio
import base64
import logging
import time
from datetime import timedelta

from src_2.config import GCS_BUCKET, GOOGLE_CLOUD_API_KEY

logger = logging.getLogger(__name__)


async def upload_to_gcs(
    sandbox_path: str,
    video_id: str,
    job_id: str,
    sandbox,
) -> str:
    """
    Read the rendered video from the sandbox, upload to GCS,
    and return a signed URL valid for 7 days.
    """
    t0 = time.perf_counter()

    # Read file from sandbox as base64
    file_b64 = await sandbox.read_file_b64(sandbox_path)
    file_bytes = base64.b64decode(file_b64)

    logger.info(
        f"[GCS] Read {len(file_bytes) / (1024*1024):.1f}MB from sandbox"
    )

    # Upload to GCS
    try:
        import google.cloud.storage as storage
    except ImportError:
        raise RuntimeError(
            "google-cloud-storage not installed. Run: pip install google-cloud-storage"
        )

    blob_name = f"edits/{job_id}/{video_id}_edited.mp4"

    client = await asyncio.to_thread(storage.Client)
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)

    await asyncio.to_thread(
        blob.upload_from_string,
        file_bytes,
        content_type="video/mp4",
    )

    # Generate signed URL (7 days)
    signed_url = await asyncio.to_thread(
        blob.generate_signed_url,
        version="v4",
        expiration=timedelta(days=7),
        method="GET",
    )

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[GCS] Uploaded in {elapsed:.2f}s | "
        f"blob={blob_name} | size={len(file_bytes) / (1024*1024):.1f}MB"
    )

    return signed_url
