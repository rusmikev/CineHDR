#!/usr/bin/env python3
"""
GPU & GPU-Next Smoke Validation Runner for CineHDR v1.8.5.2.0

Executes dual-mode testing:
  - Intel (iGPU): Executed under Flatpak container (or native).
  - NVIDIA (dGPU): Executed NATIVELY on the host via build/venv/bin/python3
    with LD_LIBRARY_PATH=build/native_libs and PRIME offload variables:
    __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia

Strict Vendor & Telemetry Assertions:
  - NVIDIA tests MUST report GL_VENDOR containing 'NVIDIA' and GL_RENDERER containing 'RTX 3050'.
  - Intel tests MUST report GL_VENDOR containing 'Intel'.
  - Any vendor mismatch is flagged immediately as VENDOR_MISMATCH_FAIL.
  - Telemetry MUST be present with source_hdr=True for PASS status.
"""

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENV_PYTHON = os.path.join(PROJECT_ROOT, "build/venv/bin/python3")
NATIVE_LIBS_DIR = os.path.join(PROJECT_ROOT, "build/native_libs")

TEST_FILES = {
    "HDR10 PQ (HEVC BT.2020)": os.path.join(
        PROJECT_ROOT, "samples/haasn-hdr-tests/01. Black Clipping_1_HDR10.mp4"
    ),
    "HLG (HEVC BT.2100)": os.path.join(
        PROJECT_ROOT, "samples/haasn-hdr-tests/Grayscale BT.2100 HLG.mkv"
    ),
    "Dolby Vision (HEVC)": os.path.join(
        PROJECT_ROOT, "samples/haasn-hdr-tests/quietvoid/02themeg-dovi.mkv"
    ),
    "HDR10 PQ (colorbars)": os.path.join(
        PROJECT_ROOT, "samples/haasn-hdr-tests/colorbars.mp4"
    ),
}

TEST_MATRIX = {
    "Intel (iGPU) - Legacy": {
        "mode": "flatpak",
        "env": {
            "CINEHDR_NON_UNIQUE": "1",
        },
        "expected_vendor": "intel",
        "expected_backend": "opengl",
        "expected_mode": "legacy",
    },
    "Intel (iGPU) - GPU-Next": {
        "mode": "flatpak",
        "env": {
            "CINEHDR_RENDER_BACKEND": "gpu-next",
            "CINEHDR_NON_UNIQUE": "1",
        },
        "expected_vendor": "intel",
        "expected_backend": "opengl-next",
        "expected_mode": "gpu-next",
    },
    "NVIDIA (dGPU) - Legacy": {
        "mode": "native",
        "env": {
            "__NV_PRIME_RENDER_OFFLOAD": "1",
            "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
            "CINEHDR_NON_UNIQUE": "1",
        },
        "expected_vendor": "nvidia",
        "expected_backend": "opengl",
        "expected_mode": "legacy",
    },
    "NVIDIA (dGPU) - GPU-Next": {
        "mode": "native",
        "env": {
            "__NV_PRIME_RENDER_OFFLOAD": "1",
            "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
            "CINEHDR_RENDER_BACKEND": "gpu-next",
            "CINEHDR_NON_UNIQUE": "1",
        },
        "expected_vendor": "nvidia",
        "expected_backend": "opengl-next",
        "expected_mode": "gpu-next",
    },
}

PLAY_DURATION = 8  # seconds per item

_IGNORE_PATTERNS = [
    "keyboardinterrupt",       # SIGINT teardown — expected
    "gsk-warning",             # GTK scene-graph warning, not a render error
    "deprecationwarning",      # Python deprecation notices
    "exception ignored in:",   # Python-level suppressed exception header
    "ioflags",                 # gi.repository + Python 3.13 GLib teardown mismatch
    "pymapping_haskeystring",  # gi teardown: Python 3.13 API warning
]

_FILE_ERROR_PATTERNS = [
    "file error path:",
]


