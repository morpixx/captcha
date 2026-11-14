"""Handcrafted default profile bundles.

These are **structurally correct examples**, not captures from live traffic:
use them to boot a dev environment, replace with real captures before
hitting protected targets. Each bundle is one JA3/JA4 + matching headers
+ matching canvas/WebGL/audio hashes.
"""

from __future__ import annotations

from captcha_fingerprint.profile import Profile

Bundle = Profile  # re-export alias; useful semantic name for callers


_CHROME_WIN_H2 = {"HEADER_TABLE_SIZE": 65536, "ENABLE_PUSH": 0, "INITIAL_WINDOW_SIZE": 6291456, "MAX_HEADER_LIST_SIZE": 262144}
_CHROME_MAC_H2 = dict(_CHROME_WIN_H2)
_FIREFOX_H2 = {"HEADER_TABLE_SIZE": 65536, "INITIAL_WINDOW_SIZE": 131072, "MAX_FRAME_SIZE": 16384}
_SAFARI_H2 = {"ENABLE_PUSH": 0, "INITIAL_WINDOW_SIZE": 4194304, "MAX_CONCURRENT_STREAMS": 100}


DEFAULT_BUNDLES: dict[str, Profile] = {
    "chrome-131-win": Profile(
        id="chrome-131-win",
        name="Chrome 131 / Windows 11",
        ja3="771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-17513,29-23-24,0",
        ja4="t13d1516h2_8daaf6152771_02713d6af862",
        http2_settings=_CHROME_WIN_H2,
        alpn=["h2", "http/1.1"],
        extensions=[0, 23, 65281, 10, 11, 35, 16, 5, 13, 18, 51, 45, 43, 27, 17513],
        canvas_hash="7f3a9e2b1c4d6a8f0e5b7c9d2a1e3f4b",
        webgl_renderer="ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        webgl_vendor="Google Inc. (NVIDIA)",
        audio_hash="124.04347527516074",
        fonts=["Arial", "Calibri", "Cambria", "Consolas", "Courier New", "Georgia", "Segoe UI", "Tahoma", "Times New Roman", "Verdana"],
        timezone="Europe/Berlin",
        webrtc_mode="default",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        accept_language="en-US,en;q=0.9",
        tags={"browser": "chrome", "os": "windows", "version": "131", "locale": "en-US"},
    ),
    "chrome-131-mac": Profile(
        id="chrome-131-mac",
        name="Chrome 131 / macOS Sonoma",
        ja3="771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-17513,29-23-24,0",
        ja4="t13d1516h2_8daaf6152771_02713d6af862",
        http2_settings=_CHROME_MAC_H2,
        alpn=["h2", "http/1.1"],
        extensions=[0, 23, 65281, 10, 11, 35, 16, 5, 13, 18, 51, 45, 43, 27, 17513],
        canvas_hash="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
        webgl_renderer="ANGLE (Apple, Apple M2 Pro, OpenGL 4.1)",
        webgl_vendor="Google Inc. (Apple)",
        audio_hash="124.04385296590137",
        fonts=["Helvetica", "Helvetica Neue", "Arial", "Times", "Times New Roman", "Courier", "Courier New", "Menlo", "Monaco", "Geneva"],
        timezone="America/Los_Angeles",
        webrtc_mode="default",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        accept_language="en-US,en;q=0.9",
        tags={"browser": "chrome", "os": "macos", "version": "131", "locale": "en-US"},
    ),
    "firefox-132-win": Profile(
        id="firefox-132-win",
        name="Firefox 132 / Windows 11",
        ja3="771,4865-4867-4866-49195-49199-52393-52392-49196-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-34-51-43-13-45-28-27-65037,29-23-24-25-256-257,0",
        ja4="t13d1715h2_5b57614c22b0_3d5424432f57",
        http2_settings=_FIREFOX_H2,
        alpn=["h2", "http/1.1"],
        extensions=[0, 23, 65281, 10, 11, 35, 16, 5, 34, 51, 43, 13, 45, 28, 27, 65037],
        canvas_hash="2c4e6b8a0f1d3c5b7a9e2d4f6c8b0a1e",
        webgl_renderer="ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
        webgl_vendor="Mozilla",
        audio_hash="35.74996031448245",
        fonts=["Arial", "Calibri", "Cambria", "Consolas", "Courier New", "Georgia", "Segoe UI", "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana"],
        timezone="Europe/Paris",
        webrtc_mode="default",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
        accept_language="en-US,en;q=0.5",
        tags={"browser": "firefox", "os": "windows", "version": "132", "locale": "en-US"},
    ),
    "firefox-132-mac": Profile(
        id="firefox-132-mac",
        name="Firefox 132 / macOS",
        ja3="771,4865-4867-4866-49195-49199-52393-52392-49196-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-34-51-43-13-45-28-27-65037,29-23-24-25-256-257,0",
        ja4="t13d1715h2_5b57614c22b0_3d5424432f57",
        http2_settings=_FIREFOX_H2,
        alpn=["h2", "http/1.1"],
        extensions=[0, 23, 65281, 10, 11, 35, 16, 5, 34, 51, 43, 13, 45, 28, 27, 65037],
        canvas_hash="9b8d7c6e5f4a3b2c1d0e9f8a7b6c5d4e",
        webgl_renderer="Apple M1",
        webgl_vendor="Apple",
        audio_hash="35.749468568325044",
        fonts=["Helvetica", "Helvetica Neue", "Arial", "Times", "Times New Roman", "Menlo", "Monaco", "Geneva", "Palatino", "Hoefler Text"],
        timezone="America/New_York",
        webrtc_mode="disable",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0",
        accept_language="en-US,en;q=0.5",
        tags={"browser": "firefox", "os": "macos", "version": "132", "locale": "en-US"},
    ),
    "safari-18-mac": Profile(
        id="safari-18-mac",
        name="Safari 18 / macOS Sonoma",
        ja3="771,4865-4866-4867-49196-49195-52393-49200-49199-52392-49188-49187-49162-49161-52394-49192-49191-49172-49171-157-156-61-60-53-47-49160-49170-10,0-23-65281-10-11-16-5-13-18-51-45-43-27-21,29-23-24-25,0",
        ja4="t13d1516h2_7a1b8c9d0e2f_safari18macpad",
        http2_settings=_SAFARI_H2,
        alpn=["h2", "http/1.1"],
        extensions=[0, 23, 65281, 10, 11, 16, 5, 13, 18, 51, 45, 43, 27, 21],
        canvas_hash="d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9",
        webgl_renderer="Apple GPU",
        webgl_vendor="Apple Inc.",
        audio_hash="124.0434806260746",
        fonts=["Helvetica", "Helvetica Neue", "Arial", "Times", "Times New Roman", "Courier", "Menlo", "Monaco", "SF Pro", "San Francisco"],
        timezone="America/Los_Angeles",
        webrtc_mode="default",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        accept_language="en-US,en;q=0.9",
        tags={"browser": "safari", "os": "macos", "version": "18", "locale": "en-US"},
    ),
    "chrome-131-android": Profile(
        id="chrome-131-android",
        name="Chrome 131 / Android 14",
        ja3="771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-17513,29-23-24,0",
        ja4="t13d1516h2_8daaf6152771_mobile7ab12c3",
        http2_settings=_CHROME_WIN_H2,
        alpn=["h2", "http/1.1"],
        extensions=[0, 23, 65281, 10, 11, 35, 16, 5, 13, 18, 51, 45, 43, 27, 17513],
        canvas_hash="3f5e7d9c1b2a4e6f8d0c2b4a6e8f0d2c",
        webgl_renderer="Adreno (TM) 740",
        webgl_vendor="Qualcomm",
        audio_hash="124.04347527516074",
        fonts=["Roboto", "Noto Sans", "Noto Serif", "Droid Sans", "Droid Serif", "Google Sans"],
        timezone="Europe/Moscow",
        webrtc_mode="default",
        user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        accept_language="ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        tags={"browser": "chrome", "os": "android", "version": "131", "locale": "ru-RU", "mobile": "1"},
    ),
}
