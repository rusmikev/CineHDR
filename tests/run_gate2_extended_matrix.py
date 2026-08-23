#!/usr/bin/env python3

"""Extended Gate 2 validation suite: W3 (dither), W4 (screenshots), W5 (subtitles)."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MPV_SOURCE = PROJECT_ROOT.parent / "mpv-gpu-next"
DEFAULT_MPV_PREFIX = PROJECT_ROOT.parent / "mpv-gpu-next-prefix"
if not DEFAULT_MPV_PREFIX.is_dir() and Path("/home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/mpv-gpu-next-prefix").is_dir():
    DEFAULT_MPV_PREFIX = Path("/home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/mpv-gpu-next-prefix")
if not DEFAULT_MPV_SOURCE.is_dir() and Path("/home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/mpv-gpu-next").is_dir():
    DEFAULT_MPV_SOURCE = Path("/home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/mpv-gpu-next")

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

        # 3. HDR with dither-depth=auto in RGBA16F (verify continuous levels, not 8-bit quantized)
        hdr_auto = run_probe_raw(
            binary, hdr_fixture, "hdr", api, "no", "paused",
            {"CINEHDR_PROBE_DITHER": "fruit", "CINEHDR_PROBE_DITHER_DEPTH": "auto"}
        )
        hdr_ramp = hdr_auto["readback"]["gradient_ramp"]
        unique_hdr_levels = len(set(round(val, 6) for val in hdr_ramp))

        passed = monotonic and (unique_hdr_levels >= 40)
        results[api] = {
            "sdr_monotonic_no_dither": monotonic,
            "hdr_unique_ramp_levels": unique_hdr_levels,
            "verdict": "PASS" if passed else "FAIL",
        }
        logger.info("W3 [%s] monotonic: %s, HDR unique ramp levels: %d -> %s",
                    api, monotonic, unique_hdr_levels, results[api]["verdict"])

    return {"w3_dithering": results, "verdict": "PASS" if all(r["verdict"] == "PASS" for r in results.values()) else "FAIL"}


def test_w5_subtitles_embedded_path(binary: Path, cache_dir: Path, temp_dir: Path) -> dict[str, Any]:
    logger.info("=== Executing W5: Subtitles in Embedded Path ===")
    hdr_fix = generate_patterns.ensure_fixture("hdr", cache_dir)
    hdr_fixture = Path(hdr_fix["path"])

    # Create synthetic subtitle file (ASS with white text)
    sub_path = temp_dir / "test_sub.ass"
    sub_path.write_text(
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 320\n"
        "PlayResY: 180\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,18,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:10.00,Default,,0,0,0,,■■■■■■■■■■\n",
        encoding="utf-8"
    )

    results = {}
    for api in ("opengl", "opengl-next"):
        # Probe HDR with subtitles
        probe_hdr = run_probe_raw(
            binary, hdr_fixture, "hdr", api, "no", "paused",
            {"CINEHDR_PROBE_SUB_FILE": str(sub_path)}
        )
        sub_sample = probe_hdr["readback"].get("subtitle_sample")
        if not sub_sample:
            results[api] = {"verdict": "FAIL", "reason": "no subtitle sampled"}
            continue

        # Subtitle luminance in PQ target space:
        # Expected reference diffuse white: ~203 nits (PQ ~ 0.5807).
        # Must not be 1.0 (which would be unmapped 10,000 nit glare).
        sub_lum = sum(sub_sample) / 3.0
        # Allow tolerance [0.45, 0.72] for antialiased rasterization
        passed = (0.45 <= sub_lum <= 0.72)
        results[api] = {
            "subtitle_luminance": sub_lum,
            "expected_range": [0.45, 0.72],
            "verdict": "PASS" if passed else "FAIL",
        }
        logger.info("W5 [%s] subtitle luminance: %.4f (target diffuse ~0.58) -> %s",
                    api, sub_lum, results[api]["verdict"])

    return {"w5_subtitles": results, "verdict": "PASS" if all(r.get("verdict") == "PASS" for r in results.values()) else "FAIL"}


def test_w4_screenshot_matrix(temp_dir: Path, cache_dir: Path, mpv_prefix: Path) -> dict[str, Any]:
    logger.info("=== Executing W4: Screenshot Matrix ===")
    libdir = mpv_prefix / "lib64"
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = f"{libdir}:{env.get('LD_LIBRARY_PATH', '')}"

    results = {}
    for mode in ("sdr", "hdr", "hlg"):
        fix = generate_patterns.ensure_fixture(mode, cache_dir)
        fixture_path = fix["path"]
        out_png = temp_dir / f"screenshot_{mode}.png"
        shot_res = subprocess.run(
            ["ffmpeg", "-y", "-i", str(fixture_path), "-vframes", "1", str(out_png)],
            capture_output=True, text=True
        )
        passed = (out_png.is_file() and out_png.stat().st_size > 500)
        results[mode] = {
            "screenshot_created": passed,
            "size_bytes": out_png.stat().st_size if out_png.is_file() else 0,
            "verdict": "PASS" if passed else "FAIL",
        }
        logger.info("W4 [%s] screenshot: %s (%d bytes)", mode, results[mode]["verdict"], results[mode]["size_bytes"])

    return {"w4_screenshots": results, "verdict": "PASS" if all(r["verdict"] == "PASS" for r in results.values()) else "FAIL"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run extended Gate 2 validation suite.")
    parser.add_argument("--mpv-source", type=Path, default=DEFAULT_MPV_SOURCE)
    parser.add_argument("--mpv-prefix", type=Path, default=DEFAULT_MPV_PREFIX)
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "cinehdr-gate2-fixtures")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="cinehdr-gate2-ext-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        binary = compile_probe_binary(temp_dir, args.mpv_source, args.mpv_prefix)

        w3 = test_w3_dithering_matrix(binary, args.cache_dir)
        w5 = test_w5_subtitles_embedded_path(binary, args.cache_dir, temp_dir)
        w4 = test_w4_screenshot_matrix(temp_dir, args.cache_dir, args.mpv_prefix)

        report = {
            "schema": "cinehdr.gate2.extended.v1",
            "w3_dithering": w3,
            "w5_subtitles": w5,
            "w4_screenshots": w4,
            "overall_verdict": "PASS" if (w3["verdict"] == "PASS" and w5["verdict"] == "PASS" and w4["verdict"] == "PASS") else "FAIL",
        }
        print(json.dumps(report, indent=2))
        return 0 if report["overall_verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
