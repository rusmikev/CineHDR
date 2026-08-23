# 🧪 GPU & GPU-Next Smoke Validation Report — CineHDR v1.8.5.2.0

- **App Version**: `v1.8.5.2.0`
- **Test Date**: 2026-08-23
- **Test Scope**: Smoke Validation Gate W9 — log-file evidence, telemetry mandatory for PASS
- **Log Capture Method**: `CINEHDR_LOG_FILE=/tmp/cinehdr_smoke_*.log` via `--filesystem=/tmp` (immune to Flatpak fd-forwarding)
- **Runner**: [`tests/test_nvidia.py`](../tests/test_nvidia.py)
- **Evidence JSON**: [`test_report.json`](../test_report.json)

---

## 1. 📌 Environment

| Component | Value |
| :--- | :--- |
| **Host OS** | Fedora 44, Wayland session |
| **iGPU** | Intel Raptor Lake Iris Xe (Mesa 26.1.6, `git-ffa422e53d`) |
| **dGPU** | NVIDIA GeForce RTX 3050 Laptop (Driver 595.80) |
| **libmpv** | `/app/lib/libmpv.so.2.5.0` (mpv v0.41.0) — standard Flatpak build |
| **libplacebo** | v7.360.1 (bundled in Flatpak runtime) |
| **FFmpeg** | 7.1.3 |
| **Display HDMI-1** | Dell Alienware AW3225QF 32" 4K QD-OLED 240Hz — **HDR ACTIVE** (`st2084_pq`, BT.2020, 10000 nit peak) — confirmed via `probe_outputs()` |
| **Display eDP-1** | Internal panel, SDR mode |

---

## 2. 📋 Fixtures & SHA-256 Evidence

| Fixture | Format | SHA-256 |
| :--- | :--- | :--- |
| **HDR10 PQ** | HEVC BT.2020 1000-nit | `23b9dd904e0138e476946afaa7ecf644726e8a5a6f75d5aa75774ab9f5b6e82c` |
| **HLG** | HEVC BT.2100 | `7b0f7fb469cf7980cc7107a3db5e8a0a01b4c008f3248c3c80eace9e040914f4` |
| **Dolby Vision** | HEVC Profile 8.1 | `66f0a93ee9909ad0229102aafb63542791f98e7f991c6e3f783b4f14ee630675` |
| **Colorbars** | HEVC BT.2020 PQ | `6adcda398d75f100d23daeb53a9f71967c7f9281e21547b1acde01e533cf11cf` |

---

## 3. 🧪 Smoke Test Matrix — 8/8 PASS (Legacy) + 8/8 STUB (GPU-Next)

**Session timestamp**: 2026-08-23 21:22–21:24 UTC+5 | Play duration: 8s/test | Total: 16 tests

### A. Intel Iris Xe (iGPU) — Legacy Backend

| Fixture | Backend | `internal_format` | `source_hdr` | `target_trc` | `dovi_profile` | `display_hdr` | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| HDR10 PQ (HEVC BT.2020) | `opengl` | `0x881a` (GL_RGBA16F) | `true` | `pq` | `null` | `null` ¹ | ✅ **PASS** |
| HLG (HEVC BT.2100) | `opengl` | `0x881a` (GL_RGBA16F) | `true` | `pq` | `null` | `null` ¹ | ✅ **PASS** |
| Dolby Vision (HEVC P8) | `opengl` | `0x881a` (GL_RGBA16F) | `true` | `pq` | **`8`** | `null` ¹ | ✅ **PASS** |
| HDR10 PQ (colorbars) | `opengl` | `0x881a` (GL_RGBA16F) | `true` | `pq` | `null` | `null` ¹ | ✅ **PASS** |

> ¹ `display_hdr: null` in the post-render telemetry because `apply_hdr()` calls subsequent to the first use `allow_probe=False`. The first telemetry call (pre-render startup) correctly returns `display_hdr: true` confirming AW3225QF HDR detection via `probe_outputs()`.

### B. Intel Iris Xe (iGPU) — GPU-Next Backend

| Fixture | Backend Requested | Outcome | Reason |
| :--- | :---: | :---: | :--- |
| HDR10 PQ, HLG, DoVi, Colorbars | `opengl-next` | ℹ️ **STUB** | Flatpak mpv v0.41.0 does not include the `opengl-next` context implementation — `NotImplementedError: The API function which was called is a stub only` |

### C. NVIDIA RTX 3050 Laptop (dGPU) — Legacy Backend

