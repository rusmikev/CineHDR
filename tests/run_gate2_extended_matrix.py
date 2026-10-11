#!/usr/bin/env python3

"""Extended Gate 2 validation suite: W3 (dither), W4 (screenshots), W5 (subtitles)."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from typing import Any
import zlib

try:
    from PIL import Image
except ImportError:
    Image = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = PROJECT_ROOT.parent

_default_source = WORK_ROOT / "mpv-gpu-next"
_default_prefix = WORK_ROOT / "mpv-gpu-next-prefix"

DEFAULT_MPV_SOURCE = Path(os.environ.get("CINEHDR_MPV_SOURCE", _default_source))
DEFAULT_MPV_PREFIX = Path(os.environ.get("CINEHDR_MPV_PREFIX", _default_prefix))

sys.path.insert(0, str(PROJECT_ROOT / "tests"))
import generate_patterns
import run_pixel_pipeline

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("gate2_extended")


def compile_probe_binary(temp_dir: Path, mpv_source: Path, mpv_prefix: Path) -> Path:
    binary = temp_dir / "pixel_validator_egl"
    run_pixel_pipeline.compile_probe(binary, mpv_source, mpv_prefix, os.environ.get("CC", "cc"))
    return binary


def run_probe_raw(
    binary: Path,
    fixture_path: Path,
    mode: str,
    api: str,
    decode_mode: str = "no",
    delivery_mode: str = "paused",
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    cmd = [
        str(binary),
        str(fixture_path),
        mode,
        api,
        "0.041667" if delivery_mode == "continuous-identical" else "0",
        decode_mode,
        delivery_mode,
    ]
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise RuntimeError(f"probe failed ({res.returncode}):\n{res.stderr}\n{res.stdout}")
    return json.loads(res.stdout)


def test_w3_dithering_matrix(binary: Path, cache_dir: Path) -> dict[str, Any]:
    logger.info("=== Executing W3: Dithering Matrix ===")
    sdr_fix = generate_patterns.ensure_fixture("sdr", cache_dir)
    hdr_fix = generate_patterns.ensure_fixture("hdr", cache_dir)
    sdr_fixture = Path(sdr_fix["path"])
    hdr_fixture = Path(hdr_fix["path"])

    results = {}
    for api in ("opengl", "opengl-next"):
        # 1. SDR with dither=no (strict monotonicity check)
        sdr_no_dither = run_probe_raw(
            binary, sdr_fixture, "sdr", api, "no", "paused",
            {"CINEHDR_PROBE_DITHER": "no"}
        )
        ramp_no = sdr_no_dither["readback"]["gradient_ramp"]
        monotonic = all(ramp_no[i] <= ramp_no[i+1] + 1e-5 for i in range(len(ramp_no)-1))

        # 2. SDR with dither=fruit, dither-depth=8
        sdr_dither_8 = run_probe_raw(
            binary, sdr_fixture, "sdr", api, "no", "paused",
            {"CINEHDR_PROBE_DITHER": "fruit", "CINEHDR_PROBE_DITHER_DEPTH": "8"}
        )
        ramp_dither = sdr_dither_8["readback"]["gradient_ramp"]
        has_dither_data = len(ramp_dither) == len(ramp_no)

        # 3. HDR with dither-depth=auto in RGBA16F (verify continuous levels & min step < 1/255)
        hdr_auto = run_probe_raw(
            binary, hdr_fixture, "hdr", api, "no", "paused",
            {"CINEHDR_PROBE_DITHER": "fruit", "CINEHDR_PROBE_DITHER_DEPTH": "auto"}
        )
        hdr_ramp = hdr_auto["readback"]["gradient_ramp"]
        diffs = [hdr_ramp[i+1] - hdr_ramp[i] for i in range(len(hdr_ramp)-1) if hdr_ramp[i+1] > hdr_ramp[i] + 1e-6]
        min_step = min(diffs) if diffs else 1.0
        unique_hdr_levels = len(set(round(val, 6) for val in hdr_ramp))

        # Step granularity must be well below 8-bit (1/255 ≈ 0.00392)
        not_quantized_8bit = min_step < (1.0 / 255.0)

        passed = monotonic and has_dither_data and not_quantized_8bit and (unique_hdr_levels >= 50)
        results[api] = {
            "sdr_monotonic_no_dither": monotonic,
            "sdr_dither_applied": has_dither_data,
            "hdr_min_step": min_step,
            "hdr_unique_ramp_levels": unique_hdr_levels,
            "hdr_not_8bit_quantized": not_quantized_8bit,
            "verdict": "PASS" if passed else "FAIL",
        }
        logger.info("API %s: monotonic=%s, dither_applied=%s, min_step=%.6f (< 0.00392), unique_levels=%d -> %s",
                    api, monotonic, has_dither_data, min_step, unique_hdr_levels, results[api]["verdict"])

    overall = "PASS" if all(r["verdict"] == "PASS" for r in results.values()) else "FAIL"
    return {"results": results, "verdict": overall}


def parse_png_dimensions(png_bytes: bytes) -> tuple[int, int]:
    """Parse width and height from PNG IHDR chunk."""
    if len(png_bytes) < 24 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid PNG header")
    width, height = struct.unpack(">II", png_bytes[16:24])
    return width, height


def check_png_chunk_integrity(png_bytes: bytes) -> tuple[bool, str, dict[str, Any]]:
    """Validate PNG signature, chunk structure, chunk CRC-32, and IDAT zlib stream.

    Operates autonomously on raw bytes before passing to image decoders.
    """
    if len(png_bytes) < 8 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return False, "invalid_png_signature", {}

    offset = 8
    chunks_found: list[str] = []
    idat_parts: list[bytes] = []
    seen_ihdr = False
    seen_iend = False

    while offset < len(png_bytes):
        if offset + 8 > len(png_bytes):
            return False, f"truncated_chunk_header_at_{offset}", {}
        chunk_len, chunk_type = struct.unpack(">I4s", png_bytes[offset:offset + 8])
        offset += 8

        if not seen_ihdr:
            if chunk_type != b"IHDR" or chunk_len != 13:
                return False, "invalid_first_chunk_must_be_ihdr", {}
            seen_ihdr = True

        if offset + chunk_len + 4 > len(png_bytes):
            cname = chunk_type.decode("latin1", errors="replace")
            return False, f"truncated_chunk_data_for_{cname}", {}

        chunk_data = png_bytes[offset:offset + chunk_len]
        offset += chunk_len

        stored_crc, = struct.unpack(">I", png_bytes[offset:offset + 4])
        offset += 4

        calc_crc = zlib.crc32(chunk_type + chunk_data) & 0xffffffff
        if calc_crc != stored_crc:
            cname = chunk_type.decode("latin1", errors="replace")
            return False, f"crc_mismatch_in_{cname}: stored 0x{stored_crc:08x} != calculated 0x{calc_crc:08x}", {}

        chunks_found.append(chunk_type.decode("latin1", errors="replace"))

        if chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            seen_iend = True
            break

    if not seen_ihdr:
        return False, "missing_ihdr_chunk", {}
    if not idat_parts:
        return False, "missing_idat_chunk", {}
    if not seen_iend:
        return False, "missing_iend_chunk", {}

    try:
        raw_scanlines = zlib.decompress(b"".join(idat_parts))
    except Exception as exc:
        return False, f"corrupted_idat_zlib_stream: {exc}", {}

    meta = {
        "chunks": chunks_found,
        "idat_chunks_count": len(idat_parts),
        "uncompressed_bytes": len(raw_scanlines),
    }
    return True, "valid", meta


def decode_and_validate_png(
    png_bytes: bytes,
    expected_dims: tuple[int, int] = (320, 180),
) -> tuple[bool, str, Any]:
    """Validate PNG integrity, dimensions, non-emptiness, and non-green state.

    1. Autonomously checks PNG chunk structure, chunk CRC-32, and IDAT zlib stream.
    2. Verifies dimensions from IHDR without requiring external libraries.
    3. If Pillow is unavailable, returns (False, "pillow_unavailable", None) gracefully.
    4. Validates frame non-emptiness and non-uninitialized state.

    Returns:
        (passed, reason, decoded_image_or_none)
    """
    chunk_valid, chunk_err, _ = check_png_chunk_integrity(png_bytes)
    if not chunk_valid:
        return False, chunk_err, None

    width, height = struct.unpack(">II", png_bytes[16:24])
    if (width, height) != expected_dims:
        return False, f"wrong_dimensions: expected {expected_dims}, got {(width, height)}", None

    if Image is None:
        return False, "pillow_unavailable", None

    try:
        bio = io.BytesIO(png_bytes)
        img = Image.open(bio)
        img.load()
    except Exception as exc:
        return False, f"corrupted_png: {exc}", None

    rgb_img = img.convert("RGB")
    extrema = rgb_img.getextrema()
    min_r, max_r = extrema[0]
    min_g, max_g = extrema[1]
    min_b, max_b = extrema[2]

    # Empty-frame structural check on Pillow-decoded 8-bit RGB (<= 2 code values).
    # ADR-0005's normalized PQ FBO threshold does not derive this PNG tolerance.
    if max_r <= 2 and max_g <= 2 and max_b <= 2:
        return False, "all_black_frame", None

    # Uninitialized YUV -> RGB conversion check:
    # All-zero YUV buffer (Y=0, U=0, V=0) converts to solid green (G >> 0, R=0, B=0 across all pixels)
    if min_g > max_r and min_g > max_b and min_g > 2:
        return False, "all_green_frame", None

    # Uniform single-color frame check
    if min_r == max_r and min_g == max_g and min_b == max_b:
        return False, "uniform_flat_frame", None

    return True, "valid", rgb_img


def validate_pattern_patches(img: Any) -> tuple[bool, str, dict[str, Any]]:
    """Validate structural invariants of deterministic 4x2 fixture patches in screenshot.

    Distinction of verification scope:
    - Structural checks (Image Structural Integrity): confirms valid rasterization,
      patch layout, monotonic contrast, channel dominance, and absence of pipeline corruption
      (such as uninitialized buffers or channel swaps).
    - Color accuracy certification: explicitly marked False. ADR-0005 defines analytical error
      bounds exclusively for offscreen FP16 FBO readback in normalized PQ coordinates.
      mpv screenshot-to-file saves high-bit-depth PNG images which are downscaled to 8-bit RGB
      in Pillow; multiplying normalized float PQ bounds by 255 does not constitute an analytical
      derivation of color accuracy for encoded PNG files.

    Structural criteria and conservative quantization tolerances (in 8-bit code values):
    - Black floor: max(black_rgb) <= 2 code values (conservative floor tolerance to detect
      true black patch rasterization without residual light leak).
    - Neutral axis: max(white_rgb) - min(white_rgb) <= 2 code values (tolerance to detect
      neutral axis preservation and absence of tinting).
    - Contrast monotonicity: lum(white) > lum(black).
    - Primary dominance (strict inequality, topological channel integrity):
        Red: R > G and R > B
        Green: G > R and G > B
        Blue: B > R and B > G
    """
    black_rgb = img.getpixel((40, 45))
    white_rgb = img.getpixel((280, 45))
    red_rgb = img.getpixel((40, 135))
    green_rgb = img.getpixel((120, 135))
    blue_rgb = img.getpixel((200, 135))

    details = {
        "diagnostic_patch_samples": {
            "black": black_rgb,
            "white": white_rgb,
            "red": red_rgb,
            "green": green_rgb,
            "blue": blue_rgb,
        },
        "color_accuracy_certified": False,
        "color_invariants": {
            "black_floor_pass": max(black_rgb) <= 2,
            "white_neutral_axis_pass": (max(white_rgb) - min(white_rgb)) <= 2,
            "white_contrast_pass": (sum(white_rgb) > sum(black_rgb)),
            "red_dominance_pass": (red_rgb[0] > max(red_rgb[1], red_rgb[2])),
            "green_dominance_pass": (green_rgb[1] > max(green_rgb[0], green_rgb[2])),
            "blue_dominance_pass": (blue_rgb[2] > max(blue_rgb[0], blue_rgb[1])),
        },
    }

    # 1. Black patch: W4 screenshot structural floor (<= 2 decoded 8-bit code values)
    if max(black_rgb) > 2:
        return False, f"black_patch_too_bright: {black_rgb} > 2", details

    # 2. White patch: neutral axis (delta <= 2 code values)
    if max(white_rgb) - min(white_rgb) > 2:
        return False, f"white_patch_not_neutral: delta {max(white_rgb) - min(white_rgb)} > 2", details

    # 3. White patch: monotonic contrast vs black
    if sum(white_rgb) <= sum(black_rgb):
        return False, f"white_not_brighter_than_black: white={white_rgb} black={black_rgb}", details

    # 4. Red primary: red dominant
    if not (red_rgb[0] > red_rgb[1] and red_rgb[0] > red_rgb[2]):
        return False, f"red_patch_not_dominant: {red_rgb}", details

    # 5. Green primary: green dominant
    if not (green_rgb[1] > green_rgb[0] and green_rgb[1] > green_rgb[2]):
        return False, f"green_patch_not_dominant: {green_rgb}", details

    # 6. Blue primary: blue dominant
    if not (blue_rgb[2] > blue_rgb[0] and blue_rgb[2] > blue_rgb[1]):
        return False, f"blue_patch_not_dominant: {blue_rgb}", details

    return True, "valid", details


def validate_subtitle_overlay(
    video_img: Any,
    sub_img: Any,
    sub_region: tuple[int, int, int, int] = (100, 120, 220, 175),
) -> tuple[bool, str, dict[str, Any]]:
    """Verify subtitle presence inside its region with 1-LSB tolerance outside.

    Geometry derivation:
    - Canvas: 320x180 (PlayResX: 320, PlayResY: 180)
    - Subtitle style: Alignment 2 (bottom center), MarginV 20px from bottom (Y=180)
    - Fontsize 20pt, text '■■■' (3 glyphs U+25A0) centered horizontally at X=160
    - Bounding box is strictly within X in [100, 220], Y in [120, 175]
    - Outside region covers Row 0 (Y < 90), Red Primary (X < 80), Gradient (X > 240)
    - Threshold changed_inside >= 50 is a conservative heuristic presence detector
      for rendered glyph pixels at 320x180 resolution (not an analytical proof of glyph area),
      discriminating actual subtitle rasterization from anti-aliasing jitter or empty rendering.
    - Outside-region criterion: changed_outside == 0 counts only pixels whose maximum
      channel difference exceeds 1 decoded 8-bit code value. A 1-LSB difference passes.
      max_outside_diff records the actual largest difference, including tolerated ones;
      it is an observation, not a zero-difference acceptance threshold.
    - White text (&H00FFFFFF) over bottom-center background (Green/Blue primary) strictly
      increases red component and overall luminance: avg_inside_lum_increase > 0
    - max_outside_diff is computed unconditionally for ALL outside pixels
    """
    if video_img.size != sub_img.size:
        return False, "size_mismatch", {}

    w, h = video_img.size
    min_x, min_y, max_x, max_y = sub_region

    changed_inside = 0
    changed_outside = 0
    max_outside_diff = 0
    inside_lum_diffs: list[float] = []

    for y in range(h):
        for x in range(w):
            p_vid = video_img.getpixel((x, y))
            p_sub = sub_img.getpixel((x, y))
            diff = max(abs(p_vid[c] - p_sub[c]) for c in range(3))
            inside = (min_x <= x <= max_x) and (min_y <= y <= max_y)

            if inside:
                if diff > 1:
                    changed_inside += 1
                    lum_vid = sum(p_vid) / 3.0
                    lum_sub = sum(p_sub) / 3.0
                    inside_lum_diffs.append(lum_sub - lum_vid)
            else:
                if diff > max_outside_diff:
                    max_outside_diff = diff
                if diff > 1:
                    changed_outside += 1

    details = {
        "changed_inside": changed_inside,
        "changed_outside": changed_outside,
        "max_outside_diff": max_outside_diff,
        "avg_inside_lum_increase": (sum(inside_lum_diffs) / len(inside_lum_diffs)) if inside_lum_diffs else 0.0,
    }

    if changed_outside > 0:
        return False, f"outside_region_modified: {changed_outside} pixels altered (max delta {max_outside_diff})", details

    if changed_inside < 50:
        return False, f"missing_subtitles: only {changed_inside} pixels changed in subtitle region (expected >= 50)", details

    if details["avg_inside_lum_increase"] <= 0.0:
        return False, f"subtitles_not_bright: average luminance increase {details['avg_inside_lum_increase']:.2f} <= 0", details

    return True, "valid", details


def _validate_probe_evidence(
    probe_res: dict[str, Any],
    expected_api: str,
    software_only: bool,
    expect_subtitles: bool = False,
    require_hardware_decoder: bool = False,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Validate runtime evidence from probe JSON against expected constraints."""
    active_api = probe_res.get("active_api")
    gl_renderer = probe_res.get("gl", {}).get("renderer", "")
    gl_vendor = probe_res.get("gl", {}).get("vendor", "")
    decode_info = probe_res.get("decode")
    decode_info = decode_info if isinstance(decode_info, dict) else {}
    requested_decoder = decode_info.get("requested")
    active_decoder = decode_info.get("active")
    screenshot_info = probe_res.get("screenshot", {})
    subtitles_info = probe_res.get("subtitles", {})

    evidence = {
        "active_api": active_api,
        "gl_renderer": gl_renderer,
        "gl_vendor": gl_vendor,
        "requested_decoder": requested_decoder,
        "active_decoder": active_decoder,
        "screenshot_status": screenshot_info.get("status"),
        "screenshot_error": screenshot_info.get("error"),
    }
    if expect_subtitles:
        evidence["subtitles"] = subtitles_info

    if active_api != expected_api:
        return False, f"api_mismatch: requested {expected_api}, active {active_api}", evidence

    if software_only:
        if active_decoder != "no":
            return False, f"software_only_violation: active decoder is {active_decoder} (expected 'no')", evidence
        is_sw_rasterizer = any(sw in gl_renderer.lower() for sw in ("llvmpipe", "softpipe", "swrast", "software"))
        if not is_sw_rasterizer:
            return False, f"software_only_violation: gl_renderer is '{gl_renderer}' (not a software rasterizer)", evidence

    # Pinned mpv's hwdec-current returns an API name when hardware decoding is
    # active (DOCS/man/input.rst); "no" means software and a missing value means
    # no decoder. Keep every named backend from its --hwdec interface eligible.
    hardware_backends = {
        "d3d11va", "d3d11va-copy", "videotoolbox", "videotoolbox-copy",
        "vaapi", "vaapi-copy", "nvdec", "nvdec-copy", "drm", "drm-copy",
        "vulkan", "vulkan-copy", "dxva2", "dxva2-copy", "vdpau", "vdpau-copy",
        "mediacodec", "mediacodec-copy", "cuda", "cuda-copy", "crystalhd", "rkmpp",
    }
    if require_hardware_decoder and (
        requested_decoder != "auto"
        or not isinstance(active_decoder, str)
        or active_decoder not in hardware_backends
    ):
        return False, (
            "active_hardware_decoder_unconfirmed: "
            f"requested={requested_decoder!r}, active={active_decoder!r}"
        ), evidence

    if screenshot_info.get("status", 0) != 0:
        err_msg = screenshot_info.get("error") or f"status_{screenshot_info.get('status')}"
        return False, f"screenshot_command_failed: {err_msg}", evidence

    if expect_subtitles:
        if not subtitles_info.get("ready"):
            err_msg = subtitles_info.get("error") or "subtitle_track_not_ready"
            return False, f"subtitles_not_ready: {err_msg}", evidence

    return True, None, evidence


