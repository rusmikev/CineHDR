#!/usr/bin/env python3

"""A/B Performance Benchmark Suite: comparing opengl vs opengl-next under EGL render loop."""

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

sys.path.insert(0, str(PROJECT_ROOT / "tests"))
import generate_patterns
import run_pixel_pipeline

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("performance_ab")


def run_benchmark_for_api(binary: Path, fixture_path: Path, api: str, frames: int = 120) -> dict[str, Any]:
    cmd = [
        str(binary),
        str(fixture_path),
        "hdr",
        api,
        "0.041667",
        "no",
        "continuous-identical",
    ]
    env = os.environ.copy()
    env["CINEHDR_PROBE_BENCHMARK_FRAMES"] = str(frames)
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise RuntimeError(f"benchmark probe failed for {api} ({res.returncode}):\n{res.stderr}\n{res.stdout}")
    data = json.loads(res.stdout)
    perf = data.get("performance")
    if not perf:
        raise RuntimeError(f"No performance telemetry returned for {api}:\n{res.stdout}")
    return perf


def main() -> int:
    parser = argparse.ArgumentParser(description="Run A/B performance benchmark.")
    parser.add_argument("--mpv-source", type=Path, default=DEFAULT_MPV_SOURCE)
    parser.add_argument("--mpv-prefix", type=Path, default=DEFAULT_MPV_PREFIX)
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "cinehdr-gate2-fixtures")
    parser.add_argument("--frames", type=int, default=120)
    args = parser.parse_args()

    fix = generate_patterns.ensure_fixture("hdr", args.cache_dir)
    fixture_path = Path(fix["path"])

    with tempfile.TemporaryDirectory(prefix="cinehdr-perf-ab-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        binary = temp_dir / "pixel_validator_egl"
        run_pixel_pipeline.compile_probe(binary, args.mpv_source, args.mpv_prefix, os.environ.get("CC", "cc"))

        logger.info("=== Running Legacy (opengl) Benchmark (%d frames) ===", args.frames)
        legacy_perf = run_benchmark_for_api(binary, fixture_path, "opengl", args.frames)
        logger.info("Legacy: mean=%.2f ms, p50=%.2f ms, p95=%.2f ms, p99=%.2f ms, drops=%d",
                    legacy_perf["mean_ms"], legacy_perf["p50_ms"], legacy_perf["p95_ms"], legacy_perf["p99_ms"], legacy_perf["dropped_frames"])

        logger.info("=== Running GPU Next (opengl-next) Benchmark (%d frames) ===", args.frames)
        gpu_next_perf = run_benchmark_for_api(binary, fixture_path, "opengl-next", args.frames)
        logger.info("GPU Next: mean=%.2f ms, p50=%.2f ms, p95=%.2f ms, p99=%.2f ms, drops=%d",
                    gpu_next_perf["mean_ms"], gpu_next_perf["p50_ms"], gpu_next_perf["p95_ms"], gpu_next_perf["p99_ms"], gpu_next_perf["dropped_frames"])

        # Strict ADR-0001 Gate 2 Criteria:
        # 1. No more output drops than max(3 frames, 0.1% of presented frames) above patched legacy
        allowed_drop_delta = max(3, int(args.frames * 0.001))
        no_excess_drops = gpu_next_perf["dropped_frames"] <= (legacy_perf["dropped_frames"] + allowed_drop_delta)
        # 2. 95th percentile render time is no more than 10% worse than patched legacy (or faster)
        p95_within_budget = gpu_next_perf["p95_ms"] <= (legacy_perf["p95_ms"] * 1.10)

        passed = no_excess_drops and p95_within_budget
        verdict = "PASS" if passed else "FAIL"

        report = {
            "schema": "cinehdr.performance.ab.report.v1",
            "frames_tested": args.frames,
            "legacy": legacy_perf,
            "gpu_next": gpu_next_perf,
            "allowed_drop_delta": allowed_drop_delta,
            "no_excess_drops": no_excess_drops,
            "p95_within_budget": p95_within_budget,
            "p95_target_max_ms": round(legacy_perf["p95_ms"] * 1.10, 4),
            "verdict": verdict,
        }
        print(json.dumps(report, indent=2))
        return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
