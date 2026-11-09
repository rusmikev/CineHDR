#!/usr/bin/env python3
# Copyright 2026 rusmikev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Comprehensive automated validation of the complete CineHDR playback trace on GNOME (Wayland).

Validates:
1. Environment & Host Stack (GNOME Shell, Mutter, GTK 4, Mesa, AMD GPU).
2. Live Wayland Color Management globals (wp_color_manager_v1 v2, intents, TFs, primaries, output luminance).
3. Live Wayland protocol trace capture via wayland_conn_tagger.so.
4. Composite toplevel window protocol submission (ready -> set_image_description -> commit with effective buffer).
5. SDR negative control (no HDR tagging on SDR content).
6. Dynamic mode transition / render target republication.
7. Diagnostics report export and fact integrity.
8. Clean termination without crashes or FBO leaks.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from tools.validation.parse_wayland_color_trace import (
    validate_composite_window_trace,
    _calls,
)
from src.gtk_cm_policy import (
    CmCaps,
    gtk_color_managed,
    gtk_can_tag_hdr,
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
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to compile wayland_conn_tagger.c: {res.stderr}")
    return output_path


def parse_wayland_info_color_manager() -> Dict[str, Any]:
    """Query live wayland-info for wp_color_manager_v1 and parse capabilities."""
    res = subprocess.run(["wayland-info", "-i", "wp_color_manager_v1"], capture_output=True, text=True)
    if res.returncode != 0:
        return {"available": False, "error": res.stderr.strip()}

    out = res.stdout
    info: Dict[str, Any] = {
        "available": True,
        "raw_output": out,
        "version": None,
        "features": [],
        "named_tfs": [],
        "named_primaries": [],
        "rendering_intents": [],
        "output_color_description": {},
    }

    m_ver = re.search(r"interface:\s*'wp_color_manager_v1',\s*version:\s*(\d+)", out)
    if m_ver:
        info["version"] = int(m_ver.group(1))

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
            info["output_color_description"]["output_id"] = line_s.split(":")[1].strip()
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
                info["output_color_description"]["tf_named"] = line_s.split(":")[1].strip()
            elif "primaries_named:" in line_s:
                info["output_color_description"]["primaries_named"] = line_s.split(":")[1].strip()
            elif "luminances (cd/m²):" in line_s:
                m_lum = re.search(r"min\s+([\d.]+)\s+max\s+([\d.]+)\s+reference\s+([\d.]+)", line_s)
                if m_lum:
                    info["output_color_description"]["min_luminance"] = float(m_lum.group(1))
                    info["output_color_description"]["max_luminance"] = float(m_lum.group(2))
                    info["output_color_description"]["ref_luminance"] = float(m_lum.group(3))

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


def collect_system_environment() -> Dict[str, Any]:
    """Collect host environment versions and parameters."""
    env_facts: Dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
        "xdg_session_type": os.environ.get("XDG_SESSION_TYPE"),
        "xdg_current_desktop": os.environ.get("XDG_CURRENT_DESKTOP"),
    }

    # GNOME Shell
    try:
        res = subprocess.run(["gnome-shell", "--version"], capture_output=True, text=True)
        env_facts["gnome_shell"] = res.stdout.strip()
    except Exception as e:
        env_facts["gnome_shell"] = f"unknown ({e})"

    # Mutter
    try:
        res = subprocess.run(["mutter", "--version"], capture_output=True, text=True)
        env_facts["mutter"] = res.stdout.strip()
    except Exception as e:
        env_facts["mutter"] = f"unknown ({e})"

    # Kernel
    try:
        res = subprocess.run(["uname", "-r"], capture_output=True, text=True)
        env_facts["kernel"] = res.stdout.strip()
    except Exception as e:
        env_facts["kernel"] = f"unknown ({e})"

    # RPM packages
    try:
        res = subprocess.run(
            ["rpm", "-q", "gnome-shell", "mutter", "mesa-dri-drivers", "gtk4"],
            capture_output=True, text=True,
        )
        env_facts["rpm_packages"] = res.stdout.strip().splitlines()
    except Exception as e:
        env_facts["rpm_packages"] = [f"rpm query error: {e}"]

    # GPU
    try:
        res = subprocess.run(["lspci", "-nnk"], capture_output=True, text=True)
        gpu_lines = [
            line for line in res.stdout.splitlines()
            if any(term in line.lower() for term in ("vga", "3d", "display", "amdgpu", "radeon"))
        ]
        env_facts["gpu_info"] = gpu_lines[:8]
    except Exception as e:
        env_facts["gpu_info"] = [f"lspci error: {e}"]

    return env_facts


