#!/usr/bin/env python3
"""Observe natural HDR/SDR/HDR playlist transitions in the installed Flatpak."""

import argparse
import json
import logging
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib


APP_ID = "io.github.rusmikev.CineHDR"
ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / "validation-reports" / "playlist-fixtures"
PHASES = ("01-hdr", "02-sdr", "03-hdr")
logger = logging.getLogger(__name__)


def check_media():
    """Check the prepared inputs without opening a display or playing video."""
    subprocess.run(["sha256sum", "--check", "--status", "SHA256SUMS"], cwd=MEDIA, check=True)
    for phase in PHASES:
        path = MEDIA / f"{phase}.mp4"
        data = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=color_transfer,color_primaries,pix_fmt:format=duration",
            "-of", "json", str(path),
        ], text=True))
        stream = data["streams"][0]
        sdr = phase == "02-sdr"
        expected = ("iec61966-2-1", "bt709") if sdr else ("smpte2084", "bt2020")
        if (stream["color_transfer"], stream["color_primaries"]) != expected:
            raise ValueError(f"Unexpected color tags: {phase}")
        if stream["pix_fmt"] != "yuv420p10le" or abs(float(data["format"]["duration"]) - 20) > 0.1:
            raise ValueError(f"Unexpected format or duration: {phase}")
    logger.info("Three fixture checksums, color tags and 20-second durations verified")


def prepare_media():
    """Extend verified fixtures and add a moving frame counter for visual observation."""
    MEDIA.mkdir(parents=True, exist_ok=True)
    for mode in ("hdr", "sdr"):
        subprocess.run([
            sys.executable, str(ROOT / "tests" / "generate_patterns.py"),
            "--mode", mode, "--fixture-profile", "hevc-main10-yuv420p10-lossless",
            "--cache-dir", str(MEDIA),
        ], check=True, stdout=subprocess.DEVNULL)
    for index, phase in enumerate(PHASES, 1):
        mode = "sdr" if phase == "02-sdr" else "hdr"
        source = MEDIA / f"cinehdr-gate2-hevc-main10-yuv420p10-lossless-{mode}.mp4"
        transfer, primaries = ("iec61966-2-1", "bt709") if mode == "sdr" else ("smpte2084", "bt2020")
        matrix = "bt709" if mode == "sdr" else "bt2020nc"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1",
            "-i", str(source), "-t", "20", "-an", "-vf",
            f"drawtext=text='{mode.upper()} {index} frame %{{n}}':x=10:y=10:fontsize=18:fontcolor=white:box=1:boxcolor=black",
            "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
            "-x265-params", "lossless=1:pools=none:frame-threads=1:wpp=0:log-level=error",
            "-color_trc", transfer, "-color_primaries", primaries, "-colorspace", matrix,
            "-tag:v", "hvc1", str(MEDIA / f"{phase}.mp4"),
        ], check=True)
    sums = subprocess.check_output(["sha256sum", *(f"{phase}.mp4" for phase in PHASES)], cwd=MEDIA)
    (MEDIA / "SHA256SUMS").write_bytes(sums)
    check_media()


