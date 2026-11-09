#!/usr/bin/env python3
# Copyright 2026 rusmikev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native GNOME legacy playback: HDR L3p, SDR texture state and normal close.

Explicit owner-run hardware check. Does not validate the Flatpak manifest,
mode transitions, FBO leaks, measured luminance, or visual HDR delivery (L4).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from tools.validation.parse_wayland_color_trace import (
    validate_composite_window_trace,
    TraceValidationError,
    FATAL_RE,
)
from src.gtk_cm_policy import (
    CmCaps,
    gtk_color_managed,
    TF_PQ,
    TF_SRGB,
    TF_GAMMA22,
    TF_EXT_LINEAR,
    PRIM_BT2020,
    PRIM_SRGB,
    INTENT_PERCEPTUAL,
    FEAT_PARAMETRIC,
    FEAT_SET_PRIMARIES,
)

logger = logging.getLogger("gnome_full_trace")

TAGGER_SRC = REPO_DIR / "tools/validation/wayland_conn_tagger.c"
FIXTURES_DIR = REPO_DIR / "validation-reports/fixtures"
HDR_FIXTURE_DEFAULT = FIXTURES_DIR / "cinehdr-gate2-hevc-main10-yuv420p10-lossless-hdr.mp4"
SDR_FIXTURE_DEFAULT = FIXTURES_DIR / "cinehdr-gate2-hevc-main10-yuv420p10-lossless-sdr.mp4"


def compile_tagger(output_path: Path) -> Path:
    """Compile wayland_conn_tagger.c into a shared library."""
    if output_path.exists() and output_path.stat().st_mtime >= TAGGER_SRC.stat().st_mtime:
        return output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "gcc", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra",
        str(TAGGER_SRC), "-o", str(output_path),
        "-ldl", "-lwayland-client", "-lpthread",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to compile wayland_conn_tagger.c: {res.stderr}")
    return output_path


def parse_wayland_info_color_manager(out: str) -> Dict[str, Any]:
    """Parse saved wayland-info text without connecting to a display."""
    info: Dict[str, Any] = {
        "available": False,
        "raw_output": out,
        "version": None,
        "features": [],
        "named_tfs": [],
        "named_primaries": [],
        "rendering_intents": [],
        "output_color_descriptions": [],
    }

    m_ver = re.search(r"interface:\s*'wp_color_manager_v1',\s*version:\s*(\d+)", out)
    if m_ver:
        info["version"] = int(m_ver.group(1))
        info["available"] = True

    # Parse sections
    current_section = None
    for line in out.splitlines():
        line_s = line.strip()
        if "supported rendering intents:" in line_s:
            current_section = "intents"
            continue
        elif "supported features:" in line_s:
            current_section = "features"
            continue
        elif "supported named transfer functions:" in line_s:
            current_section = "tfs"
            continue
        elif "supported named primaries:" in line_s:
            current_section = "primaries"
            continue
        elif line_s.startswith("output:"):
            current_section = "output"
            info["output_color_descriptions"].append({})
            continue

        if current_section == "intents" and line.startswith("\t\t"):
            info["rendering_intents"].append(line_s)
        elif current_section == "features" and line.startswith("\t\t"):
            info["features"].append(line_s)
        elif current_section == "tfs" and line.startswith("\t\t"):
            info["named_tfs"].append(line_s)
        elif current_section == "primaries" and line.startswith("\t\t"):
            info["named_primaries"].append(line_s)
        elif current_section == "output" and line.startswith("\t\t"):
            if "tf_named:" in line_s:
                info["output_color_descriptions"][-1]["tf_named"] = line_s.split(":")[1].strip()
            elif "primaries_named:" in line_s:
                info["output_color_descriptions"][-1]["primaries_named"] = line_s.split(":")[1].strip()
            elif "luminances (cd/m²):" in line_s:
                m_lum = re.search(r"min\s+([\d.]+)\s+max\s+([\d.]+)\s+reference\s+([\d.]+)", line_s)
                if m_lum:
                    info["output_color_descriptions"][-1]["min_luminance"] = float(m_lum.group(1))
                    info["output_color_descriptions"][-1]["max_luminance"] = float(m_lum.group(2))
                    info["output_color_descriptions"][-1]["ref_luminance"] = float(m_lum.group(3))

    return info