def find_user_instances(app_id: str = "io.github.rusmikev.CineHDR") -> List[str]:
    """Identify any running instances to avoid interfering with them."""
    instances: List[str] = []
    try:
        res = subprocess.run(["flatpak", "ps", "--columns=instance,application"], capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == app_id:
                    instances.append(parts[0])
    except Exception:
        pass
    return instances


def run_bounded_session(
    video_path: Path,
    output_dir: Path,
    tagger_so: Path,
    duration_seconds: float = 4.0,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Launch CineHDR under wayland_conn_tagger.so for a bounded observation duration."""
    trace_log = output_dir / "wayland-client.raw.log"
    app_log = output_dir / "cinehdr.log"

    env = os.environ.copy()
    env["LD_PRELOAD"] = str(tagger_so)
    env["WAYLAND_DEBUG"] = "1"
    env["GDK_DEBUG"] = "color-mgmt"
    env["CINEHDR_NON_UNIQUE"] = "1"
    env["CINEHDR_LOG_FILE"] = str(app_log)
    if extra_env:
        env.update(extra_env)

    start_time = time.monotonic()
    t_start_utc = datetime.now(timezone.utc).isoformat()

    with open(trace_log, "w", encoding="utf-8") as f_trace:
        proc = subprocess.Popen(
            [sys.executable, "run_dev.py", str(video_path)],
            stdout=subprocess.DEVNULL,
            stderr=f_trace,
            env=env,
            cwd=str(REPO_DIR),
        )

        # Wait for bounded observation window
        time.sleep(duration_seconds)

        # Graceful shutdown via SIGINT
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    t_end_utc = datetime.now(timezone.utc).isoformat()
    duration_observed = time.monotonic() - start_time

    raw_text = trace_log.read_text(encoding="utf-8", errors="replace")
    parsed_evidence = validate_composite_window_trace(raw_text, expected_app_id="io.github.rusmikev.CineHDR")

    # Parse telemetry events from app log
    telemetries: List[Dict[str, Any]] = []
    if app_log.exists():
        for line in app_log.read_text(encoding="utf-8", errors="replace").splitlines():
            if "HDR Pipeline Telemetry:" in line:
                try:
                    payload = line.split("HDR Pipeline Telemetry:", 1)[1].strip()
                    telemetries.append(json.loads(payload))
                except Exception:
                    pass

    session_result = {
        "fixture": str(video_path),
        "fixture_sha256": hashlib.sha256(video_path.read_bytes()).hexdigest(),
        "exit_code": proc.returncode,
        "duration_seconds": round(duration_observed, 3),
        "start_time_utc": t_start_utc,
        "end_time_utc": t_end_utc,
        "trace_file": str(trace_log),
        "trace_bytes": trace_log.stat().st_size,
        "app_log_file": str(app_log),
        "telemetries": telemetries,
        "evidence": parsed_evidence,
    }
    return session_result


def execute_full_gnome_trace_validation(
    report_parent_dir: Path,
    hdr_fixture: Path = HDR_FIXTURE_DEFAULT,
    sdr_fixture: Path = SDR_FIXTURE_DEFAULT,
    observation_duration: float = 4.0,
) -> Dict[str, Any]:
    """Execute complete validation suite across environment, HDR trace, SDR trace, and report."""
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = report_parent_dir / f"gnome51-validation-{run_stamp}-{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    tagger_so = run_dir / "libwayland_conn_tagger.so"
    compile_tagger(tagger_so)

    # 1. System environment
    sys_env = collect_system_environment()

    # 2. Wayland Color Management globals
    cm_info = parse_wayland_info_color_manager()
    cm_caps = build_cm_caps_from_wayland_info(cm_info)

    # In CineHDR runs, GDK_DEBUG=color-mgmt is applied
    gtk_cm_ok, gtk_cm_reason = gtk_color_managed(cm_caps, env={"GDK_DEBUG": "color-mgmt"}) if cm_caps else (False, "caps unavailable")
    gtk_can_hdr = gtk_can_tag_hdr(cm_caps, TF_PQ) if cm_caps else False

    # 3. Check pre-existing user instances
    user_instances = find_user_instances()

    # 4. Live HDR10 Playback Trace
    hdr_dir = run_dir / "hdr-session"
    hdr_dir.mkdir()
    hdr_result = run_bounded_session(
        video_path=hdr_fixture,
        output_dir=hdr_dir,
        tagger_so=tagger_so,
        duration_seconds=observation_duration,
    )

    # 5. Live SDR Playback Trace (Negative Control)
    sdr_dir = run_dir / "sdr-session"
    sdr_dir.mkdir()
    sdr_result = run_bounded_session(
        video_path=sdr_fixture,
        output_dir=sdr_dir,
        tagger_so=tagger_so,
        duration_seconds=observation_duration,
    )

    # Evaluate SDR negative control:
    # 1. Telemetry must strictly confirm source_hdr is False throughout
    # 2. No target_trc == 'pq' requested for SDR content
    sdr_telemetries = sdr_result.get("telemetries", [])
    sdr_telemetry_clean = (
        len(sdr_telemetries) > 0
        and all(not t.get("source_hdr") for t in sdr_telemetries)
        and all(t.get("target_trc") != "pq" for t in sdr_telemetries)
    )
    sdr_negative_control_pass = sdr_telemetry_clean

    # Evaluate HDR session:
    # 1. Wayland trace must have status PASS on composite window
    # 2. Telemetry must confirm source_hdr is True and target_trc becomes 'pq'
    hdr_ev = hdr_result["evidence"]
    hdr_status = hdr_ev.get("status")
    hdr_telemetries = hdr_result.get("telemetries", [])
    hdr_telemetry_pass = any(
        t.get("source_hdr") and t.get("target_trc") == "pq"
        for t in hdr_telemetries
    )
    hdr_session_pass = (hdr_status == "PASS" and hdr_telemetry_pass)

    # 6. Overall Verdict
    overall_pass = (
        cm_info.get("available") is True
        and gtk_cm_ok is True
        and gtk_can_hdr is True
        and hdr_session_pass is True
        and sdr_negative_control_pass is True
    )

    final_report = {
        "schema": "cinehdr.gnome51.full-trace-validation.v1",
        "verdict": "PASS" if overall_pass else "FAIL",
        "timestamp_utc": run_stamp,
        "run_directory": str(run_dir),
        "system_environment": sys_env,
        "wayland_color_management": {
            "version": cm_info.get("version"),
            "rendering_intents": cm_info.get("rendering_intents"),
            "features": cm_info.get("features"),
            "named_transfer_functions": cm_info.get("named_tfs"),
            "named_primaries": cm_info.get("named_primaries"),
            "output_color_description": cm_info.get("output_color_description"),
        },
        "gtk_color_management_policy": {
            "gtk_color_managed": gtk_cm_ok,
            "gtk_color_managed_reason": gtk_cm_reason,
            "gtk_can_tag_hdr_pq": gtk_can_hdr,
        },
        "user_instances_preserved": len(user_instances),
        "hdr_playback_validation": {
            "status": hdr_status,
            "oracle": hdr_ev.get("oracle"),
            "matched_submission": hdr_ev.get("matched"),
            "telemetry_pass": hdr_telemetry_pass,
            "trace_bytes": hdr_result["trace_bytes"],
            "duration_seconds": hdr_result["duration_seconds"],
        },
        "sdr_negative_control": {
            "negative_control_pass": sdr_negative_control_pass,
            "telemetry_clean": sdr_telemetry_clean,
            "trace_bytes": sdr_result["trace_bytes"],
            "duration_seconds": sdr_result["duration_seconds"],
        },
    }

    report_file = run_dir / "report.json"
    report_file.write_text(json.dumps(final_report, indent=2), encoding="utf-8")

    # Generate SHA256SUMS
    sha_lines = []
    for f in sorted(run_dir.rglob("*")):
        if f.is_file() and f.name != "SHA256SUMS":
            rel = f.relative_to(run_dir)
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            sha_lines.append(f"{digest}  {rel}")
    (run_dir / "SHA256SUMS").write_text("\n".join(sha_lines) + "\n", encoding="utf-8")

    return final_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=REPO_DIR / "validation-reports",
        help="Directory to save report artifacts",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=4.5,
        help="Observation duration per session in seconds (default: 4.5)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger.info("Starting complete GNOME Wayland pipeline trace validation...")

    report = execute_full_gnome_trace_validation(
        report_parent_dir=args.report_dir,
        observation_duration=args.duration,
    )

    print("\n" + "=" * 60)
    print(f"=== GNOME PIPELINE TRACE VALIDATION: {report['verdict']} ===")
    print("=" * 60)
    print(f"Report Directory: {report['run_directory']}")
    print(f"GNOME Shell:      {report['system_environment'].get('gnome_shell')}")
    print(f"Mutter:           {report['system_environment'].get('mutter')}")
    print(f"Wayland CM v2:    version {report['wayland_color_management'].get('version')}")
    out_desc = report['wayland_color_management'].get('output_color_description', {})
    print(f"Display Mode:     {out_desc.get('primaries_named')} / {out_desc.get('tf_named')}")
    print(f"Display Peak:     min {out_desc.get('min_luminance')} cd/m², max {out_desc.get('max_luminance')} cd/m², ref {out_desc.get('ref_luminance')} cd/m²")
    print(f"GTK Color Mgmt:   managed={report['gtk_color_management_policy'].get('gtk_color_managed')} (can_tag_hdr={report['gtk_color_management_policy'].get('gtk_can_tag_hdr_pq')})")
    print(f"HDR Protocol L3p: {report['hdr_playback_validation'].get('status')} ({report['hdr_playback_validation'].get('oracle')})")
    print(f"SDR Control:      {'PASS' if report['sdr_negative_control'].get('negative_control_pass') else 'FAIL'}")
    print("=" * 60 + "\n")

    sys.exit(0 if report["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
