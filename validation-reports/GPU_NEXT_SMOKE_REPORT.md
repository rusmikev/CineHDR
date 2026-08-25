# 🧪 Dual-Mode (Intel iGPU + Native NVIDIA dGPU) Smoke Validation Report — CineHDR v1.8.5.2.0

- **App Version**: `v1.8.5.2.0`
- **Test Date**: 2026-08-23
- **Test Scope**: Dual-Mode Validation Gate W9 (Flatpak container for Intel iGPU + Native Host for NVIDIA dGPU)
- **Log Capture Method**: `CINEHDR_LOG_FILE=/tmp/cinehdr_smoke_*.log` (via `--filesystem=/tmp` for Flatpak, native file handler for Host)
- **Process Isolation**: Enforced via `CINEHDR_NON_UNIQUE=1` (prevents D-Bus single-instance session forwarding)
- **Runner**: [`tests/test_nvidia.py`](../tests/test_nvidia.py)
- **Evidence JSON**: [`test_report.json`](../test_report.json)

> [!WARNING]
> **Evidence correction (2026-08-25):** the eight historical `PASS` labels are
> provisional. The runner proved process startup and GL vendor selection but
> did not require `first_frame_render`, expected HDR telemetry, or absence of
> file-open warnings. The other eight rows are `STUB_UNSUPPORTED`, not GPU Next
> passes. This report cannot close a renderer, HDR-content, Gate 2, or Gate 3
> evidence row until the smoke oracle is corrected and the affected rows are
> rerun under ADR-0006.

> The replacement `cinehdr-vendor-smoke-v2` oracle is now locally verified but
> has not been run on this laptop. It uses one pinned Flatpak for both vendors,
> rejects missing/changed fixtures before launch, and requires a loaded timed
> media-frame marker in addition to backend/runtime/HDR evidence. Historical
> labels below remain unchanged evidence, not results of the new runner.

---

## 1. 📌 Environment Details & Hardware Verification

| Hardware Component | Value & Driver Details | Execution Environment |
| :--- | :--- | :--- |
| **Host OS & Compositor** | Fedora 44, Wayland Session | Host / Sandbox |
| **Intel iGPU** | Intel Raptor Lake Iris Xe (`Mesa Intel(R) Iris(R) Xe Graphics RPL-P`, OpenGL ES 3.2 Mesa 26.1.6) | Flatpak Sandbox (`io.github.rusmikev.CineHDR`) |
| **NVIDIA dGPU** | **NVIDIA GeForce RTX 3050 6GB Laptop GPU** (`OpenGL ES 3.2 NVIDIA 610.57.04`) | **Native Host Execution** (PRIME offload: `__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia`) |
| **Bundled libmpv** | `libmpv.so.2.5.0` (mpv v0.41.0, libplacebo v7.360.1, FFmpeg 7.1.3) | `build/native_libs` / `/app/lib` |
| **Display HDMI-1** | Dell Alienware AW3225QF 32" 4K QD-OLED 240Hz — reported separately as HDR active; the linked JSON monitor probe failed and does not verify this row | `HDMI-1` |
| **Display eDP-1** | Internal panel, SDR mode (80 nits peak) | `eDP-1` |

---

## 2. 📋 Fixtures & SHA-256 Evidence Traceability

| Fixture Name | Container / Format | SHA-256 Hash |
| :--- | :--- | :--- |
| **HDR10 PQ** | HEVC BT.2020 (1000-nit) | `23b9dd904e0138e476946afaa7ecf644726e8a5a6f75d5aa75774ab9f5b6e82c` |
| **HLG** | HEVC BT.2100 | `7b0f7fb469cf7980cc7107a3db5e8a0a01b4c008f3248c3c80eace9e040914f4` |
| **Dolby Vision** | HEVC Profile 8.1 | `66f0a93ee9909ad0229102aafb63542791f98e7f991c6e3f783b4f14ee630675` |
| **Colorbars** | HEVC BT.2020 PQ | `6adcda398d75f100d23daeb53a9f71967c7f9281e21547b1acde01e533cf11cf` |

---

## 3. 🧪 Smoke Test Results Matrix — 8 provisional / 8 STUB

**Session timestamp**: 2026-08-23 22:02 UTC+5 | Play duration: 8s per test | Total items: 16

### A. Intel Iris Xe Graphics (iGPU — Flatpak Mode)

