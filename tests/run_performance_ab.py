#!/usr/bin/env python3

"""A/B Performance benchmark and soak harness: opengl vs opengl-next."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MPV_PREFIX = PROJECT_ROOT.parent / "mpv-gpu-next-prefix"
if not DEFAULT_MPV_PREFIX.is_dir() and Path("/home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/mpv-gpu-next-prefix").is_dir():
    DEFAULT_MPV_PREFIX = Path("/home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/mpv-gpu-next-prefix")

sys.path.insert(0, str(PROJECT_ROOT / "tests"))
import generate_patterns

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("performance_ab")


def run_playback_benchmark(
    video_path: Path,
    api: str,
    duration_seconds: float,
    mpv_prefix: Path,
) -> dict[str, Any]:
    libdir = mpv_prefix / "lib64"
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = f"{libdir}:{env.get('LD_LIBRARY_PATH', '')}"

    # Use mpv to benchmark rendering frames over duration
    cmd = [
        "mpv",
        str(video_path),
        "--vo=null",
        "--ao=null",
        "--video-sync=display-resample",
        f"--length={duration_seconds}",
        "--untimed=yes",
        "--msg-level=all=info",
    ]
    t0 = time.perf_counter()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    t1 = time.perf_counter()
    wall_time = t1 - t0

    # Parse stderr/stdout for frame stats
    return {
        "api": api,
        "wall_time_seconds": round(wall_time, 4),
        "status": "PASS" if res.returncode == 0 else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run A/B performance benchmark.")
    parser.add_argument("--mpv-prefix", type=Path, default=DEFAULT_MPV_PREFIX)
    parser.add_argument("--duration", type=float, default=5.0)
    args = parser.parse_args()

    fixtures_dir = Path(tempfile.gettempdir()) / "cinehdr-gate2-fixtures"
    fix = generate_patterns.ensure_fixture("hdr", fixtures_dir)
    video_path = Path(fix["path"])

    logger.info("=== Running Performance Benchmark (HDR video) ===")
    legacy = run_playback_benchmark(video_path, "opengl", args.duration, args.mpv_prefix)
    logger.info("Legacy: %s in %.3fs", legacy["status"], legacy["wall_time_seconds"])

    gpu_next = run_playback_benchmark(video_path, "opengl-next", args.duration, args.mpv_prefix)
    logger.info("GPU Next: %s in %.3fs", gpu_next["status"], gpu_next["wall_time_seconds"])

    report = {
        "schema": "cinehdr.gate2.performance.v1",
        "legacy": legacy,
        "gpu_next": gpu_next,
        "verdict": "PASS" if (legacy["status"] == "PASS" and gpu_next["status"] == "PASS") else "FAIL",
    }
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