> [!NOTE]
> `__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia` env vars are set, but the Flatpak sandbox does not forward PRIME DRM node access. All NVIDIA-config tests render on the Intel iGPU (`Mesa Intel(R) Iris(R) Xe Graphics (RPL-P)`). This is a **Flatpak PRIME limitation**, not a CineHDR bug.

| Fixture | Effective GPU | `internal_format` | `source_hdr` | `target_trc` | `dovi_profile` | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| HDR10 PQ (HEVC BT.2020) | Intel (PRIME blocked) | `0x881a` (GL_RGBA16F) | `true` | `pq` | `null` | ✅ **PASS** |
| HLG (HEVC BT.2100) | Intel (PRIME blocked) | `0x881a` (GL_RGBA16F) | `true` | `pq` | `null` | ✅ **PASS** |
| Dolby Vision (HEVC P8) | Intel (PRIME blocked) | `0x881a` (GL_RGBA16F) | `true` | `pq` | **`8`** | ✅ **PASS** |
| HDR10 PQ (colorbars) | Intel (PRIME blocked) | `0x881a` (GL_RGBA16F) | `true` | `pq` | `null` | ✅ **PASS** |

### D. NVIDIA RTX 3050 (dGPU) — GPU-Next Backend

Same STUB outcome as Intel GPU-Next (shared Flatpak mpv binary).

---

## 4. 🔑 Key Evidence Lines (per run)

Sample from `Intel (iGPU) - Legacy / Dolby Vision (HEVC)`:

```
libmpv render backend requested=legacy active=opengl fallback_reason=none
Render runtime libmpv=/app/lib/libmpv.so.2.5.0 mpv=mpv v0.41.0 libplacebo=v7.360.1 ffmpeg=7.1.3
  OpenGL=Intel / Mesa Intel(R) Iris(R) Xe Graphics (RPL-P) / OpenGL ES 3.2 Mesa 26.1.6
Rendered first frame backend=opengl target=2560x1072 internal_format=0x881a depth=16
HDR Pipeline Telemetry: {"source_hdr": true, "target_trc": "pq", "target_peak": "auto",
  "tone_mapping_active": false, "display_hdr": null, "hdr_mode": "auto", "dovi_profile": 8}
```

`internal_format=0x881a` = **`GL_RGBA16F`** — 16-bit floating-point HDR texture confirmed in Flatpak sandbox with AW3225QF connected on HDMI-1.

---

## 5. ⚠️ Known Limitations (Not CineHDR Bugs)

| Issue | Root Cause | Impact |
| :--- | :--- | :--- |
| **gpu-next STUB_UNSUPPORTED** | Standard Flatpak mpv v0.41.0 has `opengl-next` as a stub only — not the patched gpu-next build | Cannot test `opengl-next` backend path in Flatpak. Requires a native `mpv-gpu-next` build or updated Flatpak manifest with patched mpv. |
| **NVIDIA PRIME not forwarded** | Flatpak sandbox does not expose `/dev/dri/card1` (NVIDIA DRM node) by default | All dGPU configs render on Intel iGPU. True NVIDIA testing requires either: native run, `--device=all`, or a system-level GPU offload profile. |
| **`display_hdr: null` in post-render telemetry** | `apply_hdr()` uses `allow_probe=False` after the first call to avoid expensive Wayland probes on every frame | First startup telemetry correctly returns `display_hdr: true`. Not a pipeline bug. |

---

## 6. 🎯 Gate Status Summary

| Gate | Status | Evidence |
| :--- | :---: | :--- |
| **W9 Smoke Gate (Stability + Telemetry)** | ✅ **PASSED** | 8/8 Legacy configs PASS with `GL_RGBA16F`, `source_hdr=true`, HDR Pipeline Telemetry. |
| **gpu-next Backend Validation** | ⛔ **BLOCKED** | Flatpak mpv does not contain the patched `opengl-next` implementation. All gpu-next configs = `STUB_UNSUPPORTED`. Requires updated Flatpak manifest or native build. |
| **NVIDIA dGPU (native PRIME)** | ⏳ **PENDING** | Flatpak PRIME forwarding unavailable. Native dev run or `--device=all` required for true NVIDIA dGPU testing. |
| **Gate 2 — FBO Pixel Pipeline** | ⏳ **PENDING** | Requires native `mpv-gpu-next` prefix + `pixel_validator_egl` EGL harness. |
| **Gate 3 — DoVi Policy & Peak Sweep** | ⏳ **PENDING** | Requires EGL harness on physical HDR display + native mpv build. |
| **Interactive Scenarios S1–S4** | ⏳ **PENDING** | Manual verification required with AW3225QF on HDMI-1. |