def test_w4_screenshot_matrix(
    binary: Path,
    cache_dir: Path,
    temp_dir: Path,
    software_only: bool = False,
) -> dict[str, Any]:
    logger.info("=== Executing W4: mpv Screenshot-to-File Matrix (Multi-Mode / Subtitles / HWDEC / A/B) ===")
    results = {}

    # 1. Base Modes on opengl-next
    for mode in ("sdr", "hdr", "hlg"):
        fix = generate_patterns.ensure_fixture(mode, cache_dir)
        fixture_path = Path(fix["path"])
        shot_path = temp_dir / f"screenshot_{mode}_next.png"
        if shot_path.exists():
            shot_path.unlink()

        probe_res = run_probe_raw(
            binary, fixture_path, mode, "opengl-next", "no", "paused",
            {"CINEHDR_PROBE_SCREENSHOT_PATH": str(shot_path)}
        )
        ev_ok, ev_err, evidence = _validate_probe_evidence(probe_res, "opengl-next", software_only)
        if not ev_ok:
            results[f"{mode}_next"] = {"probe_evidence": evidence, "reason": ev_err, "verdict": "FAIL"}
            logger.error("Screenshot %s probe evidence validation failed: %s", mode, ev_err)
            continue

        if not shot_path.is_file():
            results[f"{mode}_next"] = {"file_exists": False, "probe_evidence": evidence, "verdict": "FAIL"}
            continue

        data = shot_path.read_bytes()
        valid_img, img_err, img = decode_and_validate_png(data)
        if not valid_img:
            results[f"{mode}_next"] = {
                "file_exists": True,
                "size_bytes": len(data),
                "image_valid": False,
                "probe_evidence": evidence,
                "reason": img_err,
                "verdict": "FAIL",
            }
            logger.error("Screenshot %s (opengl-next) failed decode: %s", mode, img_err)
            continue

        patches_valid, patch_err, patch_details = validate_pattern_patches(img)
        passed = patches_valid
        results[f"{mode}_next"] = {
            "file_exists": True,
            "size_bytes": len(data),
            "dimensions": img.size,
            "image_valid": True,
            "patches_valid": patches_valid,
            "patch_details": patch_details,
            "probe_evidence": evidence,
            "verdict": "PASS" if passed else "FAIL",
        }
        if not passed:
            results[f"{mode}_next"]["reason"] = patch_err
        logger.info("Screenshot %s (opengl-next): size=%d, dims=%dx%d, patches=%s -> %s",
                    mode, len(data), img.size[0], img.size[1], patches_valid, results[f"{mode}_next"]["verdict"])

    # 2. Legacy A/B comparison on HDR
    hdr_fix = generate_patterns.ensure_fixture("hdr", cache_dir)
    hdr_fixture = Path(hdr_fix["path"])
    shot_legacy = temp_dir / "screenshot_hdr_legacy.png"
    if shot_legacy.exists():
        shot_legacy.unlink()
    probe_res_leg = run_probe_raw(
        binary, hdr_fixture, "hdr", "opengl", "no", "paused",
        {"CINEHDR_PROBE_SCREENSHOT_PATH": str(shot_legacy)}
    )
    ev_ok, ev_err, evidence_leg = _validate_probe_evidence(probe_res_leg, "opengl", software_only)
    if not ev_ok:
        results["hdr_legacy_ab"] = {"probe_evidence": evidence_leg, "reason": ev_err, "verdict": "FAIL"}
    elif shot_legacy.is_file():
        data_leg = shot_legacy.read_bytes()
        valid_leg, leg_err, img_leg = decode_and_validate_png(data_leg)
        if valid_leg:
            patches_leg, patch_err, patch_details = validate_pattern_patches(img_leg)
            results["hdr_legacy_ab"] = {
                "file_exists": True,
                "size_bytes": len(data_leg),
                "dimensions": img_leg.size,
                "image_valid": True,
                "patches_valid": patches_leg,
                "patch_details": patch_details,
                "probe_evidence": evidence_leg,
                "verdict": "PASS" if patches_leg else "FAIL",
            }
            if not patches_leg:
                results["hdr_legacy_ab"]["reason"] = patch_err
        else:
            results["hdr_legacy_ab"] = {
                "file_exists": True,
                "size_bytes": len(data_leg),
                "image_valid": False,
                "probe_evidence": evidence_leg,
                "reason": leg_err,
                "verdict": "FAIL",
            }
        logger.info("Screenshot hdr (legacy A/B): size=%d, dims=%s -> %s",
                    len(data_leg), results["hdr_legacy_ab"].get("dimensions"), results["hdr_legacy_ab"]["verdict"])
    else:
        results["hdr_legacy_ab"] = {"file_exists": False, "probe_evidence": evidence_leg, "verdict": "FAIL"}

    # 3. Subtitle-inclusive screenshot on HDR
    sub_path = temp_dir / "shot_sub.ass"
    sub_path.write_text("""[Script Info]
Title: CineHDR Screenshot Subtitle Test
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,10,10,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:10.00,Default,,0,0,0,,■■■
""", encoding="utf-8")
    shot_sub = temp_dir / "screenshot_hdr_subtitles.png"
    if shot_sub.exists():
        shot_sub.unlink()
    probe_res_sub = run_probe_raw(
        binary, hdr_fixture, "hdr", "opengl-next", "no", "paused",
        {
            "CINEHDR_PROBE_SUB_FILE": str(sub_path),
            "CINEHDR_PROBE_SCREENSHOT_PATH": str(shot_sub),
            "CINEHDR_PROBE_SCREENSHOT_MODE": "subtitles",
        }
    )
    ev_ok, ev_err, evidence_sub = _validate_probe_evidence(probe_res_sub, "opengl-next", software_only, expect_subtitles=True)
    if not ev_ok:
        results["hdr_with_subtitles"] = {"probe_evidence": evidence_sub, "reason": ev_err, "verdict": "FAIL"}
    elif shot_sub.is_file():
        data_sub = shot_sub.read_bytes()
        valid_sub, sub_err, img_sub = decode_and_validate_png(data_sub)
        if valid_sub:
            shot_hdr_next = temp_dir / "screenshot_hdr_next.png"
            if shot_hdr_next.is_file():
                valid_base, _, img_base = decode_and_validate_png(shot_hdr_next.read_bytes())
                if valid_base:
                    sub_diff_valid, sub_diff_err, diff_details = validate_subtitle_overlay(img_base, img_sub)
                else:
                    sub_diff_valid, sub_diff_err, diff_details = False, "base_image_invalid", {}
            else:
                sub_diff_valid, sub_diff_err, diff_details = False, "base_image_missing", {}

            results["hdr_with_subtitles"] = {
                "file_exists": True,
                "size_bytes": len(data_sub),
                "dimensions": img_sub.size,
                "image_valid": True,
                "subtitles_overlay_valid": sub_diff_valid,
                "subtitle_diff_details": diff_details,
                "probe_evidence": evidence_sub,
                "verdict": "PASS" if sub_diff_valid else "FAIL",
            }
            if not sub_diff_valid:
                results["hdr_with_subtitles"]["reason"] = sub_diff_err
        else:
            results["hdr_with_subtitles"] = {
                "file_exists": True,
                "size_bytes": len(data_sub),
                "image_valid": False,
                "probe_evidence": evidence_sub,
                "reason": sub_err,
                "verdict": "FAIL",
            }
        logger.info("Screenshot hdr (with subtitles): size=%d, dims=%s, overlay_valid=%s -> %s",
                    len(data_sub), results["hdr_with_subtitles"].get("dimensions"),
                    results["hdr_with_subtitles"].get("subtitles_overlay_valid"),
                    results["hdr_with_subtitles"]["verdict"])
    else:
        results["hdr_with_subtitles"] = {"file_exists": False, "probe_evidence": evidence_sub, "verdict": "FAIL"}

    # 4. Hardware Decode Screenshot (auto / vaapi-copy)
    if software_only:
        results["hdr_hwdec_auto"] = {
            "verdict": "NOT_RUN",
            "reason": "software_only_mode_hwdec_skipped",
        }
        logger.info("Screenshot hdr (hwdec auto): skipped in software-only mode -> NOT_RUN")
    else:
        shot_hw = temp_dir / "screenshot_hdr_hwdec.png"
        if shot_hw.exists():
            shot_hw.unlink()
        probe_res_hw = run_probe_raw(
            binary, hdr_fixture, "hdr", "opengl-next", "auto", "paused",
            {"CINEHDR_PROBE_SCREENSHOT_PATH": str(shot_hw)}
        )
        ev_ok, ev_err, evidence_hw = _validate_probe_evidence(
            probe_res_hw, "opengl-next", software_only=False, require_hardware_decoder=True
        )
        if not ev_ok:
            results["hdr_hwdec_auto"] = {"probe_evidence": evidence_hw, "reason": ev_err, "verdict": "FAIL"}
        elif shot_hw.is_file():
            data_hw = shot_hw.read_bytes()
            valid_hw, hw_err, img_hw = decode_and_validate_png(data_hw)
            if valid_hw:
                patches_hw, patch_err, patch_details = validate_pattern_patches(img_hw)
                results["hdr_hwdec_auto"] = {
                    "file_exists": True,
                    "size_bytes": len(data_hw),
                    "dimensions": img_hw.size,
                    "image_valid": True,
                    "patches_valid": patches_hw,
                    "patch_details": patch_details,
                    "probe_evidence": evidence_hw,
                    "verdict": "PASS" if patches_hw else "FAIL",
                }
                if not patches_hw:
                    results["hdr_hwdec_auto"]["reason"] = patch_err
            else:
                results["hdr_hwdec_auto"] = {
                    "file_exists": True,
                    "size_bytes": len(data_hw),
                    "image_valid": False,
                    "probe_evidence": evidence_hw,
                    "reason": hw_err,
                    "verdict": "FAIL",
                }
            logger.info("Screenshot hdr (hwdec auto): size=%d, dims=%s -> %s",
                        len(data_hw), results["hdr_hwdec_auto"].get("dimensions"), results["hdr_hwdec_auto"]["verdict"])
        else:
            results["hdr_hwdec_auto"] = {"file_exists": False, "probe_evidence": evidence_hw, "verdict": "FAIL"}

    failed = [k for k, r in results.items() if r.get("verdict") == "FAIL"]
    passed = [k for k, r in results.items() if r.get("verdict") == "PASS"]
    overall = "PASS" if (len(failed) == 0 and len(passed) > 0) else "FAIL"
    return {"results": results, "verdict": overall}