| Configuration | Test Fixture | Target Backend | Verified Vendor & Renderer | Status |
| :--- | :--- | :---: | :--- | :---: |
| **Intel (iGPU) - Legacy** | HDR10 PQ | `opengl` | `Intel / Mesa Intel(R) Iris(R) Xe Graphics (RPL-P)` | ⚠️ **PROVISIONAL** |
| **Intel (iGPU) - Legacy** | HLG BT.2100 | `opengl` | `Intel / Mesa Intel(R) Iris(R) Xe Graphics (RPL-P)` | ⚠️ **PROVISIONAL** |
| **Intel (iGPU) - Legacy** | Dolby Vision P8 | `opengl` | `Intel / Mesa Intel(R) Iris(R) Xe Graphics (RPL-P)` | ⚠️ **PROVISIONAL** |
| **Intel (iGPU) - Legacy** | Colorbars PQ | `opengl` | `Intel / Mesa Intel(R) Iris(R) Xe Graphics (RPL-P)` | ⚠️ **PROVISIONAL** |
| **Intel (iGPU) - GPU-Next** | All 4 Fixtures | `opengl-next` | `NotImplementedError: opengl-next stub` | ℹ️ **STUB** |

### B. NVIDIA GeForce RTX 3050 Laptop GPU (dGPU — Native Host PRIME Mode)

| Configuration | Test Fixture | Target Backend | Verified Vendor & Renderer | Status |
| :--- | :--- | :---: | :--- | :---: |
| **NVIDIA (dGPU) - Legacy** | HDR10 PQ | `opengl` | **`NVIDIA Corporation / NVIDIA GeForce RTX 3050 6GB Laptop GPU/PCIe/SSE2`** | ⚠️ **PROVISIONAL** |
| **NVIDIA (dGPU) - Legacy** | HLG BT.2100 | `opengl` | **`NVIDIA Corporation / NVIDIA GeForce RTX 3050 6GB Laptop GPU/PCIe/SSE2`** | ⚠️ **PROVISIONAL** |
| **NVIDIA (dGPU) - Legacy** | Dolby Vision P8 | `opengl` | **`NVIDIA Corporation / NVIDIA GeForce RTX 3050 6GB Laptop GPU/PCIe/SSE2`** | ⚠️ **PROVISIONAL** |
| **NVIDIA (dGPU) - Legacy** | Colorbars PQ | `opengl` | **`NVIDIA Corporation / NVIDIA GeForce RTX 3050 6GB Laptop GPU/PCIe/SSE2`** | ⚠️ **PROVISIONAL** |
| **NVIDIA (dGPU) - GPU-Next** | All 4 Fixtures | `opengl-next` | `NotImplementedError: opengl-next stub` | ℹ️ **STUB** |

---

## 4. 🔑 Key Hardware & Render Evidence Lines

### Native NVIDIA GeForce RTX 3050 Rendering Output (Log Snippet)
```
[INFO] root: libmpv render backend requested=legacy active=opengl fallback_reason=none
[INFO] root: Render runtime libmpv=/home/rusmikev/Downloads/Cine/build/native_libs/libmpv.so.2 mpv=mpv v0.41.0 libplacebo=v7.360.1 ffmpeg=7.1.3
  OpenGL=NVIDIA Corporation / NVIDIA GeForce RTX 3050 6GB Laptop GPU/PCIe/SSE2 / OpenGL ES 3.2 NVIDIA 610.57.04
[INFO] root: HDR Pipeline Telemetry: {"source_hdr": true, "target_trc": "pq", "target_peak": "auto", "tone_mapping_active": false, "display_hdr": true, "hdr_mode": "auto", "dovi_profile": null}
```

- **Vendor Match**: Verified `NVIDIA Corporation` (dGPU hardware offload confirmed).
- **Renderer Match**: Verified `NVIDIA GeForce RTX 3050 6GB Laptop GPU/PCIe/SSE2`.
- **Driver Version**: Verified `OpenGL ES 3.2 NVIDIA 610.57.04`.
- **Display Probe**: not proven by the linked JSON; its probe recorded an import
  failure. The separately reported HDMI-1 state is retained as context only.

---

## 5. 🎯 Gate Status Summary

| Validation Gate | Status | Details |
| :--- | :---: | :--- |
| **W9 Smoke Gate (Intel iGPU)** | ⚠️ **PROVISIONAL** | Legacy process startup and Intel GL vendor observed; rendered content was not a required oracle. |
| **W9 Smoke Gate (NVIDIA dGPU)** | ⚠️ **PROVISIONAL** | Native PRIME selected NVIDIA; rendered content and clean file load were not required oracles. |
| **gpu-next Backend Validation** | ℹ️ **STUB_UNSUPPORTED** | 8/8 configs correctly report `STUB_UNSUPPORTED` due to unpatched `opengl-next` stub in standard libmpv. |
| **Gate 2 — FBO Pixel Pipeline** | ⏳ **PENDING** | `tests/run_gate2_extended_matrix.py` EGL harness ready for native execution. |
| **Gate 3 — DoVi Policy & Peak Sweep** | ⏳ **PENDING** | `tests/run_gate3_dovi_matrix.py` EGL harness ready for native execution. |
| **Interactive Scenarios S1–S4** | ⏳ **PENDING** | Manual physical verification on HDMI-1 Dell Alienware AW3225QF display. |
