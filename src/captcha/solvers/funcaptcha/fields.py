"""The wired field tables of an Arkose BDA (public RE, 2026-07).

Enhanced fingerprints are a ~90-entry key/value list plus 18 webgl_* fields.
The tables here are data, not policy: constants the bundle compares against
(platform math fingerprint, GREASE brands, codec probes) live next to the
builder so a build rotation is a one-file diff.

Two entries are approximations, marked FILL:

  * audio_codecs_extended_hash / video_codecs_extended_hash — a trimmed Chrome
    152 codec probe instead of the full output;
  * f58835f — probe names and order from the public dump, values from a stock
    Chrome/Windows profile.

Neither is cross-checked against fe, so drift there is a fidelity risk, not a
hard failure.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .devices import Device, js_heap_size_limit
from .hashes import khash, md5_hex

WEBGL_EXTENSIONS = (
    "ANGLE_instanced_arrays;EXT_blend_minmax;EXT_clip_control;EXT_color_buffer_half_float;"
    "EXT_depth_clamp;EXT_disjoint_timer_query;EXT_float_blend;EXT_frag_depth;"
    "EXT_polygon_offset_clamp;EXT_shader_texture_lod;EXT_texture_compression_bptc;"
    "EXT_texture_compression_rgtc;EXT_texture_filter_anisotropic;EXT_texture_mirror_clamp_to_edge;"
    "EXT_sRGB;KHR_parallel_shader_compile;OES_element_index_uint;OES_fbo_render_mipmap;"
    "OES_standard_derivatives;OES_texture_float;OES_texture_float_linear;OES_texture_half_float;"
    "OES_texture_half_float_linear;OES_vertex_array_object;WEBGL_blend_func_extended;"
    "WEBGL_color_buffer_float;WEBGL_compressed_texture_s3tc;WEBGL_compressed_texture_s3tc_srgb;"
    "WEBGL_debug_renderer_info;WEBGL_debug_shaders;WEBGL_depth_texture;WEBGL_draw_buffers;"
    "WEBGL_lose_context;WEBGL_multi_draw;WEBGL_polygon_mode"
)
WEBGL_EXTENSIONS_HASH = khash(WEBGL_EXTENSIONS, 0)
WEBGL_VERSION = "WebGL 1.0 (OpenGL ES 2.0 Chromium)"
WEBGL_SHADING_LANGUAGE = "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)"

#: V8/platform constants — every Chrome/Windows box reports exactly these.
MATH_FINGERPRINT = "e00752d866abf7fdf93bd4111bcfeb7b"
SUPPORTED_MATH_FUNCTIONS = "3f7aaba900fde542f258166cd2b71ef5"
DEFAULT_ENFORCEMENT_HASH = "2d1c8a89671586563cb793ae2399b954"
DEFAULT_DOCUMENT_REFERRER = "https://www.google.com/"

CHROME_MAJORS = (148, 149, 150, 151, 152)
GREASE_BRANDS = {152: "Not?A_Brand", 151: "Not_A Brand", 150: "Not)A;Brand", 149: "Not-A.Brand"}
TREE_STRUCTURES = ("[[[],[]],[[]],[],[]]", "[[],[],[],[[]],[]]", "[[],[],[],[[]],[[]],[],[]]")
AUDIO_FINGERPRINTS = (
    "124.04347776696522",
    "124.04347527516074",
    "124.04347527840094",
    "124.04347541281574",
    "124.0434806260746",
    "124.04344968475198",
    "35.7383295930922",
)
AUDIO_CODECS = (
    ('audio/ogg; codecs="vorbis"', "probably", True),
    ("audio/mpeg", "probably", True),
    ('audio/wav; codecs="1"', "probably", True),
    ('audio/mp4; codecs="mp4a.40.2"', "maybe", True),
    ("audio/aac", "probably", True),
)
VIDEO_CODECS = (
    ('video/ogg; codecs="theora"', "", False),
    ('video/mp4; codecs="avc1.42E01E"', "probably", True),
    ('video/webm; codecs="vp8"', "probably", True),
    ('video/mp4; codecs="mp4v.20.8"', "", False),
)
FEATURE_PROBES = (
    "permission_status",
    "eye_dropper",
    "audio_data",
    "writable_stream",
    "css_style_rule",
    "navigator_ua",
    "barcode_detector",
    "display_names",
    "contacts_manager",
    "svg_discard_element",
    "usb",
    "media_device",
    "playback_quality",
)


def webgl_fields(device: Device) -> list[tuple[str, Any]]:
    return [
        ("webgl_extensions", WEBGL_EXTENSIONS),
        ("webgl_extensions_hash", WEBGL_EXTENSIONS_HASH),
        ("webgl_renderer", "WebKit WebGL"),
        ("webgl_vendor", "WebKit"),
        ("webgl_version", WEBGL_VERSION),
        ("webgl_shading_language_version", WEBGL_SHADING_LANGUAGE),
        ("webgl_aliased_line_width_range", "[1, 1]"),
        ("webgl_aliased_point_size_range", "[1, 1024]"),
        ("webgl_antialiasing", "yes"),
        ("webgl_bits", "8,8,24,8,8,0"),
        ("webgl_max_params", "16,32,16384,1024,16384,16,16384,30,16,16,4096"),
        ("webgl_max_viewport_dims", "[32767, 32767]"),
        ("webgl_unmasked_vendor", device.unmasked_vendor),
        ("webgl_unmasked_renderer", device.gl_renderer),
        ("webgl_vsf_params", "23,127,127,23,127,127,23,127,127"),
        ("webgl_vsi_params", "0,31,30,0,31,30,0,31,30"),
        ("webgl_fsf_params", "23,127,127,23,127,127,23,127,127"),
        ("webgl_fsi_params", "0,31,30,0,31,30,0,31,30"),
    ]


def webgl_hash(fields: list[tuple[str, Any]]) -> str:
    """K over the entries, key and value alternating, with a trailing empty hash."""
    parts: list[str] = []
    for key, value in fields:
        parts.append(key)
        parts.append(value if isinstance(value, str) else "")
    return khash(",".join(parts) + ",webgl_hash_webgl,", 0)


def _codec_json(rows: Any) -> str:
    parts = []
    for mime, can_play, mse in rows:
        parts.append(
            json.dumps(mime)
            + ':{"canPlay":'
            + json.dumps(can_play)
            + ',"mediaSource":'
            + ("true" if mse else "false")
            + "}"
        )
    return "{" + ",".join(parts) + "}"


def feature_probe_hash() -> str:
    defined = {"usb", "media_device"}
    parts = [f"{name}: {'defined' if name in defined else 'true'}" for name in FEATURE_PROBES]
    return md5_hex(json.dumps(parts, separators=(",", ":")))


def jsbd(title: str) -> str:
    """Document state blob; DT is the embedding page's title."""
    return '{"HL":2,"NCE":true,"DT":' + json.dumps(title) + ',"NWD":"false","DMTO":1,"DOTO":1}'


