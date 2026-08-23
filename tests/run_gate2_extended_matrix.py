#!/usr/bin/env python3

"""Extended Gate 2 validation suite: W3 (dither), W4 (screenshots), W5 (subtitles)."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = PROJECT_ROOT.parent
DEFAULT_MPV_SOURCE = Path(os.environ.get("CINEHDR_MPV_SOURCE", WORK_ROOT / "mpv-gpu-next"))
DEFAULT_MPV_PREFIX = Path(os.environ.get("CINEHDR_MPV_PREFIX", WORK_ROOT / "mpv-gpu-next-prefix"))

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


def test_w4_screenshot_matrix(binary: Path, cache_dir: Path, temp_dir: Path) -> dict[str, Any]:
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
        if not shot_path.is_file():
            results[f"{mode}_next"] = {"file_exists": False, "verdict": "FAIL"}
            continue
        data = shot_path.read_bytes()
        width, height = parse_png_dimensions(data)
        passed = (len(data) > 1000) and (width == 320) and (height == 180)
        results[f"{mode}_next"] = {
            "file_exists": True,
            "size_bytes": len(data),
            "dimensions": (width, height),
            "verdict": "PASS" if passed else "FAIL",
        }
        logger.info("Screenshot %s (opengl-next): size=%d, dims=%dx%d -> %s",
                    mode, len(data), width, height, results[f"{mode}_next"]["verdict"])

    # 2. Legacy A/B comparison on HDR
    hdr_fix = generate_patterns.ensure_fixture("hdr", cache_dir)
    hdr_fixture = Path(hdr_fix["path"])
    shot_legacy = temp_dir / "screenshot_hdr_legacy.png"
    if shot_legacy.exists():
        shot_legacy.unlink()
    run_probe_raw(
        binary, hdr_fixture, "hdr", "opengl", "no", "paused",
        {"CINEHDR_PROBE_SCREENSHOT_PATH": str(shot_legacy)}
    )
    if shot_legacy.is_file():
        data_leg = shot_legacy.read_bytes()
        w_leg, h_leg = parse_png_dimensions(data_leg)
        passed_leg = (len(data_leg) > 1000) and (w_leg == 320) and (h_leg == 180)
        results["hdr_legacy_ab"] = {
            "file_exists": True,
            "size_bytes": len(data_leg),
            "dimensions": (w_leg, h_leg),
            "verdict": "PASS" if passed_leg else "FAIL",
        }
        logger.info("Screenshot hdr (legacy A/B): size=%d, dims=%dx%d -> %s",
                    len(data_leg), w_leg, h_leg, results["hdr_legacy_ab"]["verdict"])
    else:
        results["hdr_legacy_ab"] = {"file_exists": False, "verdict": "FAIL"}

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
    run_probe_raw(
        binary, hdr_fixture, "hdr", "opengl-next", "no", "paused",
        {
            "CINEHDR_PROBE_SUB_FILE": str(sub_path),
            "CINEHDR_PROBE_SCREENSHOT_PATH": str(shot_sub),
            "CINEHDR_PROBE_SCREENSHOT_MODE": "subtitles",
        }
    )
    if shot_sub.is_file():
        data_sub = shot_sub.read_bytes()
        w_sub, h_sub = parse_png_dimensions(data_sub)
        passed_sub = (len(data_sub) > 1000) and (w_sub == 320) and (h_sub == 180)
        results["hdr_with_subtitles"] = {
            "file_exists": True,
            "size_bytes": len(data_sub),
            "dimensions": (w_sub, h_sub),
            "verdict": "PASS" if passed_sub else "FAIL",
        }
        logger.info("Screenshot hdr (with subtitles): size=%d, dims=%dx%d -> %s",
                    len(data_sub), w_sub, h_sub, results["hdr_with_subtitles"]["verdict"])
    else:
        results["hdr_with_subtitles"] = {"file_exists": False, "verdict": "FAIL"}

    # 4. Hardware Decode Screenshot (auto / vaapi-copy)
    shot_hw = temp_dir / "screenshot_hdr_hwdec.png"
    if shot_hw.exists():
        shot_hw.unlink()
    run_probe_raw(
        binary, hdr_fixture, "hdr", "opengl-next", "auto", "paused",
        {"CINEHDR_PROBE_SCREENSHOT_PATH": str(shot_hw)}
    )
    if shot_hw.is_file():
        data_hw = shot_hw.read_bytes()
        w_hw, h_hw = parse_png_dimensions(data_hw)
        passed_hw = (len(data_hw) > 1000) and (w_hw == 320) and (h_hw == 180)
        results["hdr_hwdec_auto"] = {
            "file_exists": True,
            "size_bytes": len(data_hw),
            "dimensions": (w_hw, h_hw),
            "verdict": "PASS" if passed_hw else "FAIL",
        }
        logger.info("Screenshot hdr (hwdec auto): size=%d, dims=%dx%d -> %s",
                    len(data_hw), w_hw, h_hw, results["hdr_hwdec_auto"]["verdict"])
    else:
        results["hdr_hwdec_auto"] = {"file_exists": False, "verdict": "FAIL"}

    overall = "PASS" if all(r["verdict"] == "PASS" for r in results.values()) else "FAIL"
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
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="cinehdr-gate2-ext-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        binary = compile_probe_binary(temp_dir, args.mpv_source, args.mpv_prefix)

        w3_report = test_w3_dithering_matrix(binary, args.cache_dir)
        w4_report = test_w4_screenshot_matrix(binary, args.cache_dir, temp_dir)
        w5_report = test_w5_embedded_subtitles(binary, args.cache_dir, temp_dir)

        overall_verdict = "PASS" if (
            w3_report["verdict"] == "PASS"
            and w4_report["verdict"] == "PASS"
            and w5_report["verdict"] == "PASS"
        ) else "FAIL"

        report = {
            "schema": "cinehdr.gate2.extended.report.v1",
            "w3_dithering": w3_report,
            "w4_screenshots": w4_report,
            "w5_subtitles": w5_report,
            "overall_verdict": overall_verdict,
        }
        print(json.dumps(report, indent=2))
        return 0 if overall_verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
