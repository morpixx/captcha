"""VK wire: the success-token hook, the token readers, and the image reply.

The VK ID page stores the result of `captchaNotRobot.check` in a JSON body that
it forwards to its opener. Rather than guess where the page puts it, the solver
installs a fetch/XHR hook before navigation and keeps any JSON that carries
`success_token` (or a plain token field). The DOM input and the
`captchaNotRobot` global are read as fallbacks.

The legacy image call returns bytes on success and a JSON error object for a
spent `sid`; `parse_image` separates those.
"""

from __future__ import annotations

import json
from typing import Any

from captcha.domain.errors import ChallengeExpiredError

#: Run before any page JS. Keeps only token-shaped JSON, bounded to 20 entries.
TOKEN_HOOK_JS = r"""
(() => {
  const store = (window.__vkCaptchaTokens = window.__vkCaptchaTokens || []);
  const keep = (url, body) => {
    try {
      const data = JSON.parse(body);
      const hit = data && (data.success_token || data.token || (data.data && data.data.success_token));
      if (hit) store.push({url: String(url || ''), token: String(hit)});
      if (store.length > 20) store.shift();
    } catch (e) {}
  };
  const originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = async function () {
      const response = await originalFetch.apply(this, arguments);
      try { keep(response.url, await response.clone().text()); } catch (e) {}
      return response;
    };
  }
  const open = XMLHttpRequest.prototype.open;
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__vkUrl = url;
    return open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    this.addEventListener('load', () => keep(this.__vkUrl, this.responseText));
    return send.apply(this, arguments);
  };
})();
"""

#: Poll for the token: hook first, then a hidden field, then the page global.
TOKEN_JS = r"""
() => {
  const hooked = window.__vkCaptchaTokens || [];
  if (hooked.length) return hooked[hooked.length - 1].token || '';
  const input = document.querySelector('input[name="success_token"], input[name="token"]');
  if (input && input.value) return input.value;
  if (typeof window.__vkSuccessToken === 'string' && window.__vkSuccessToken) return window.__vkSuccessToken;
  return '';
}
"""


def parse_image(reply: Any) -> bytes:
    """Legacy captcha bytes, or a retryable error when the sids are spent."""
    status = getattr(reply, "status", None)
    body = getattr(reply, "body", b"") or b""
    if status is not None and int(status) != 200:
        raise ChallengeExpiredError(f"vk captcha image: HTTP {status}", context={"status": status})
    if not body:
        raise ChallengeExpiredError("vk captcha image: empty body", context={})
    head = body.lstrip()[:1]
    if head in (b"{", b"<"):
        # VK answers a dead sid with a JSON/XML error object, not an image.
        message = ""
        try:
            data = json.loads(body)
            message = str((data.get("error") or {}).get("error_msg") or "")[:120]
        except Exception:
            message = body[:120].decode("utf-8", "replace")
        raise ChallengeExpiredError(
            "vk captcha image: provider returned an error object",
            context={"message": message},
        )
    return body
