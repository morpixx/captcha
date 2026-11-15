"""Audio payload download for the reCAPTCHA v2 audio path."""

from __future__ import annotations

from captcha.domain import Task
from captcha.domain.errors import ChallengeExpiredError
from captcha.solvers import SolverContext

from ._common import GOOGLE_HOST, Endpoints


async def download_audio(
    task: Task,
    ctx: SolverContext,
    endpoints: Endpoints,
    *,
    token: str,
    sitekey: str,
    url: str | None,
) -> bytes:
    """Fetch MP3 bytes, preferring the URL embedded in the audio reload."""
    headers = {"referer": task.site_url or "", "accept": "audio/mpeg,audio/*"}
    candidates: list[tuple[str, dict[str, str] | None]] = []
    if url:
        candidates.append((url if url.startswith("http") else GOOGLE_HOST + url, None))
    candidates.append((endpoints.payload, {"c": token, "k": sitekey, "id": "2"}))
    candidates.append((endpoints.payload, {"p": token}))

    last_status = None
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for candidate, params in candidates:
        key = (candidate, tuple(sorted((params or {}).items())))
        if key in seen:
            continue
        seen.add(key)
        resp = await ctx.http.get(candidate, params=params, headers=headers)
        last_status = resp.status
        if resp.status == 200 and resp.body:
            return resp.body

    raise ChallengeExpiredError(
        "reCAPTCHA audio payload unavailable",
        context={"status": last_status, "audio_token": token[:24]},
    )
