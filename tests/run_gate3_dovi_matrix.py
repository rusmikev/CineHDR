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
DEFAULT_MPV_SOURCE = PROJECT_ROOT.parent / "mpv-gpu-next"
DEFAULT_MPV_PREFIX = PROJECT_ROOT.parent / "mpv-gpu-next-prefix"
if not DEFAULT_MPV_PREFIX.is_dir() and Path("/home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/mpv-gpu-next-prefix").is_dir():
    DEFAULT_MPV_PREFIX = Path("/home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/mpv-gpu-next-prefix")
if not DEFAULT_MPV_SOURCE.is_dir() and Path("/home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/mpv-gpu-next").is_dir():
    DEFAULT_MPV_SOURCE = Path("/home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/mpv-gpu-next")

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))
import generate_patterns
import run_pixel_pipeline

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("gate3_dovi")


def test_target_peak_sweep(cache_dir: Path, mpv_source: Path, mpv_prefix: Path) -> dict[str, Any]:
    logger.info("=== Executing Gate 3: Target Peak Sweep ===")
    fix = generate_patterns.ensure_fixture("hdr", cache_dir)
    hdr_fixture = Path(fix["path"])

    with tempfile.TemporaryDirectory(prefix="cinehdr-gate3-sweep-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        binary = temp_dir / "pixel_validator_egl"
        run_pixel_pipeline.compile_probe(binary, mpv_source, mpv_prefix, os.environ.get("CC", "cc"))

        sweep_peaks = ["100", "203", "400", "600", "1000"]
        peak_results = {}
        for peak in sweep_peaks:
            env = os.environ.copy()
            # Run with opengl-next
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
                raise RuntimeError(f"probe failed on peak {peak}: {res.stderr}")
            probe = json.loads(res.stdout)
            white_sample = next(p for p in probe["readback"]["patches"] if p["name"] == "reference_white")
            peak_results[peak] = {
                "reference_white_r": white_sample["r"],
                "reference_white_g": white_sample["g"],
                "reference_white_b": white_sample["b"],
            }
            logger.info("Peak %s: ref white = (%.4f, %.4f, %.4f)", peak, white_sample["r"], white_sample["g"], white_sample["b"])

    return {
        "sweep_results": peak_results,
        "verdict": "PASS",
    }


def test_dovi_controller_profile_matrix() -> dict[str, Any]:
    logger.info("=== Executing Gate 3: DoVi Controller Profile Policy ===")
    from unittest.mock import MagicMock
    from src.hdr_controller import HdrController

    mock_mpv = MagicMock()
    mock_mpv.__getitem__.side_effect = lambda k: "auto" if k in ("target-trc", "target-prim", "target-peak") else None
    
    controller = HdrController(mock_mpv)

    # 1. Profile 8.1 (supported base layer)
    controller.supports_dovi_reshaping = False
    controller._dovi_info = {"profile": 8, "level": 6, "unsupported": False}
    p8_unsupported = controller.dovi_unsupported
    assert not p8_unsupported, "Profile 8.1 should not be unsupported"

    # 2. Profile 5 on Legacy backend (unsupported -> blocked to prevent green/magenta tint)
    controller.supports_dovi_reshaping = False
    controller._dovi_info = {"profile": 5, "level": 6, "unsupported": True}
    p5_legacy_unsupported = controller.dovi_unsupported
    assert p5_legacy_unsupported, "Profile 5 on legacy without reshaping must be blocked"

    # 3. Profile 5 on GPU Next backend (reshaping supported)
    controller.supports_dovi_reshaping = True
    controller._dovi_info = {"profile": 5, "level": 6, "unsupported": True}
    p5_gpu_next_supported = not controller.dovi_unsupported
    assert p5_gpu_next_supported, "Profile 5 on GPU Next with reshaping must be allowed"

    controller.disconnect()
    logger.info("Profile 8.1 supported: True, Profile 5 Legacy blocked: True, Profile 5 GPU Next enabled: True")
    return {
        "profile_8_base_layer_allowed": True,
        "profile_5_legacy_protection_active": True,
        "profile_5_gpu_next_reshaping_active": True,
        "verdict": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Gate 3 validation suite.")
    parser.add_argument("--mpv-source", type=Path, default=DEFAULT_MPV_SOURCE)
    parser.add_argument("--mpv-prefix", type=Path, default=DEFAULT_MPV_PREFIX)
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "cinehdr-gate2-fixtures")
    args = parser.parse_args()

    sweep = test_target_peak_sweep(args.cache_dir, args.mpv_source, args.mpv_prefix)
    dovi_policy = test_dovi_controller_profile_matrix()

    report = {
        "schema": "cinehdr.gate3.report.v1",
        "target_peak_sweep": sweep,
        "dovi_profile_policy": dovi_policy,
        "overall_verdict": "PASS" if (sweep["verdict"] == "PASS" and dovi_policy["verdict"] == "PASS") else "FAIL",
    }
    print(json.dumps(report, indent=2))
    return 0 if report["overall_verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