def test_w5_embedded_subtitles(binary: Path, cache_dir: Path, temp_dir: Path) -> dict[str, Any]:
    logger.info("=== Executing W5: Embedded Subtitles Diffuse White Test ===")
    hdr_fix = generate_patterns.ensure_fixture("hdr", cache_dir)
    hdr_fixture = Path(hdr_fix["path"])

    # Create ASS subtitle file with text at bottom center
    sub_path = temp_dir / "test_sub.ass"
    sub_content = """[Script Info]
Title: CineHDR Subtitle Calibration
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,10,10,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:10.00,Default,,0,0,0,,■■■
"""
    sub_path.write_text(sub_content, encoding="utf-8")

    probe_res = run_probe_raw(
        binary, hdr_fixture, "hdr", "opengl-next", "no", "paused",
        {"CINEHDR_PROBE_SUB_FILE": str(sub_path)}
    )

    readback = probe_res.get("readback", {})
    sub_sample = readback.get("subtitle_sample")
    if not sub_sample or len(sub_sample) != 3:
        logger.error("Subtitle sample was not reported by probe!")
        return {"verdict": "FAIL", "reason": "no_subtitle_sample"}

    r, g, b = sub_sample
    # White subtitle text in PQ target should be diffuse reference white (~203 nits -> PQ ~0.5807)
    # Tolerance window: [0.50, 0.65] (must NOT be peak white 1.0 / 10000 nits)
    valid_luminance = (0.50 <= r <= 0.65) and (0.50 <= g <= 0.65) and (0.50 <= b <= 0.65)
    neutral_color = abs(r - g) < 0.02 and abs(g - b) < 0.02

    passed = valid_luminance and neutral_color
    verdict = "PASS" if passed else "FAIL"
    logger.info("Subtitle sample: (%.4f, %.4f, %.4f), diffuse_window=%s, neutral=%s -> %s",
                r, g, b, valid_luminance, neutral_color, verdict)

    return {
        "subtitle_sample": {"r": r, "g": g, "b": b},
        "diffuse_white_target_range": [0.50, 0.65],
        "neutral_color": neutral_color,
        "verdict": verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run extended Gate 2 matrix.")
    parser.add_argument("--mpv-source", type=Path, default=DEFAULT_MPV_SOURCE)
    parser.add_argument("--mpv-prefix", type=Path, default=DEFAULT_MPV_PREFIX)
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "cinehdr-gate2-fixtures")
    parser.add_argument("--only-w4", action="store_true", help="Execute only W4 screenshot-to-file matrix.")
    parser.add_argument("--software-only", action="store_true", help="Force software-only decoding and execution (skip hwdec).")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory to preserve artifacts and screenshots.")
    args = parser.parse_args()

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        ctx = contextlib.nullcontext(str(args.output_dir))
    else:
        ctx = tempfile.TemporaryDirectory(prefix="cinehdr-gate2-ext-")

    with ctx as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        binary = compile_probe_binary(temp_dir, args.mpv_source, args.mpv_prefix)

        if args.only_w4:
            w4_report = test_w4_screenshot_matrix(binary, args.cache_dir, temp_dir, software_only=args.software_only)
            report = {
                "schema": "cinehdr.gate2.extended.report.v1",
                "software_only": args.software_only,
                "w4_screenshots": w4_report,
                "overall_verdict": w4_report["verdict"],
            }
            if args.output_dir:
                (args.output_dir / "gate2_extended_w4_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))
            return 0 if w4_report["verdict"] == "PASS" else 1

        w3_report = test_w3_dithering_matrix(binary, args.cache_dir)
        w4_report = test_w4_screenshot_matrix(binary, args.cache_dir, temp_dir, software_only=args.software_only)
        w5_report = test_w5_embedded_subtitles(binary, args.cache_dir, temp_dir)

        overall_verdict = "PASS" if (
            w3_report["verdict"] == "PASS"
            and w4_report["verdict"] == "PASS"
            and w5_report["verdict"] == "PASS"
        ) else "FAIL"

        report = {
            "schema": "cinehdr.gate2.extended.report.v1",
            "software_only": args.software_only,
            "w3_dithering": w3_report,
            "w4_screenshots": w4_report,
            "w5_subtitles": w5_report,
            "overall_verdict": overall_verdict,
        }
        print(json.dumps(report, indent=2))
        return 0 if overall_verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
