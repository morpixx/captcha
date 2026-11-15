"""reCAPTCHA v2 audio pipeline: anchor -> reload q -> reload a -> userverify.

Transcription is injected (`ctx.extra["audio_transcriber"]` or a
`CaptchaType.AUDIO` solver). Missing injection is ERR_CONFIG, not a silent retry.
"""

from __future__ import annotations

from captcha.domain import SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.solvers import SolverContext

from ._asr import transcribe
from ._common import (
    GOOGLE_HOST,
    Endpoints,
    encode_origin,
    extract_anchor_token,
    extract_audio_url,
    extract_reload_token,
    initial_version,
    random_cb,
    resolve_version,
)
from ._payload import download_audio


async def solve_audio(
    task: Task,
    ctx: SolverContext,
    *,
    endpoints: Endpoints,
    anchor_size: str,
) -> SolverResult:
    """Run the full audio bypass for one v2 task."""
    if ctx.http is None:
        raise ConfigError("reCAPTCHA v2 requires ctx.http", context={"task": str(task.id)})
    sitekey = _require(task.sitekey, "sitekey", task)
    site_url = _require(task.site_url, "site_url", task)

    version = initial_version(task)
    anchor = await _fetch_anchor(
        ctx,
        endpoints,
        sitekey=sitekey,
        site_url=site_url,
        version=version,
        size=anchor_size,
        language=task.language or "en",
    )
    anchor_token = extract_anchor_token(anchor.text)
    if not anchor_token:
        refreshed = await resolve_version(task, ctx, endpoints)
        if refreshed != version:
            version = refreshed
            anchor = await _fetch_anchor(
                ctx,
                endpoints,
                sitekey=sitekey,
                site_url=site_url,
                version=version,
                size=anchor_size,
                language=task.language or "en",
            )
            anchor_token = extract_anchor_token(anchor.text)
    if not anchor_token:
        raise ChallengeExpiredError(
            "reCAPTCHA anchor returned no token — release slug or origin stale",
            context={"status": anchor.status, "url": anchor.final_url, "size": anchor_size},
        )

    reload_headers = {
        "content-type": "application/x-www-form-urlencoded",
        "referer": anchor.final_url or site_url,
        "origin": GOOGLE_HOST,
        "accept": "*/*",
    }
    swap = await ctx.http.post(
        endpoints.reload,
        params={"k": sitekey},
        data={
            "v": version,
            "reason": "q",
            "c": anchor_token,
            "k": sitekey,
            "sa": "",
            "ds": "[]",
            "chr": "[0,0,0]",
        },
        headers=reload_headers,
    )
    challenge_token = extract_reload_token(swap.text)
    if not challenge_token:
        raise ChallengeExpiredError(
            "reCAPTCHA refused the challenge reload — session burned",
            context={"status": swap.status, "body_head": swap.text[:120]},
        )

    confirm = await ctx.http.post(
        endpoints.reload,
        params={"k": sitekey},
        data={
            "v": version,
            "reason": "a",
            "c": challenge_token,
            "k": sitekey,
            "sa": "",
        },
        headers=reload_headers,
    )
    audio_token = extract_reload_token(confirm.text)
    if not audio_token:
        raise ChallengeExpiredError(
            "reCAPTCHA refused the audio reload — audio challenge unavailable",
            context={"status": confirm.status, "body_head": confirm.text[:120]},
        )
    audio_url = extract_audio_url(confirm.text)

    mp3 = await download_audio(
        task,
        ctx,
        endpoints,
        token=audio_token,
        sitekey=sitekey,
        url=audio_url,
    )
    transcript = await transcribe(mp3, task=task, ctx=ctx)
    if not transcript:
        raise UnsolvableError(
            "audio transcription is empty",
            context={"bytes": len(mp3), "audio_token": audio_token[:24]},
        )

    verify = await ctx.http.post(
        endpoints.userverify,
        params={"k": sitekey},
        data={
            "v": version,
            "c": audio_token,
            "response": transcript,
            "t": "0",
            "ct": "0",
            "bg": "",
        },
        headers=reload_headers,
    )
    final_token = extract_reload_token(verify.text)
    if not final_token:
        raise UnsolvableError(
            "reCAPTCHA userverify rejected the transcript",
            context={
                "status": verify.status,
                "transcript_len": len(transcript),
                "body_head": verify.text[:120],
            },
        )

    return SolverResult(
        token=final_token,
        text=transcript,
        solve_mode=SolveMode.HTTP,
        extra={"audio_bytes": len(mp3)},
    )


async def _fetch_anchor(
    ctx: SolverContext,
    endpoints: Endpoints,
    *,
    sitekey: str,
    site_url: str,
    version: str,
    size: str,
    language: str,
):
    return await ctx.http.get(
        endpoints.anchor,
        params={
            "ar": "1",
            "k": sitekey,
            "co": encode_origin(site_url),
            "hl": language,
            "v": version,
            "size": size,
            "cb": random_cb(),
        },
        headers={"referer": site_url, "accept": "text/html,application/xhtml+xml"},
    )


def _require(value: str | None, field: str, task: Task) -> str:
    if not value:
        raise UnsolvableError(
            f"reCAPTCHA payload missing {field}",
            context={"task": str(task.id), "field": field},
        )
    return value
