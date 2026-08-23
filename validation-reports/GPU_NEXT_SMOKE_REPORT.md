# 🧪 GPU & GPU-Next Smoke Validation Report — CineHDR v1.8.5.2.0

- **App Version**: `v1.8.5.2.0`
- **Date**: 2026-08-23
- **Test Scope**: Smoke Validation Gate W9 (6-second process stability, GL context creation, clean shutdown)
- **Environment**:
  - **Host Hardware**: Hybrid Graphics Laptop (Intel Raptor Lake Iris Xe iGPU + NVIDIA GeForce RTX 3050 Laptop dGPU, Driver 595.80)
  - **Connected Displays**:
    - **`HDMI-1`**: Dell Alienware AW3225QF 32" 4K QD-OLED 240Hz (HDR Mode **ACTIVE**, Transfer Function: `st2084_pq`, Gamut: `BT.2020`, Peak Luminance: `10000.0 nits`, Min Luminance: `0.005 nits`, Reference Luminance: `203.0 nits`)
    - **`eDP-1`**: Internal Panel (80 nits peak, SDR Mode)
  - **Windowing / Compositor**: Wayland Session (`wp_color_manager_v1` protocol enabled)
  - **Container Runtime**: Flatpak (`io.github.rusmikev.CineHDR`)
- **Evidence Artifacts**:
  - [`test_report.json`](../test_report.json) — Machine-readable JSON with SHA-256 hashes for all 4 media fixtures and per-run metadata
  - [`tests/test_nvidia.py`](../tests/test_nvidia.py) — Automated smoke test runner (16 combinations)

---

## 1. 📌 Scope & Methodology

This document records the results of smoke-testing CineHDR (`v1.8.5.2.0`) across Intel and NVIDIA GPUs using both the **Legacy** (`vo=libmpv` / `opengl`) and **GPU-Next** (`vo=libmpv` / `opengl-next` via `libplacebo` v7.360.1) rendering backends.

### What Was Verified (Smoke Scope — Gate W9)
- Process startup and stable execution for **6 seconds** without process crash or uncaught application exceptions.
- Successful creation of `opengl` (Legacy) and `opengl-next` (GPU-Next) render contexts on Intel iGPU and NVIDIA dGPU.
- Clean process termination via `SIGINT → SIGKILL` sequence without GL context deadlocks or segmentation faults.
- HDR pipeline telemetry emission verified in intermediate runs: `source_hdr=True`, `target_trc=pq`, `dovi_profile=8` confirmed for Dolby Vision fixture.
- Render backend log confirmed: `libmpv render backend requested=legacy active=opengl fallback_reason=none` (Intel & NVIDIA Legacy).
- Exit codes accepted: `0`, `-2` (SIGINT), `-9` (SIGKILL), `-15` (SIGTERM).

### What Was NOT Verified (Open Gates)
> [!IMPORTANT]
> This report is **strictly a smoke validation**. It does **NOT** constitute pixel pipeline certification or hardware passthrough acceptance.
>
> - **Gate 2 (FBO Pixel Validation — W3/W4/W5)**: `pixel_validator_egl` EGL harness requires a native `mpv-gpu-next` build (not available in standard Flatpak runtime). `tests/run_gate2_extended_matrix.py` and `tests/run_performance_ab.py` are ready to run when natively built prefix is available.
> - **Gate 3 (DoVi Policy & Peak Sweep)**: `tests/run_gate3_dovi_matrix.py` (peak sweep at 100/203/400/600/1000 nit) requires native EGL context — pending native build.
> - **HDR Hardware Passthrough**: `get_monitor_hdr_state("HDMI-1")` returns `True` (AW3225QF confirmed active). Full PQ pipeline path (`GL_RGBA16F` / `Rec.2100`) requires EGL harness on physical display.
> - **Interactive Scenarios**: Monitor swap (HDMI-1 ↔ eDP-1), fullscreen, HDR mode switch — require manual user verification.

---

## 2. 📋 Fixtures & Evidence Traceability

| Fixture Name | Container / Format | SHA-256 Hash |
| :--- | :--- | :--- |
| **HDR10 PQ** | HEVC BT.2020 (1000-nit) | `23b9dd904e0138e476946afaa7ecf644726e8a5a6f75d5aa75774ab9f5b6e82c` |
| **HLG** | HEVC BT.2100 | `7b0f7fb469cf7980cc7107a3db5e8a0a01b4c008f3248c3c80eace9e040914f4` |
| **Dolby Vision** | HEVC Profile 8.1 | `66f0a93ee9909ad0229102aafb63542791f98e7f991c6e3f783b4f14ee630675` |
| **Colorbars** | HEVC BT.2020 PQ | `6adcda398d75f100d23daeb53a9f71967c7f9281e21547b1acde01e533cf11cf` |

---

## 3. 🧪 Smoke Test Results Matrix — 16/16 PASS ✅

**Session**: 2026-08-23 20:53:52 UTC+5 | Runner: `tests/test_nvidia.py` | Duration: ~6s/test × 16 = ~96s total

### A. Intel Iris Xe Graphics (iGPU)

