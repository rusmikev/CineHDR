# 🧪 GPU & GPU-Next Smoke Validation Report — CineHDR v1.8.5.2.0

- **App Version**: `v1.8.5.2.0`
- **Date**: 2026-08-23
- **Test Scope**: Smoke Validation (5-second process stability, GL context creation, clean shutdown)
- **Environment**:
  - **Host Hardware**: Hybrid Graphics Laptop (Intel Raptor Lake Iris Xe iGPU + NVIDIA GeForce RTX 3050 Laptop dGPU, Driver 595.80)
  - **Active Display**: `eDP-1` Internal Panel (80 nits peak, SDR Mode, `get_monitor_hdr_state() = False`)
  - **Windowing / Compositor**: Wayland Session
  - **Container Runtime**: Flatpak (`io.github.rusmikev.CineHDR`)
- **Evidence Artifact**: [`test_report.json`](file:///home/rusmikev/Downloads/Cine/test_report.json)

---

## 1. 📌 Scope & Methodology

This document records the results of smoke-testing the CineHDR application (`v1.8.5.2.0`) across Intel and NVIDIA GPUs using both the **Legacy** (`vo=libmpv` / `opengl`) and **GPU-Next** (`vo=libmpv` / `opengl-next` via `libplacebo` v7.360.1) rendering backends.

### What Was Verified (Smoke Scope)
- Process startup and clean execution for 5 seconds without process crash or uncaught exceptions.
- Creation of `opengl` (Legacy) and `opengl-next` (GPU-Next) render contexts on Intel iGPU and NVIDIA dGPU.
- Clean process termination (`SIGKILL` process group cleanup) without memory segmentation faults or GL context deadlocks.
- Execution under Wayland with SDR tone-mapping (`hdr_mode=auto` on `eDP-1` SDR display).

### What Was NOT Verified (Open Gates)
> [!IMPORTANT]
> This report is **strictly a smoke validation**. It does **NOT** constitute pixel pipeline certification or hardware passthrough acceptance.
>
> - **Gate 2 & Gate 3 (Pixel Validation)**: `pixel_validator_egl` and EGL reference harnesses were NOT executed during this 5-second smoke test.
> - **HDR Hardware Passthrough**: Because the test host panel (`eDP-1`) is in SDR mode (`get_monitor_hdr_state() = False`), the player executed its SDR tone-mapping path (`GL_RGBA8` / `sRGB`). True `Rec.2100 PQ` passthrough (`GL_RGBA16F`) requires a physical HDR display.
> - **Compositor Scenarios (TEST_MATRIX.md)**: 1000-nit passthrough, 400-nit roll-off, double tone-mapping check, and tearing resistance remain **OPEN / PENDING**.

---

## 2. 📋 Fixtures & Evidence Traceability

| Fixture Name | Container / Format | SHA-256 Hash |
| :--- | :--- | :--- |
| **HDR10 PQ** | HEVC BT.2020 (1000-nit) | `23b9dd904e0138e476946afaa7ecf644726e8a5a6f75d5aa75774ab9f5b6e82c` |
| **HLG** | HEVC BT.2100 | `7b0f7fb469cf7980cc7107a3db5e8a0a01b4c008f3248c3c80eace9e040914f4` |
| **Dolby Vision** | HEVC Profile 8.1 | `66f0a93ee9909ad0229102aafb63542791f98e7f991c6e3f783b4f14ee630675` |
| **Colorbars** | HEVC BT.2020 PQ | `6adcda398d75f100d23daeb53a9f71967c7f9281e21547b1acde01e533cf11cf` |

---

## 3. 🧪 Smoke Test Results Matrix

### A. Intel Iris Xe Graphics (iGPU)

| Configuration | Test Fixture | Target Backend | Smoke Result | Exit Code |
| :--- | :--- | :--- | :---: | :---: |
| **Intel (iGPU) - Legacy** | HDR10 PQ | `opengl` | ✅ **PASS** | -9 |
| **Intel (iGPU) - Legacy** | HLG BT.2100 | `opengl` | ✅ **PASS** | -9 |
| **Intel (iGPU) - Legacy** | Dolby Vision P8 | `opengl` | ✅ **PASS** | -9 |
| **Intel (iGPU) - Legacy** | Colorbars | `opengl` | ✅ **PASS** | -9 |
| **Intel (iGPU) - GPU-Next** | HDR10 PQ | `opengl-next` | ✅ **PASS** | -9 |
| **Intel (iGPU) - GPU-Next** | HLG BT.2100 | `opengl-next` | ✅ **PASS** | -9 |
| **Intel (iGPU) - GPU-Next** | Dolby Vision P8 | `opengl-next` | ✅ **PASS** | -9 |
| **Intel (iGPU) - GPU-Next** | Colorbars | `opengl-next` | ✅ **PASS** | -9 |

### B. NVIDIA GeForce RTX 3050 Laptop GPU (dGPU)
*Offloaded via `__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia`.*

| Configuration | Test Fixture | Target Backend | Smoke Result | Exit Code |
| :--- | :--- | :--- | :---: | :---: |
| **NVIDIA (dGPU) - Legacy** | HDR10 PQ | `opengl` | ✅ **PASS** | -9 |
| **NVIDIA (dGPU) - Legacy** | HLG BT.2100 | `opengl` | ✅ **PASS** | -9 |
| **NVIDIA (dGPU) - Legacy** | Dolby Vision P8 | `opengl` | ✅ **PASS** | -9 |
| **NVIDIA (dGPU) - Legacy** | Colorbars | `opengl` | ✅ **PASS** | -9 |
| **NVIDIA (dGPU) - GPU-Next** | HDR10 PQ | `opengl-next` | ✅ **PASS** | -9 |
| **NVIDIA (dGPU) - GPU-Next** | HLG BT.2100 | `opengl-next` | ✅ **PASS** | -9 |
| **NVIDIA (dGPU) - GPU-Next** | Dolby Vision P8 | `opengl-next` | ✅ **PASS** | -9 |
| **NVIDIA (dGPU) - GPU-Next** | Colorbars | `opengl-next` | ✅ **PASS** | -9 |

---

## 4. 🔬 Unit Test Coverage Verification

The automated unit test suite was re-verified during this run:
- **Command**: `PYTHONPATH=tests:src:. flatpak run --filesystem=host --env=PYTHONPATH=tests:src:. --command=python3 io.github.rusmikev.CineHDR -m unittest tests/test_render_backend.py tests/test_gpu_validation.py tests/test_pixel_pipeline.py tests/test_wayland_color_trace.py`
- **Result**: `Ran 136 tests in 3.192s — OK` (100% PASS).

---

## 5. 🎯 Gate Status Summary

| Validation Gate | Status | Details |
| :--- | :---: | :--- |
| **W9 Smoke Gate (Stability)** | ✅ **PASSED** | App starts and plays for 5s without crashes on Intel and NVIDIA GPUs for both `opengl` and `opengl-next` backends. |
| **Gate 2 (FBO & Pixel Pipeline)** | ⏳ **PENDING** | Requires running `run_pixel_pipeline.py` / `pixel_validator_egl` harness. |
| **Gate 3 (Dolby Vision & Peak Sweep)** | ⏳ **PENDING** | Requires physical HDR display testing for 1000-nit passthrough and tone mapping validation. |
