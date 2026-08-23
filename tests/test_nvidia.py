#!/usr/bin/env python3
"""
GPU Integration & Backend Validation Test for CineHDR v1.8.5.2.0
Tests playback on Intel (integrated) and NVIDIA (discrete) GPUs across
Legacy (vo=libmpv / opengl) and GPU-Next (vo=gpu-next / opengl-cb) render backends.
"""
import subprocess
import time
import os
import sys
import json
import signal

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

TEST_FILES = {
    "HDR10 PQ (HEVC BT.2020)": os.path.join(PROJECT_ROOT, "samples/haasn-hdr-tests/01. Black Clipping_1_HDR10.mp4"),
    "HLG (HEVC BT.2100)": os.path.join(PROJECT_ROOT, "samples/haasn-hdr-tests/Grayscale BT.2100 HLG.mkv"),
    "Dolby Vision (HEVC)": os.path.join(PROJECT_ROOT, "samples/haasn-hdr-tests/quietvoid/02themeg-dovi.mkv"),
    "HDR10 PQ (colorbars)": os.path.join(PROJECT_ROOT, "samples/haasn-hdr-tests/colorbars.mp4"),
}

# GPU + Render Backend Test Combinations
TEST_MATRIX = {
    "Intel (iGPU) - Legacy": {
        "env": {},
        "backend": "legacy"
    },
    "Intel (iGPU) - GPU-Next": {
        "env": {"CINEHDR_RENDER_BACKEND": "gpu-next"},
        "backend": "gpu-next"
    },
    "NVIDIA (dGPU) - Legacy": {
        "env": {
            "__NV_PRIME_RENDER_OFFLOAD": "1",
            "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
        },
        "backend": "legacy"
    },
    "NVIDIA (dGPU) - GPU-Next": {
        "env": {
            "__NV_PRIME_RENDER_OFFLOAD": "1",
            "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
            "CINEHDR_RENDER_BACKEND": "gpu-next",
        },
        "backend": "gpu-next"
    },
}

PLAY_DURATION = 5


def run_test(config_name, config, video_name, video_path):
    """Run CineHDR in Flatpak with specified GPU env, backend and video file."""
    env = os.environ.copy()
    env.update(config["env"])

    cmd = [
        "flatpak", "run",
        "--filesystem=host",
        "--env=PYTHONPATH=tests:src:.",
        "--command=python3",
        "io.github.rusmikev.CineHDR",
        os.path.join(PROJECT_ROOT, "run_dev.py"),
        video_path,
    ]

    result = {
        "config": config_name,
        "backend": config["backend"],
        "video": video_name,
        "file": video_path,
        "status": "UNKNOWN",
        "exit_code": None,
        "errors": [],
        "warnings": [],
        "key_logs": [],
    }

    try:
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            preexec_fn=os.setsid,
        )
        time.sleep(PLAY_DURATION)

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass

        try:
            stdout, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()

        result["exit_code"] = proc.returncode

        lines = stderr.splitlines() if stderr else []
        for line in lines:
            lower = line.lower()
            if any(kw in lower for kw in ["error", "critical", "fatal", "traceback", "exception"]):
                if "gsk-warning" not in lower and "deprecationwarning" not in lower:
                    result["errors"].append(line.strip())
            elif "warning" in lower:
                result["warnings"].append(line.strip())

            if any(kw in lower for kw in [
                "vendor", "offload", "hdr", "colorspace", "target-prim",
                "target-peak", "target-trc", "dolby", "dovi", "pq", "hlg",
                "color_state", "rec2100", "rgba16f", "gl_renderer",
                "wp_color_manager", "monitor", "cinehdr", "version",
                "gpu-next", "render_backend", "vo_gpu_next"
            ]):
                result["key_logs"].append(line.strip())

        if result["errors"]:
            result["status"] = "FAIL"
        elif result["exit_code"] is not None and result["exit_code"] < 0:
            if result["exit_code"] in (-9, -15):
                result["status"] = "PASS"
            else:
                result["status"] = "CRASH"
        else:
            result["status"] = "PASS"

    except Exception as e:
        result["status"] = "ERROR"
        result["errors"].append(str(e))

    return result


def print_result(r):
    status_icon = {"PASS": "✅", "FAIL": "❌", "CRASH": "💥", "ERROR": "⚠️"}.get(
        r["status"], "❓"
    )
    print(f"  {status_icon} [{r['config']}] {r['video']}: {r['status']}")
    if r["errors"]:
        for e in r["errors"][:5]:
            print(f"      ERROR: {e}")
    if r["key_logs"]:
        print(f"      Key logs ({len(r['key_logs'])} lines):")
        for log in r["key_logs"][:10]:
            print(f"        {log}")
        if len(r["key_logs"]) > 10:
            print(f"        ... and {len(r['key_logs']) - 10} more")


def main():
    print("=" * 75)
    print("CineHDR v1.8.5.2.0 — GPU & GPU-Next Integration Test Matrix")
    print("=" * 75)
    print(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Launch: flatpak run --command=python3 ... run_dev.py <file>")
    print(f"Play Duration per test: {PLAY_DURATION}s")
    print()

    all_results = []

    for config_name, config in TEST_MATRIX.items():
        print(f"\n{'─' * 65}")
        print(f"Test Configuration: {config_name}")
        print(f"{'─' * 65}")

        for video_name, video_path in TEST_FILES.items():
            if not os.path.exists(video_path):
                print(f"  ⏭️  [{config_name}] {video_name}: SKIPPED (file not found)")
                continue

            r = run_test(config_name, config, video_name, video_path)
            all_results.append(r)
            print_result(r)

    # Summary
    print(f"\n{'=' * 75}")
    print("SUMMARY")
    print(f"{'=' * 75}")
    total = len(all_results)
    passed = sum(1 for r in all_results if r["status"] == "PASS")
    failed = sum(1 for r in all_results if r["status"] == "FAIL")
    crashed = sum(1 for r in all_results if r["status"] == "CRASH")
    errors = sum(1 for r in all_results if r["status"] == "ERROR")
    print(f"Total: {total} | ✅ Passed: {passed} | ❌ Failed: {failed} | 💥 Crashed: {crashed} | ⚠️ Errors: {errors}")

    report_path = os.path.join(PROJECT_ROOT, "test_report.json")
    with open(report_path, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\nDetailed JSON report saved to: {report_path}")

    return 0 if (failed + crashed + errors) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
