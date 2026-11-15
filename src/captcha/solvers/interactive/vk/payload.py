"""VK payload: which of the three protocols a task is, and the page to open.

One catalog type covers three different flows, so the discriminator lives here
and the solver only branches on a mode string. Key aliases are collected in one
place because 2captcha clients send the legacy image task four different ways.

  * `image` — API error 14: `captcha_sid` + `captcha_img`; OCR, HTTP only.
  * `steps` — `captchaNotRobot.getContent` image+steps, a kaleidoscope path.
  * `token` — `redirectUri` to `id.vk.com/not_robot_captcha`; browser only.
"""

from __future__ import annotations

from captcha.domain import Task
from captcha.domain.errors import UnsolvableError

from .._browser import website_url


def mode_of(task: Task) -> str:
    """Explicit `mode`, else inferred from the payload keys; default `token`."""
    payload = task.payload or {}
    mode = payload.get("mode")
    if isinstance(mode, str) and mode.strip():
        return mode.strip().lower()
    if payload.get("image") and payload.get("steps"):
        return "steps"
    if (
        payload.get("sid")
        or payload.get("captcha_sid")
        or payload.get("captchaSid")
        or payload.get("captcha_img")
        or payload.get("captchaImg")
    ):
        return "image"
    return "token"


def sid_of(task: Task) -> str:
    payload = task.payload or {}
    for value in (payload.get("sid"), payload.get("captcha_sid"), payload.get("captchaSid")):
        if value is not None and str(value).strip():
            return str(value).strip()
    if image_url_of(task) is not None:
        return ""
    raise UnsolvableError("vk image payload missing sid/captcha_sid", context={"type": task.type.value})


def image_url_of(task: Task) -> str | None:
    """API error 14 also ships a full `captcha_img` URL; prefer it when present."""
    payload = task.payload or {}
    for value in (payload.get("captcha_img"), payload.get("captchaImg"), payload.get("image_url")):
        if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
            return value.strip()
    return None


def site_url(task: Task) -> str:
    try:
        return website_url(task, "redirectUri", "redirect_uri")
    except UnsolvableError as exc:
        raise UnsolvableError(
            "vk payload missing redirectUri/websiteURL",
            context={"type": task.type.value},
        ) from exc
