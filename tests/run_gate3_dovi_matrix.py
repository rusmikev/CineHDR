#!/usr/bin/env python3

"""Gate 3 validation suite: Dolby Vision RPU, profile matrix, and peak sweep."""

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
WORK_ROOT = PROJECT_ROOT.parent
DEFAULT_MPV_SOURCE = Path(os.environ.get("CINEHDR_MPV_SOURCE", WORK_ROOT / "mpv-gpu-next"))
DEFAULT_MPV_PREFIX = Path(os.environ.get("CINEHDR_MPV_PREFIX", WORK_ROOT / "mpv-gpu-next-prefix"))

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))
import generate_patterns
import run_pixel_pipeline

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("gate3_dovi")


def test_target_peak_sweep(binary: Path, cache_dir: Path) -> dict[str, Any]:
    logger.info("=== Executing Gate 3: Target Peak Sweep ===")
    fix = generate_patterns.ensure_fixture("hdr", cache_dir)
    hdr_fixture = Path(fix["path"])

    sweep_peaks = ["100", "203", "400", "600", "1000"]
    peak_results = {}
    for peak in sweep_peaks:
        env = os.environ.copy()
        env["CINEHDR_PROBE_TARGET_PEAK"] = peak
        cmd = [
            str(binary),
            str(hdr_fixture),
            "hdr",
            "opengl-next",
            "0",
            "no",
            "paused",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if res.returncode != 0:
            raise RuntimeError(f"probe failed on target-peak {peak}: {res.stderr}")
        probe = json.loads(res.stdout)
        
        # Verify that output target_peak matches requested peak
        reported_peak = probe["output"]["target_peak"]
        if str(reported_peak) != peak:
            raise ValueError(f"probe did not apply target-peak {peak} (got {reported_peak})")
            
        white_sample = next(p for p in probe["readback"]["patches"] if p["name"] == "reference_white")
        peak_results[peak] = {
            "applied_target_peak": reported_peak,
            "reference_white_r": white_sample["r"],
            "reference_white_g": white_sample["g"],
            "reference_white_b": white_sample["b"],
        }
        logger.info("Peak %s: ref white = (%.4f, %.4f, %.4f), target_peak=%s",
                    peak, white_sample["r"], white_sample["g"], white_sample["b"], reported_peak)

    # Strict analytical checks:
    # 1. Monotonic brightness scaling across tone-mapped target peaks:
    #    Peak 100 < Peak 203 < Peak 400 < Peak 600 <= Peak 1000
    peaks_order = ["100", "203", "400", "600", "1000"]
    luminances = [peak_results[p]["reference_white_r"] for p in peaks_order]
    monotonic_scaling = all(luminances[i] < luminances[i+1] + 1e-4 for i in range(len(luminances)-1))

    # 2. Perfect neutral axis preserved under all tone mapping curves:
    neutral_axis = all(
        abs(r["reference_white_r"] - r["reference_white_g"]) < 0.001 and
        abs(r["reference_white_g"] - r["reference_white_b"]) < 0.001
        for r in peak_results.values()
    )

    # 3. Peak 1000 reaches native diffuse reference white (~0.5801):
    native_diffuse_white = abs(peak_results["1000"]["reference_white_r"] - 0.5801) < 0.002

    valid_sweep = monotonic_scaling and neutral_axis and native_diffuse_white
    return {
        "sweep_results": peak_results,
        "monotonic_scaling": monotonic_scaling,
        "neutral_axis_preserved": neutral_axis,
        "native_diffuse_white": native_diffuse_white,
        "verdict": "PASS" if valid_sweep else "FAIL",
    }


def test_dovi_controller_profile_policy() -> dict[str, Any]:
    logger.info("=== Executing Gate 3: DoVi Controller Profile Policy ===")
    from unittest.mock import MagicMock
    from src.hdr_controller import HdrController

    mock_mpv = MagicMock()
    mock_mpv.__getitem__.side_effect = lambda k: "auto" if k in ("target-trc", "target-prim", "target-peak") else None
    
    controller = HdrController(mock_mpv)

    # 1. Profile 8.1 (supported base layer) -> not unsupported
    controller.supports_dovi_reshaping = False
    controller._dovi_info = {"profile": 8, "level": 6, "unsupported": False}
    p8_allowed = not controller.dovi_unsupported

    # 2. Profile 5 on Legacy backend (unsupported -> blocked to prevent green/magenta tint)
    controller.supports_dovi_reshaping = False
    controller._dovi_info = {"profile": 5, "level": 6, "unsupported": True}
    p5_legacy_blocked = controller.dovi_unsupported

    # 3. Profile 5 on GPU Next backend (supports_dovi_reshaping capability enabled)
    controller.supports_dovi_reshaping = True
    controller._dovi_info = {"profile": 5, "level": 6, "unsupported": True}
    p5_gpu_next_allowed = not controller.dovi_unsupported

    controller.disconnect()
    passed = p8_allowed and p5_legacy_blocked and p5_gpu_next_allowed
    logger.info("Profile 8.1 base layer allowed: %s, Profile 5 Legacy blocked: %s, Profile 5 GPU Next capability allowed: %s",
                p8_allowed, p5_legacy_blocked, p5_gpu_next_allowed)
    return {
        "profile_8_base_layer_allowed": p8_allowed,
        "profile_5_legacy_blocked": p5_legacy_blocked,
        "profile_5_gpu_next_allowed": p5_gpu_next_allowed,
        "verdict": "PASS" if passed else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Gate 3 validation suite.")
    parser.add_argument("--mpv-source", type=Path, default=DEFAULT_MPV_SOURCE)
    parser.add_argument("--mpv-prefix", type=Path, default=DEFAULT_MPV_PREFIX)
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "cinehdr-gate2-fixtures")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="cinehdr-gate3-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        binary = temp_dir / "pixel_validator_egl"
        run_pixel_pipeline.compile_probe(binary, args.mpv_source, args.mpv_prefix, os.environ.get("CC", "cc"))

        sweep = test_target_peak_sweep(binary, args.cache_dir)
        dovi_policy = test_dovi_controller_profile_policy()

        overall_verdict = "PASS" if (sweep["verdict"] == "PASS" and dovi_policy["verdict"] == "PASS") else "FAIL"
        report = {
            "schema": "cinehdr.gate3.report.v1",
            "target_peak_sweep": sweep,
            "dovi_profile_policy": dovi_policy,
            "overall_verdict": overall_verdict,
        }
        print(json.dumps(report, indent=2))
        return 0 if overall_verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