class PlaylistObserver:
    """Sample existing Copy Report output; never advance, seek or pause playback."""

    def __init__(self, directory):
        self.directory = directory
        self.started = time.monotonic()
        self.phase_started = self.started
        self.generation = 0
        self.saved = set()
        self.complete = False
        self.failed = False

    def poll(self):
        # GLib callback boundary: report failures and request normal window teardown.
        app = Gio.Application.get_default()
        windows = app.get_windows() if app is not None else []
        window = windows[0] if windows else None
        try:
            now = time.monotonic()
            if now - self.started > 90:
                raise TimeoutError("Playlist did not complete its three phases within 90 seconds")
            if window is None:
                return GLib.SOURCE_CONTINUE
            generation = window._media_load_generation
            if generation == 0:
                return GLib.SOURCE_CONTINUE
            if generation != self.generation:
                if generation != self.generation + 1 or generation > 3:
                    raise RuntimeError("Unexpected media-load sequence")
                if self.generation and len(self.saved) != 2 * self.generation:
                    raise RuntimeError("Previous playlist item ended before both diagnostic samples")
                self.generation = generation
                self.phase_started = now
                logger.info("Natural playlist phase %s started", PHASES[generation - 1])
            age = now - self.phase_started
            for label, delay in (("early", 3), ("late", 10)):
                name = f"{PHASES[generation - 1]}-{label}.txt"
                if age >= delay and name not in self.saved:
                    report = sys.modules["cinehdr.diagnostics_report"].generate_video_output_report(window)
                    (self.directory / name).write_text(report)
                    self.saved.add(name)
                    logger.info("Saved %s", name)
            if generation == 3 and age >= 15:
                self.complete = len(self.saved) == 6
                logger.info("Observation complete; closing the window normally")
                window.close()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        except Exception:
            logger.exception("Playlist observation failed")
            self.failed = True
            if window is not None:
                window.close()
            elif app is not None:
                app.quit()
            return GLib.SOURCE_REMOVE


def run_inside(directory):
    if os.environ.get("FLATPAK_ID") != APP_ID:
        raise RuntimeError("The observer must run inside the CineHDR Flatpak")
    observer = PlaylistObserver(directory)
    # Only Gio/GLib are imported before the installed launcher applies GTK CM policy.
    GLib.timeout_add_seconds(1, observer.poll)
    sys.argv = ["/app/bin/cinehdr", *(str(MEDIA / f"{phase}.mp4") for phase in PHASES)]
    try:
        runpy.run_path("/app/bin/cinehdr", run_name="__main__")
    except SystemExit as error:
        if error.code not in (None, 0):
            raise
    if observer.failed or not observer.complete:
        logger.error("Incomplete capture; no PASS is inferred")
        return 1
    logger.info("Six Copy Reports captured; visual acceptance and report review remain required")
    return 0


def run_host():
    check_media()
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise RuntimeError("Run this command from the owner's Wayland desktop terminal")
    directory = Path(tempfile.mkdtemp(prefix="p15-playlist-", dir=ROOT / "validation-reports"))
    commit = subprocess.check_output(["flatpak", "info", "--show-commit", APP_ID], text=True).strip()
    (directory / "flatpak-commit.txt").write_text(commit + "\n")
    command = ["flatpak", "run", "--command=python3",
               f"--filesystem={Path(__file__).resolve().parent}:ro",
               f"--filesystem={MEDIA}:ro", f"--filesystem={directory}"]
    env = {
        "GSETTINGS_BACKEND": "memory", "CINEHDR_NON_UNIQUE": "1",
        "CINEHDR_RENDER_BACKEND": "legacy", "CINEHDR_GPU_VALIDATION": "",
        "CINEHDR_DIAGNOSTICS_EXPORT_PATH": "", "CINEHDR_DIAGNOSTICS_EXPORT_DIR": "",
        "XDG_CONFIG_HOME": str(directory / "config"),
        "XDG_CACHE_HOME": str(directory / "cache"),
        "XDG_DATA_HOME": str(directory / "data"), "LC_ALL": "C.UTF-8",
    }
    command.extend(f"--env={key}={value}" for key, value in env.items())
    command.extend([APP_ID, str(Path(__file__).resolve()), "--inside", str(directory)])
    logger.info("Watch HDR 1 -> SDR 2 -> HDR 3; automatic close after about 55 seconds")
    logger.info("Report directory: %s", directory)
    with (directory / "app.log").open("w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    (directory / "exit-code.txt").write_text(f"{result.returncode}\n")
    logger.info("Exit code: %s; Copy Reports: %s/6", result.returncode, len(list(directory.glob("0*-*.txt"))))
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify prepared inputs without launching GTK")
    parser.add_argument("--prepare", action="store_true", help="Build and verify the three local fixtures without GTK")
    parser.add_argument("--inside", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.inside:
        return run_inside(args.inside)
    if args.prepare:
        prepare_media()
        return 0
    if args.check:
        check_media()
        return 0
    return run_host()


if __name__ == "__main__":
    sys.exit(main())