def build_cm_caps_from_wayland_info(info: Dict[str, Any]) -> Optional[CmCaps]:
    """Convert parsed wayland-info to CmCaps dataclass."""
    if not info.get("available"):
        return None

    intents = set()
    if "perceptual" in info.get("rendering_intents", []):
        intents.add(INTENT_PERCEPTUAL)

    features = set()
    feat_map = {
        "parametric": FEAT_PARAMETRIC,
        "set_primaries": FEAT_SET_PRIMARIES,
    }
    for f in info.get("features", []):
        if f in feat_map:
            features.add(feat_map[f])

    tfs = set()
    tf_map = {
        "srgb": TF_SRGB,
        "st2084_pq": TF_PQ,
        "gamma22": TF_GAMMA22,
        "ext_linear": TF_EXT_LINEAR,
    }
    for t in info.get("named_tfs", []):
        if t in tf_map:
            tfs.add(tf_map[t])

    primaries = set()
    prim_map = {
        "srgb": PRIM_SRGB,
        "bt2020": PRIM_BT2020,
    }
    for p in info.get("named_primaries", []):
        if p in prim_map:
            primaries.add(prim_map[p])

    return CmCaps(
        intents=frozenset(intents),
        features=frozenset(features),
        tfs=frozenset(tfs),
        primaries=frozenset(primaries),
    )


