#!/usr/bin/env python3

"""Build and run the development-only embedded GPU Next DV A/B probe."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = PROJECT_ROOT.parent
DEFAULT_MPV_SOURCE = Path(os.environ.get("CINEHDR_MPV_SOURCE", WORK_ROOT / "mpv-gpu-next"))
DEFAULT_MPV_PREFIX = Path(os.environ.get("CINEHDR_MPV_PREFIX", WORK_ROOT / "mpv-gpu-next-prefix"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Render the same frame with Dolby Vision metadata enabled and "
            "disabled through embedded opengl-next."
        )
    )
    parser.add_argument("video", type=Path)
    parser.add_argument("--timestamp", default="300")
    parser.add_argument("--target-peak", default="279")
    parser.add_argument("--hwdec", default="no")
    parser.add_argument(
        "--egl-platform",
        default="surfaceless",
        help="EGL platform for the offscreen probe (default: surfaceless)",
    )
    parser.add_argument("--mpv-source", type=Path, default=DEFAULT_MPV_SOURCE)
    parser.add_argument("--mpv-prefix", type=Path, default=DEFAULT_MPV_PREFIX)
    args = parser.parse_args()

    video = args.video.expanduser().resolve()
    header = args.mpv_source / "include"
    libdir = args.mpv_prefix / "lib64"
    if not video.is_file():
        parser.error(f"video does not exist: {video}")
    if not (header / "mpv" / "render.h").is_file():
        parser.error(f"pinned mpv headers not found under: {header}")
    if not any(libdir.glob("libmpv.so*")):
        parser.error(f"pinned libmpv not found under: {libdir}")

    with tempfile.TemporaryDirectory(prefix="cinehdr-dovi-ab-") as temp_dir:
        binary = Path(temp_dir) / "dovi_rpu_ab_egl"
        compile_command = [
            os.environ.get("CC", "cc"),
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            f"-I{header}",
            str(PROJECT_ROOT / "tests" / "dovi_rpu_ab_egl.c"),
            f"-L{libdir}",
            f"-Wl,-rpath,{libdir}",
            "-lmpv",
            "-lEGL",
            "-lGL",
            "-lm",
            "-o",
            str(binary),
        ]
        subprocess.run(compile_command, check=True)
        run_command = [
            str(binary),
            str(video),
            str(args.timestamp),
            str(args.target_peak),
            str(args.hwdec),
        ]
        run_environment = os.environ.copy()
        if args.egl_platform:
            run_environment["EGL_PLATFORM"] = args.egl_platform
        return subprocess.run(
            run_command, check=False, env=run_environment
        ).returncode


if __name__ == "__main__":
    sys.exit(main())
