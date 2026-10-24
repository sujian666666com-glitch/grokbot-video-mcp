from __future__ import annotations

import asyncio
import time
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
import bridge

mcp = FastMCP('grokbot-video')
bridge.prepare()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
async def generate_video(prompt: str, image_paths: list[str] | None = None, duration_seconds: int = 6,
                         aspect_ratio: str = '16:9', silent: bool = True) -> dict:
    """Submit text and optional local PNG/JPEG/WebP reference images to the user's Grok Bot.

    Sends the selected images to Grok Bot via private per-job HTTPS links and consumes
    the user's Bot/video allowance. Returns a job_id, not a completed video. Do not
    automatically resubmit submission_unknown jobs. Generate only when the user requested video.
    """
    return await asyncio.to_thread(bridge.submit_video, prompt, image_paths, duration_seconds, aspect_ratio, silent)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
def get_video_status(job_id: str) -> dict:
    """Read one job's actual callbacks, image transfer evidence, errors and verified video metadata."""
    return bridge.public_job(bridge.get_job(job_id))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
async def wait_video(job_id: str, timeout_seconds: int = 30) -> dict:
    """Wait up to 45 seconds for success/failure; returns current status if still running."""
    deadline = time.monotonic() + min(max(timeout_seconds, 0), 45)
    while True:
        job = bridge.get_job(job_id)
        if job['status'] in ('succeeded', 'failed', 'submission_unknown') or time.monotonic() >= deadline:
            return bridge.public_job(job)
        await asyncio.sleep(1)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def download_video(job_id: str, output_dir: str | None = None) -> dict:
    """Copy a verified completed MP4 to Downloads/grokbot-videos or the requested directory.
    Existing different files are never overwritten. Returns the absolute playable video path.
    """
    return bridge.download_video(job_id, output_dir)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
def list_video_jobs(limit: int = 10) -> list[dict]:
    """List up to 20 recent local jobs without exposing webhook keys or capability tokens."""
    with bridge.db() as c:
        rows = c.execute('SELECT document FROM jobs ORDER BY rowid DESC LIMIT ?', (min(max(limit, 1), 20),)).fetchall()
    return [bridge.public_job(bridge.json.loads(row[0])) for row in rows]


if __name__ == '__main__':
    mcp.run(transport='stdio')
