"""NetEase Yidun (易盾) — captcha / dun.163.com.

Public SDK host: `cstaticdun.126.net`.
API host: `c.dun.163.com`.
"""

from __future__ import annotations

API_HOST = "c.dun.163.com"
GET_CONF = "https://c.dun.163.com/api/v2/get"  # challenge metadata
IMAGE_HOST = "https://necaptcha.nosdn.127.net"  # OSS bucket for puzzle PNGs
CHECK = "https://c.dun.163.com/api/v2/check"  # verify