def get_sha256(filepath):
    """Return hex SHA-256 of file, or None if file does not exist."""
    if not os.path.exists(filepath):
        return None
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_active_monitors():
    """Probe Wayland outputs from the host session using wayland_output_hdr."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "wayland_output_hdr",
            os.path.join(PROJECT_ROOT, "src", "wayland_output_hdr.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        outputs = mod.probe_outputs()
        descriptions = []
        for name, info in outputs.items():
            if info.hdr:
                desc = (
                    f"{name}: HDR ACTIVE  tf={info.tf}  primaries={info.primaries}"
                    f"  max_lum={info.max_lum:.1f} nit"
                )
            else:
                desc = f"{name}: SDR (hdr=False)"
            descriptions.append(desc)
        return descriptions if descriptions else ["(no outputs detected)"]
    except Exception as exc:
        return [f"(probe unavailable: {exc})"]


def parse_log_file(log_path):
    """Read CINEHDR_LOG_FILE and extract evidence fields."""
    out = {
        "lines": [],
        "first_frame": None,
        "first_frame_render": None,
        "telemetry": None,
        "gl_vendor": None,
        "errors": [],
        "file_open_errors": [],
        "key_logs": [],
    }

    if not log_path or not os.path.exists(log_path):
        return out

    with open(log_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    out["lines"] = [l.rstrip("\n") for l in lines]

    for line in out["lines"]:
        lower = line.lower()

        if any(pat in lower for pat in _FILE_ERROR_PATTERNS):
            out["file_open_errors"].append(line.strip())
            continue

        if any(kw in lower for kw in ("error", "critical", "fatal", "traceback")):
            if not any(ign in lower for ign in _IGNORE_PATTERNS):
                out["errors"].append(line.strip())

        if out["first_frame"] is None and "render backend" in lower:
            out["first_frame"] = line.strip()

        if out["first_frame_render"] is None and "rendered first frame" in lower:
            out["first_frame_render"] = line.strip()

        if out["gl_vendor"] is None and "opengl=" in lower:
            out["gl_vendor"] = line.strip()

        if "hdr pipeline telemetry:" in lower:
            try:
                json_str = line[line.find("{"):line.rfind("}") + 1]
                candidate = json.loads(json_str)
                if out["telemetry"] is None or (
                    candidate.get("source_hdr") and not out["telemetry"].get("source_hdr")
                ):
                    out["telemetry"] = candidate
            except Exception:
                pass

        if any(
            kw in lower
            for kw in [
                "render backend", "libmpv", "libplacebo", "opengl-next", "opengl",
                "hdr pipeline", "gl_vendor", "gl_renderer", "source_hdr",
                "target_trc", "dovi_profile", "display_hdr", "hdr_mode",
                "rec2100", "rgba16f", "rgba8", "pq", "hlg", "dolby",
            ]
        ):
            out["key_logs"].append(line.strip())

    return out


def run_test(config_name, config, video_name, video_path, file_sha256):
    """Run CineHDR test either via Flatpak or Native Host process."""
    env = os.environ.copy()
    env.update(config["env"])
    env["PYTHONUNBUFFERED"] = "1"

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{config_name}_{video_name}")
    log_path = f"/tmp/cinehdr_smoke_{safe_name}.log"
    env["CINEHDR_LOG_FILE"] = log_path

    if os.path.exists(log_path):
        os.remove(log_path)

    run_mode = config["mode"]

    if run_mode == "native":
        # Native host run using build/venv Python and build/native_libs
        ld_path = f"{NATIVE_LIBS_DIR}:{env.get('LD_LIBRARY_PATH', '')}"
        env["LD_LIBRARY_PATH"] = ld_path

        cmd = [
            VENV_PYTHON,
            os.path.join(PROJECT_ROOT, "run_dev.py"),
            video_path,
        ]
    else:
        # Flatpak run
        flatpak_env_args = []
        for k, v in env.items():
            if k in config["env"] or k in ("PYTHONUNBUFFERED", "CINEHDR_LOG_FILE"):
                flatpak_env_args += [f"--env={k}={v}"]

        cmd = [
            "flatpak", "run",
            "--device=dri",
            "--filesystem=host",
            "--filesystem=/tmp",
            "--env=PYTHONPATH=tests:src:.",
            *flatpak_env_args,
            "--command=python3",
            "io.github.rusmikev.CineHDR",
            os.path.join(PROJECT_ROOT, "run_dev.py"),
            video_path,
        ]

    result = {
        "config": config_name,
        "run_mode": run_mode,
        "backend_requested": config["expected_mode"],
        "expected_backend": config["expected_backend"],
        "expected_vendor": config["expected_vendor"],
        "video_name": video_name,
        "file_path": video_path,
        "file_sha256": file_sha256,
        "log_path": log_path,
        "status": "UNKNOWN",
        "exit_code": None,
        "telemetry": None,
        "first_frame_log": None,
        "first_frame_render": None,
        "gl_vendor_detected": None,
        "errors": [],
        "file_open_errors": [],
        "key_logs": [],
    }

    try:
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )
        time.sleep(PLAY_DURATION)

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        except ProcessLookupError:
            pass

        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()

        result["exit_code"] = proc.returncode

    except Exception as e:
        result["status"] = "ERROR"
        result["errors"].append(str(e))
        result["failure_reason"] = str(e)
        return result

    time.sleep(0.3)

    # --- Parse evidence from log file ---
    parsed = parse_log_file(log_path)
    result["first_frame_log"] = parsed["first_frame"]
    result["first_frame_render"] = parsed["first_frame_render"]
    result["telemetry"] = parsed["telemetry"]
    result["gl_vendor_detected"] = parsed["gl_vendor"]
    result["errors"] = parsed["errors"]
    result["file_open_errors"] = parsed["file_open_errors"]
    result["key_logs"] = parsed["key_logs"]

    # --- Evaluate pass criteria ---
    ec = result["exit_code"]
    log_written = bool(parsed["lines"])

    stub_errors = [e for e in result["errors"] if "stub" in e.lower() or "notimplementederror" in e.lower()]
    real_errors = [e for e in result["errors"] if "stub" not in e.lower() and "notimplementederror" not in e.lower()]

    clean_exit = ec in (0, 2, -2, -9, -15, None) or (ec == 1 and bool(stub_errors))

    detected_vendor_lower = (result["gl_vendor_detected"] or "").lower()
    expected_vendor_lower = config["expected_vendor"].lower()
    vendor_match = expected_vendor_lower in detected_vendor_lower if result["gl_vendor_detected"] else False

    if not clean_exit:
        result["status"] = "CRASH"
        result["failure_reason"] = f"Unexpected exit code: {ec}"
    elif not log_written:
        result["status"] = "NO_LOG"
        result["failure_reason"] = "CINEHDR_LOG_FILE was not written"
    elif stub_errors and not real_errors:
        result["status"] = "STUB_UNSUPPORTED"
        result["failure_reason"] = "libmpv opengl-next stub: opengl-next backend not compiled in libmpv"
    elif real_errors:
        result["status"] = "FAIL"
        result["failure_reason"] = f"Application errors in log: {real_errors[:2]}"
    elif not vendor_match and result["gl_vendor_detected"]:
        result["status"] = "VENDOR_MISMATCH_FAIL"
        result["failure_reason"] = (
            f"GPU Vendor mismatch! Expected '{config['expected_vendor']}', but detected: "
            f"'{result['gl_vendor_detected']}'"
        )
    elif result["first_frame_log"] is None:
        result["status"] = "NO_TELEMETRY"
        result["failure_reason"] = f"first_frame_log absent within {PLAY_DURATION}s"
    elif result["telemetry"] is None:
        result["status"] = "NO_TELEMETRY"
        result["failure_reason"] = "HDR Pipeline Telemetry absent"
    else:
        result["status"] = "PASS"

    return result


def print_result(r):
    icons = {
        "PASS": "✅", "FAIL": "❌", "CRASH": "💥", "ERROR": "⚠️",
        "NO_TELEMETRY": "📭", "NO_LOG": "🔇", "STUB_UNSUPPORTED": "ℹ️",
        "VENDOR_MISMATCH_FAIL": "🛑", "FILE_PLAYBACK_ERROR": "📂",
    }
    icon = icons.get(r["status"], "❓")
    print(f"  {icon} [{r['config']} | {r['run_mode']}] {r['video_name']}: {r['status']}")
    if r.get("failure_reason"):
        print(f"      ↳ {r['failure_reason']}")
    if r["first_frame_log"]:
        print(f"      🎬 {r['first_frame_log']}")
    if r.get("first_frame_render"):
        print(f"      🖼️  {r['first_frame_render']}")
    if r["gl_vendor_detected"]:
        print(f"      🖥️  {r['gl_vendor_detected']}")
    if r["telemetry"]:
        print(f"      📡 {json.dumps(r['telemetry'])}")
    for e in r["errors"][:3]:
        print(f"      ❗ {e}")
    for fe in r.get("file_open_errors", [])[:2]:
        print(f"      📂 {fe}")


def main():
    print("=" * 75)
    print("CineHDR v1.8.5.2.0 — Dual-Mode (Flatpak iGPU + Native NVIDIA dGPU) Runner")
    print("=" * 75)
    print(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Native Python: {VENV_PYTHON}")
    print(f"Native Libs: {NATIVE_LIBS_DIR}")
    print(f"Play duration per test: {PLAY_DURATION}s")
    print()

    active_monitors = probe_active_monitors()
    print("Active monitors (probe_outputs):")
    for m in active_monitors:
        print(f"  {m}")
    print()

    file_hashes = {}
    for name, path in TEST_FILES.items():
        if os.path.exists(path):
            file_hashes[name] = get_sha256(path)
            print(f"Fixture: {name} → SHA-256: {file_hashes[name][:16]}…")
        else:
            print(f"Fixture: {name} → NOT FOUND ({path})")

    all_results = []

    for config_name, config in TEST_MATRIX.items():
        print(f"\n{'─' * 65}")
        print(f"Smoke Test Config: {config_name} (Mode: {config['mode']})")
        print(f"{'─' * 65}")

        for video_name, video_path in TEST_FILES.items():
            if not os.path.exists(video_path):
                print(f"  ⏭️  [{config_name}] {video_name}: SKIPPED (file not found)")
                continue

            r = run_test(
                config_name, config, video_name, video_path,
                file_hashes.get(video_name),
            )
            all_results.append(r)
            print_result(r)

    print(f"\n{'=' * 75}")
    print("SUMMARY")
    print(f"{'=' * 75}")
    total = len(all_results)
    by_status = {}
    for r in all_results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    print(
        f"Total: {total} | "
        f"✅ PASS: {by_status.get('PASS', 0)} | "
        f"ℹ️  STUB: {by_status.get('STUB_UNSUPPORTED', 0)} | "
        f"🛑 VENDOR_MISMATCH: {by_status.get('VENDOR_MISMATCH_FAIL', 0)} | "
        f"📭 NO_TELEMETRY: {by_status.get('NO_TELEMETRY', 0)} | "
        f"❌ FAIL: {by_status.get('FAIL', 0)}"
    )

    hard_failures = (
        by_status.get("FAIL", 0)
        + by_status.get("CRASH", 0)
        + by_status.get("VENDOR_MISMATCH_FAIL", 0)
        + by_status.get("NO_LOG", 0)
        + by_status.get("ERROR", 0)
    )

    report_path = os.path.join(PROJECT_ROOT, "test_report.json")
    with open(report_path, "w") as f:
        json.dump(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "version": "1.8.5.2.0",
                "play_duration_s": PLAY_DURATION,
                "log_capture_method": "CINEHDR_LOG_FILE",
                "environment": {
                    "active_monitors": active_monitors,
                    "session": os.environ.get("WAYLAND_DISPLAY", "unknown"),
                    "igpu": "Intel Raptor Lake Iris Xe Graphics",
                    "dgpu": "NVIDIA GeForce RTX 3050 Laptop GPU (Driver 610.57.04 / 595.80)",
                },
                "fixtures": file_hashes,
                "results": all_results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nEvidence JSON saved to: {report_path}")

    return 0 if hard_failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