def enhanced_fp(
    *,
    identity: dict[str, Any],
    device: Device,
    cfg_surl: str,
    language: str,
    location_href: str,
    document_referrer: str | None,
    enforcement_hash: str,
    build_id: str,
    rand: Any,
) -> list[tuple[str, Any]]:
    """Assemble the enhanced_fp list in wire order."""
    webgl = webgl_fields(device)
    webgl_digest = webgl_hash(webgl)
    rtt_type = WEBGL_EXTENSIONS_HASH[:3] + webgl_digest[:3] if len(webgl_digest) > 12 else "abcdef"
    brands = ["Chromium", "Google Chrome", GREASE_BRANDS.get(rand.choice(CHROME_MAJORS), "Not/A)Brand")]
    rand.shuffle(brands)
    out: list[tuple[str, Any]] = list(webgl)
    out += [
        ("webgl_hash_webgl", webgl_digest),
        ("user_agent_data_brands", ",".join(brands)),
        ("user_agent_data_mobile", False),
        ("navigator_connection_downlink", device.downlink),
        ("navigator_connection_downlink_max", None),
        ("network_info_rtt", device.rtt),
        ("network_info_save_data", device.save_data),
        ("network_info_rtt_type", rtt_type),
        ("screen_pixel_depth", device.color_depth * 3),
        ("navigator_device_memory", device.device_memory),
        ("navigator_languages", identity["languages"]),
        ("window_inner_width", device.inner_width),
        ("window_inner_height", device.inner_height),
        ("window_outer_width", device.outer_width),
        ("window_outer_height", device.outer_height),
        ("browser_detection_firefox", False),
        ("browser_detection_brave", False),
        ("9f41a2c", False),
        ("5c273b3", False),
        ("ce4046e", False),
        ("f58835f", feature_probe_hash()),
        ("browser_object_checks", md5_hex("chrome")),
        ("29s83ih9", md5_hex("false") + "\u2063"),
        ("audio_codecs", '{"ogg":"probably","mp3":"probably","wav":"probably","m4a":"maybe","aac":"probably"}'),
        ("audio_codecs_extended_hash", md5_hex(_codec_json(AUDIO_CODECS))),
        ("video_codecs", '{"ogg":"","h264":"probably","webm":"probably","mpeg4v":"","mpeg4a":"","theora":""}'),
        ("video_codecs_extended_hash", md5_hex(_codec_json(VIDEO_CODECS))),
        ("media_query_dark_mode", False),
        (
            "f9bf2db",
            '{"pc":"no-preference","ah":"hover","ap":"fine","p":"fine","h":"hover","u":"fast",'
            '"prm":"no-preference","prt":"no-preference","s":"enabled","fc":"none"}',
        ),
        ("headless_browser_phantom", False),
        ("headless_browser_selenium", False),
        ("headless_browser_nightmare_js", False),
        ("862f2c1", 4),
        ("1l2l5234ar2", f"{int(time.time() * 1000)}\u2063"),
        ("document__referrer", document_referrer or DEFAULT_DOCUMENT_REFERRER),
        ("window__ancestor_origins", []),
        ("window__tree_index", []),
        ("window__tree_structure", rand.choice(TREE_STRUCTURES)),
        ("window__location_href", location_href),
        ("client_config__sitedata_location_href", location_href),
        ("client_config__language", language.lower()),
        ("client_config__surl", cfg_surl),
        ("c8480e29a", md5_hex(cfg_surl) + "\u2062"),
        ("client_config__triggered_inline", False),
        ("mobile_sdk__is_sdk", False),
        ("z87b89t5", None),
        ("audio_fingerprint", rand.choice(AUDIO_FINGERPRINTS)),
        ("navigator_battery_charging", True),
        ("7541c2s", identity["7541c2s"]),
        ("1f220c9", identity["1f220c9"]),
        ("math_fingerprint", MATH_FINGERPRINT),
        ("supported_math_functions", SUPPORTED_MATH_FUNCTIONS),
        ("3f76dd27", "landscape-primary"),
        ("5dd48ca0", 5),
        ("4b4b269e68", str(uuid.uuid4())),
        ("6a62b2a558", enforcement_hash or DEFAULT_ENFORCEMENT_HASH),
        ("is_keyless", False),
        ("client_config__wait_for_settings", False),
        ("c2d2015", identity["c2d2015"]),
        ("43f2d94", []),
        ("20c15922", True),
        ("4f59ca8", None),
        ("3ea7194", {"supported": True, "formats": ["HDR10", "HLG"], "isHDR": False}),
        ("05d3d24", identity["05d3d24"]),
        ("speech_default_voice", identity["speech_default_voice"]),
        ("speech_voices_hash", identity["speech_voices_hash"]),
        ("83eb055", identity["83eb055"]),
        ("4ca87df3d1", "Ow=="),
        ("867e25e5d4", "Ow=="),
        ("d4a306884c", "Ow=="),
        ("vsadsa", rand.randint(9, 10)),
        ("basfas", [0, js_heap_size_limit(device.device_memory)]),
        ("lfasdgs", build_id or str(uuid.uuid4())),
    ]
    return out
