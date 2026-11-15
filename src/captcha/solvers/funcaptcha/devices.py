"""Device population the BDA samples from.

A BDA that always reports the same GPU, screen and core count is a fleet tell,
so every solve samples a device and recomputes the fields derived from it.
The pools are a trimmed copy of the public 2026-07 census (234 GPUs / 105
screens / 4 memory sizes there); enough spread to avoid a constant, small
enough to stay reviewable. `FILL`: widen from a fresh capture before canary.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

CHROME_COLOR_DEPTH = 24

# (vendor, unmasked renderer, unmasked vendor) — D3D11 strings Chrome emits on Windows.
GPUS: tuple[tuple[str, str, str], ...] = (
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)", "Google Inc. (NVIDIA)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)", "Google Inc. (NVIDIA)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)", "Google Inc. (Intel)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 2048SP Direct3D11 vs_5_0 ps_5_0, D3D11)", "Google Inc. (AMD)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)", "Google Inc. (Intel)"),
)

# (screen w, h, avail w, avail h)
SCREENS: tuple[tuple[int, int, int, int], ...] = (
    (1920, 1080, 1920, 1040),
    (1536, 864, 1536, 824),
    (1366, 768, 1366, 728),
    (2560, 1440, 2560, 1400),
    (1440, 900, 1440, 860),
)

DPI: tuple[float, ...] = (1, 1, 1, 1.25, 1.5)
CORES: tuple[int, ...] = (4, 8, 8, 12, 16)
MEMORY: tuple[int, ...] = (4, 8, 8, 8, 16)
# (downlink Mbps, rtt ms, saveData)
CONNECTIONS: tuple[tuple[float, int, bool], ...] = (
    (10.0, 50, False),
    (5.0, 100, False),
    (2.5, 150, False),
    (10.0, 0, False),
)


@dataclass(frozen=True, slots=True)
class Device:
    color_depth: int
    device_pixel_ratio: float
    screen_width: int
    screen_height: int
    avail_width: int
    avail_height: int
    inner_width: int
    inner_height: int
    outer_width: int
    outer_height: int
    hardware_concurrency: int
    device_memory: int
    downlink: float
    rtt: int
    save_data: bool
    gl_vendor: str
    gl_renderer: str
    unmasked_vendor: str


def sample_device(rng: random.Random) -> Device:
    """One device per solve. Window geometry is sampled apart from the screen."""
    gl_vendor, gl_renderer, unmasked = rng.choice(GPUS)
    screen_w, screen_h, avail_w, avail_h = rng.choice(SCREENS)
    downlink, rtt, save_data = rng.choice(CONNECTIONS)
    # A maximised window on every device is itself a fingerprint.
    outer_w = max(960, int(screen_w * rng.uniform(0.72, 1.0)))
    outer_h = max(540, int(screen_h * rng.uniform(0.68, 0.95)))
    inner_w = outer_w - rng.randint(0, 16)
    inner_h = outer_h - rng.randint(60, 140)
    return Device(
        color_depth=CHROME_COLOR_DEPTH,
        device_pixel_ratio=rng.choice(DPI),
        screen_width=screen_w,
        screen_height=screen_h,
        avail_width=avail_w,
        avail_height=avail_h,
        inner_width=inner_w,
        inner_height=inner_h,
        outer_width=outer_w,
        outer_height=outer_h,
        hardware_concurrency=rng.choice(CORES),
        device_memory=rng.choice(MEMORY),
        downlink=downlink,
        rtt=rtt,
        save_data=save_data,
        gl_vendor=gl_vendor,
        gl_renderer=gl_renderer,
        unmasked_vendor=unmasked,
    )


def js_heap_size_limit(device_memory: int) -> int:
    """V8's heap cap as Chrome reports it, scaled off device memory."""
    return int(device_memory) * 1024 * 1024 * 1024 // 4