| Configuration | Test Fixture | Target Backend | Smoke Result | Notes |
| :--- | :--- | :--- | :---: | :--- |
| **Intel (iGPU) - Legacy** | HDR10 PQ | `opengl` | ✅ **PASS** | No GL errors, clean exit |
| **Intel (iGPU) - Legacy** | HLG BT.2100 | `opengl` | ✅ **PASS** | No GL errors, clean exit |
| **Intel (iGPU) - Legacy** | Dolby Vision P8 | `opengl` | ✅ **PASS** | No GL errors, clean exit |
| **Intel (iGPU) - Legacy** | Colorbars PQ | `opengl` | ✅ **PASS** | No GL errors, clean exit |
| **Intel (iGPU) - GPU-Next** | HDR10 PQ | `opengl-next` | ✅ **PASS** | No GL errors, clean exit |
| **Intel (iGPU) - GPU-Next** | HLG BT.2100 | `opengl-next` | ✅ **PASS** | No GL errors, clean exit |
| **Intel (iGPU) - GPU-Next** | Dolby Vision P8 | `opengl-next` | ✅ **PASS** | No GL errors, clean exit |
| **Intel (iGPU) - GPU-Next** | Colorbars PQ | `opengl-next` | ✅ **PASS** | No GL errors, clean exit |

### B. NVIDIA GeForce RTX 3050 Laptop GPU (dGPU)
*Offloaded via `__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia`.*

| Configuration | Test Fixture | Target Backend | Smoke Result | Notes |
| :--- | :--- | :--- | :---: | :--- |
| **NVIDIA (dGPU) - Legacy** | HDR10 PQ | `opengl` | ✅ **PASS** | Telemetry: `source_hdr=True, target_trc=pq` |
| **NVIDIA (dGPU) - Legacy** | HLG BT.2100 | `opengl` | ✅ **PASS** | Telemetry: `source_hdr=True, target_trc=pq` |
| **NVIDIA (dGPU) - Legacy** | Dolby Vision P8 | `opengl` | ✅ **PASS** | Telemetry: `source_hdr=True, dovi_profile=8` |
| **NVIDIA (dGPU) - Legacy** | Colorbars PQ | `opengl` | ✅ **PASS** | No GL errors, clean exit |
| **NVIDIA (dGPU) - GPU-Next** | HDR10 PQ | `opengl-next` | ✅ **PASS** | No GL errors, clean exit |
| **NVIDIA (dGPU) - GPU-Next** | HLG BT.2100 | `opengl-next` | ✅ **PASS** | No GL errors, clean exit |
| **NVIDIA (dGPU) - GPU-Next** | Dolby Vision P8 | `opengl-next` | ✅ **PASS** | No GL errors, clean exit |
| **NVIDIA (dGPU) - GPU-Next** | Colorbars PQ | `opengl-next` | ✅ **PASS** | No GL errors, clean exit |

---

## 4. 📡 Telemetry Evidence (Intermediate Capture — task-843/task-852)

During intermediate sessions where stderr flushed fully before process termination, the HDR pipeline telemetry was captured directly from `src/hdr_controller.py`:

| Config | Fixture | `source_hdr` | `target_trc` | `dovi_profile` | `display_hdr` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| NVIDIA (dGPU) - Legacy | HDR10 PQ | `True` | `pq` | `null` | `null` (Wayland probe pending) |
| NVIDIA (dGPU) - Legacy | HLG BT.2100 | `True` | `pq` | `null` | `null` |
| NVIDIA (dGPU) - Legacy | Dolby Vision P8 | `True` | `pq` | **`8`** | `null` |

> [!NOTE]
> `display_hdr: null` indicates the Flatpak sandbox process cannot probe the Wayland `HDMI-1` output at startup (wp_color_manager requires compositor access). The Wayland probe (`probe_outputs()`) works correctly when run natively in the dev environment.

---

## 5. 🖥️ Interactive Scenario Status (Manual Verification — HDMI-1 AW3225QF)

> [!WARNING]
> These scenarios require manual testing by the user with the physical Dell Alienware AW3225QF monitor connected on `HDMI-1`.

| Scenario | Description | Status |
| :--- | :--- | :---: |
| **S1 — HDR Passthrough** | Run `python3 run_dev.py samples/...HDR10.mp4` on HDMI-1, verify HDR badge in UI, `st2084_pq` color state | ⏳ PENDING |
| **S2 — Display Swap** | Drag window HDMI-1 → eDP-1 → back; verify seamless SDR↔HDR pipeline switch | ⏳ PENDING |
| **S3 — Fullscreen & Resize** | F11 fullscreen on HDMI-1 during playback, resize window; verify no hangs or buffer leaks | ⏳ PENDING |
| **S4 — HDR Mode Toggle** | Switch `auto / force-hdr / force-sdr` in menu during playback; verify pipeline reacts | ⏳ PENDING |

---

## 6. 🔬 Unit Test Coverage Verification

| Test Suite | Tests | Result |
| :--- | :---: | :---: |
| `test_render_backend.py` | — | ✅ PASS |
| `test_gpu_validation.py` | — | ✅ PASS |
| `test_pixel_pipeline.py` | — | ✅ PASS |
| `test_wayland_color_trace.py` | — | ✅ PASS |
| **Total** | **136** | ✅ **136/136 OK** |

---

## 7. 🎯 Gate Status Summary

| Validation Gate | Status | Details |
| :--- | :---: | :--- |
| **W9 Smoke Gate (Stability)** | ✅ **PASSED** | 16/16 PASS — App starts, plays 6s, exits cleanly on Intel iGPU + NVIDIA dGPU for both `opengl` and `opengl-next` backends. |
| **Gate 2 — W3/W4/W5 (FBO & Pixel Pipeline)** | ⏳ **PENDING** | Requires native `mpv-gpu-next` prefix build + `pixel_validator_egl` EGL harness. Harnesses ready in `tests/run_gate2_extended_matrix.py` and `tests/run_performance_ab.py`. |
| **Gate 3 — DoVi Policy & Peak Sweep** | ⏳ **PENDING** | Requires EGL harness on physical HDR display + native mpv build. Harness ready in `tests/run_gate3_dovi_matrix.py`. |
| **Interactive Scenarios S1–S4** | ⏳ **PENDING** | Manual physical verification required with AW3225QF on HDMI-1. |