def command_output(command: List[str]) -> Optional[str]:
    """Optional host facts stay unknown when a bounded query fails."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        logger.warning("Host query %s failed: %s", command[0], type(error).__name__)
        return None
    if result.returncode:
        logger.warning("Host query %s exited with %s", command[0], result.returncode)
        return None
    return result.stdout.strip()


def session_environment(output_dir: Path, tagger_so: Path, private: Path) -> Dict[str, str]:
    # Keep the Wayland socket and session bus, but isolate all persistent app data.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CINEHDR_", "GSETTINGS_", "GDK_", "GSK_", "GTK_", "MPV_"))}
    for name in ("CONFIG", "CACHE", "DATA", "STATE"):
        directory = private / name.lower()
        directory.mkdir()
        env[f"XDG_{name}_HOME"] = str(directory)
    env.update({
        "LD_PRELOAD": str(tagger_so), "WAYLAND_DEBUG": "1", "GDK_BACKEND": "wayland",
        "GDK_DEBUG": "color-mgmt", "GSETTINGS_BACKEND": "memory",
        "CINEHDR_NON_UNIQUE": "1", "CINEHDR_RENDER_BACKEND": "legacy",
        "CINEHDR_LOG_FILE": str(output_dir / "cinehdr.log"),
        "CINEHDR_DIAGNOSTICS_EXPORT_PATH": str(output_dir / "copy-report.txt"),
        "LC_ALL": "C.UTF-8",
    })
    return env


def read_session(output_dir: Path) -> Dict[str, Any]:
    """Analyze saved artifacts without opening a display or trusting an old verdict."""
    raw = (output_dir / "wayland-client.raw.log").read_text(errors="replace")
    app_path = output_dir / "cinehdr.log"
    app_text = app_path.read_text(errors="replace") if app_path.exists() else ""
    telemetries = []
    telemetry_valid = True
    for line in app_text.splitlines():
        if "HDR Pipeline Telemetry:" in line:
            try:
                event = json.loads(line.split("HDR Pipeline Telemetry:", 1)[1].strip())
                if not isinstance(event, dict):
                    raise ValueError("Telemetry must be an object")
                telemetries.append(event)
            except (ValueError, TypeError):
                logger.warning("Invalid pipeline telemetry in session")
                telemetry_valid = False
    diagnostics_path = output_dir / "copy-report.json"
    try:
        diagnostics = json.loads(diagnostics_path.read_text()) if diagnostics_path.exists() else {}
        if not isinstance(diagnostics, dict):
            raise ValueError("Diagnostic manifest must be an object")
    except (ValueError, TypeError):
        logger.warning("Invalid diagnostic manifest in session")
        diagnostics = {}
    try:
        evidence = validate_composite_window_trace(raw, expected_app_id="io.github.rusmikev.CineHDR")
    except TraceValidationError as error:
        logger.warning("Composite trace rejected: %s", error)
        evidence = {"status": "FAIL", "reason": str(error)}
    exit_path = output_dir / "exit-code.txt"
    timeout_path = output_dir / "timeout.txt"
    report_path = output_dir / "copy-report.txt"
    if diagnostics and (not report_path.exists() or diagnostics.get("report_sha256") !=
                        hashlib.sha256(report_path.read_bytes()).hexdigest()):
        logger.warning("Diagnostic report checksum missing or mismatched")
        diagnostics = {}
    return {
        "evidence": evidence,
        "telemetries": telemetries, "telemetry_valid": telemetry_valid,
        "diagnostics": diagnostics,
        "exit_code": int(exit_path.read_text()) if exit_path.exists() else None,
        "timed_out": timeout_path.read_text().strip() != "false" if timeout_path.exists() else None,
        "runtime_log": next((line.split("Render runtime ", 1)[1] for line in app_text.splitlines()
                             if "Render runtime " in line), None),
        "process_errors": bool(FATAL_RE.search(raw)) or any(token in raw or token in app_text for token in
                              ("Traceback (most recent call last)", "[ERROR]", "[CRITICAL]")),
    }


def evaluate_session(session: Dict[str, Any], *, hdr: bool) -> Dict[str, Any]:
    """Fail closed on missing media evidence; composite PQ is not a SDR texture test."""
    events = session["telemetries"]
    diag = session["diagnostics"]
    summary = diag.get("summary", {})
    frame = diag.get("media_frame") or {}
    expected_trc = "pq" if hdr else "auto"
    telemetry_ok = session["telemetry_valid"] and bool(events)
    if hdr:
        telemetry_ok &= any(t.get("source_hdr") is True and t.get("target_trc") == expected_trc
                            and t.get("hdr_mode") == "auto" for t in events)
    else:
        telemetry_ok &= all(t.get("source_hdr") is False and t.get("target_trc") == expected_trc
                            and t.get("hdr_mode") == "auto" for t in events)
    color = "Rec.2100 PQ (published texture)" if hdr else "sRGB (published texture)"
    checks = {
        "telemetry": bool(telemetry_ok),
        "media_frame": (diag.get("first_frame_confirmed") is True
                        and frame.get("source_width", 0) > 0 and frame.get("source_height", 0) > 0)
                       if diag else None,
        "published_texture": (summary.get("render_target_format") == ("GL_RGBA16F" if hdr else "GL_RGBA8")
                              and summary.get("render_target_depth") == (16 if hdr else 8)
                              and summary.get("gdk_color_state") == color) if diag else None,
        "legacy_active": (summary.get("active_api") == "opengl"
                          and summary.get("session_status") == "active"
                          and summary.get("startup_fallback_reason") in (None, "none", "None")) if diag else None,
        "normal_exit": (session["exit_code"] == 0 and session["timed_out"] is False)
                       if session["exit_code"] is not None else None,
        "no_process_errors": not session["process_errors"],
    }
    if hdr:
        checks["hdr_protocol"] = session["evidence"].get("status") == "PASS"
    status = "PASS" if all(value is True for value in checks.values()) else (
        "FAIL" if False in checks.values() else "UNCLEAR")
    return {"status": status, "checks": checks, "exit_code": session["exit_code"],
            "runtime_log": session["runtime_log"], "protocol": session["evidence"],
            "texture_summary": summary,
            "claim": "HDR composite submission and client texture" if hdr else
                     "SDR client texture and target TRC; composite color description is informational"}


def redact_artifacts(output_dir: Path, video_path: Path) -> None:
    """Do not persist personal paths or media names in the report bundle."""
    replacements = ((str(video_path), "<FIXTURE>"), (video_path.name, "<FIXTURE>"),
                    (str(REPO_DIR), "<REPO>"), (str(Path.home()), "<HOME>"))
    for path in output_dir.iterdir():
        if path.suffix not in (".log", ".txt", ".json"):
            continue
        value = path.read_text(errors="replace")
        for source, target in replacements:
            value = value.replace(source, target)
        path.write_text(value)
    # The exporter hashes the original text. Keep its manifest consistent after redaction.
    manifest = output_dir / "copy-report.json"
    report = output_dir / "copy-report.txt"
    if manifest.exists() and report.exists():
        try:
            data = json.loads(manifest.read_text())
            if not isinstance(data, dict):
                raise ValueError("Diagnostic manifest must be an object")
        except ValueError:
            logger.warning("Cannot update checksum of malformed diagnostic manifest")
        else:
            data["report_sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(data, indent=2))


def run_bounded_session(video_path: Path, output_dir: Path, tagger_so: Path,
                        duration_seconds: float = 4.0) -> Dict[str, Any]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("Observation duration must be finite and positive")
    video_path = video_path.resolve(strict=True)
    output_dir = output_dir.resolve()
    fixture_hash = hashlib.sha256(video_path.read_bytes()).hexdigest()
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="cinehdr-trace-") as temporary:
        env = session_environment(output_dir, tagger_so.resolve(), Path(temporary))
        with (output_dir / "wayland-client.raw.log").open("w") as trace:
            proc = subprocess.Popen([
                sys.executable, str(REPO_DIR / "tools/validation/gnome_trace_observer.py"),
                str(video_path), str(duration_seconds),
            ], stdout=trace, stderr=subprocess.STDOUT, env=env, cwd=REPO_DIR, start_new_session=True)
            try:
                proc.wait(timeout=duration_seconds + 30)
            except subprocess.TimeoutExpired:
                logger.error("Observation timed out; terminating only the test process")
                timed_out = True
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        logger.error("Test process did not terminate; killing it")
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=3)
    (output_dir / "exit-code.txt").write_text(f"{proc.returncode}\n")
    (output_dir / "timeout.txt").write_text("true\n" if timed_out else "false\n")
    redact_artifacts(output_dir, video_path)
    result = read_session(output_dir)
    result["fixture_sha256"] = fixture_hash
    result["timed_out"] = timed_out
    # Even a SIGTERM handler returning zero does not make timeout a clean exit.
    if timed_out:
        result["process_errors"] = True
    (output_dir / "session.json").write_text(json.dumps(result, indent=2))
    return result


def execute_full_gnome_trace_validation(report_parent_dir: Path,
                                       hdr_fixture: Path = HDR_FIXTURE_DEFAULT,
                                       sdr_fixture: Path = SDR_FIXTURE_DEFAULT,
                                       observation_duration: float = 4.0) -> Dict[str, Any]:
    if not math.isfinite(observation_duration) or observation_duration <= 0:
        raise ValueError("Observation duration must be finite and positive")
    if not os.environ.get("WAYLAND_DISPLAY") or "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        raise RuntimeError("Run explicitly from the owner's GNOME Wayland session")
    hdr_fixture.resolve(strict=True)
    sdr_fixture.resolve(strict=True)
    # Build failures must not silently fall through to stale run_dev.py resources.
    subprocess.run(["meson", "compile", "-C", "build"], cwd=REPO_DIR, check=True, timeout=120)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = report_parent_dir.resolve() / f"gnome-validation-{stamp}-{os.getpid()}"
    run_dir.mkdir(parents=True)
    tagger_so = compile_tagger(run_dir / "libwayland_conn_tagger.so")
    cm_info = parse_wayland_info_color_manager(command_output(["wayland-info", "-i", "wp_color_manager_v1"]) or "")
    caps = build_cm_caps_from_wayland_info(cm_info)
    cm_ok, reason = gtk_color_managed(caps, env={"GDK_DEBUG": "color-mgmt"}) if caps else (False, "caps unavailable")
    sessions = {}
    for label, fixture in (("hdr", hdr_fixture), ("sdr", sdr_fixture)):
        directory = run_dir / f"{label}-session"
        directory.mkdir()
        session = run_bounded_session(fixture, directory, tagger_so, observation_duration)
        sessions[label] = evaluate_session(session, hdr=label == "hdr")
    final_report = {
        "schema": "cinehdr.gnome51.full-trace-validation.v1",
        "verdict": "PASS" if cm_ok and all(item["status"] == "PASS" for item in sessions.values()) else "FAIL",
        "timestamp_utc": stamp, "run_directory": run_dir.name,
        "scope": "native run_dev.py, legacy, auto; not Flatpak validation or L4",
        "source_sha256": {str(path.relative_to(REPO_DIR)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted([*REPO_DIR.glob("src/*.py"), REPO_DIR / "run_dev.py",
                                              Path(__file__).resolve(),
                                              REPO_DIR / "tools/validation/gnome_trace_observer.py",
                                              REPO_DIR / "tools/validation/parse_wayland_color_trace.py"])},
        "git_commit": command_output(["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"]),
        "working_tree_status": command_output(["git", "-C", str(REPO_DIR), "status", "--porcelain", "--untracked-files=no"]),
        "system_environment": {"gnome_shell": command_output(["gnome-shell", "--version"]),
                               "mutter": command_output(["mutter", "--version"])},
        "wayland_color_management": {key: value for key, value in cm_info.items() if key != "raw_output"},
        "gtk_color_management_policy": {"gtk_color_managed": cm_ok, "reason": reason},
        "hdr_playback_validation": sessions["hdr"], "sdr_negative_control": sessions["sdr"],
        "not_run": ["Flatpak", "L4", "mode transitions", "FBO leak detection"],
    }
    (run_dir / "report.json").write_text(json.dumps(final_report, indent=2))
    sums = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(run_dir)}"
            for path in sorted(run_dir.rglob("*")) if path.is_file()]
    (run_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    logger.info("Report saved in %s", run_dir)
    return final_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=REPO_DIR / "validation-reports")
    parser.add_argument("--duration", type=float, default=4.5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        report = execute_full_gnome_trace_validation(args.report_dir, observation_duration=args.duration)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        logger.error("Validation incomplete: %s", error)
        return 1
    logger.info("Native validation: %s; Flatpak and L4: NOT_RUN", report["verdict"])
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
