# 📊 GPU & GPU-Next Validation Report — CineHDR v1.8.5.2.0

- **App Version**: `v1.8.5.2.0`
- **Date**: 2026-08-23
- **Environment**: Linux / Wayland Session / Hybrid Graphics (Intel Iris Xe + NVIDIA GeForce RTX 3050 Laptop)
- **Container Runtime**: Flatpak (`io.github.rusmikev.CineHDR`)

---

## 1. 📌 Executive Summary

Full integration and hardware rendering validation was conducted for CineHDR release `v1.8.5.2.0`. This version incorporates the new experimental **`gpu-next`** rendering pipeline (powered by `libplacebo` v7.360.1 / `libmpv`), along with Wayland Color Management protocol probes (`wp_color_manager_v1`), per-output monitor HDR state queries, and Dolby Vision Profile 8 metadata mapping.

All **16 integration scenarios** across 2 GPU targets (Intel iGPU & NVIDIA dGPU) and 2 rendering backends (`legacy` and `gpu-next`) completed with **100% PASS**.

---

## 2. 🧪 Test Matrix Results

### A. Intel Iris Xe Graphics (iGPU)

| Scenario | Format | Render Backend | Status | Pipeline Notes |
| :--- | :--- | :--- | :---: | :--- |
| **HDR10 PQ** | HEVC BT.2020 (1000-nit) | Legacy (`vo=libmpv`) | ✅ **PASS** | 64 bpp `Rec.2100 PQ` color state, Direct `Gtk.GraphicsOffload` active |
| **HDR10 PQ** | HEVC BT.2020 (1000-nit) | **GPU-Next** (`vo=gpu-next`) | ✅ **PASS** | `libplacebo` PQ pipeline, 16-bit float FBO, zero frame drops |
| **HLG** | HEVC BT.2100 | Legacy (`vo=libmpv`) | ✅ **PASS** | HLG curve auto-transcoded to PQ target for GTK 4.16 |
| **HLG** | HEVC BT.2100 | **GPU-Next** (`vo=gpu-next`) | ✅ **PASS** | Native HLG inverse OETF & tone-mapping pass via `libplacebo` |
| **Dolby Vision** | HEVC Profile 8.1 | Legacy (`vo=libmpv`) | ✅ **PASS** | HDR10 base-layer fallback, RPU metadata mapped |
| **Dolby Vision** | HEVC Profile 8.1 | **GPU-Next** (`vo=gpu-next`) | ✅ **PASS** | RPU metadata parsed & mapped via libplacebo DoVi engine |
| **Colorbars** | HEVC BT.2020 PQ | Legacy (`vo=libmpv`) | ✅ **PASS** | Clean gamut compliance, no tone-mapping distortion |
| **Colorbars** | HEVC BT.2020 PQ | **GPU-Next** (`vo=gpu-next`) | ✅ **PASS** | Pixel pipeline invariant verified |

### B. NVIDIA GeForce RTX 3050 Laptop GPU (dGPU)
*Executed via PRIME Render Offload (`__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia`).*

| Scenario | Format | Render Backend | Status | Pipeline Notes |
| :--- | :--- | :--- | :---: | :--- |
| **HDR10 PQ** | HEVC BT.2020 (1000-nit) | Legacy (`vo=libmpv`) | ✅ **PASS** | Vendor detected: `nvidia`. `GraphicsOffload` set to `DISABLED` |
| **HDR10 PQ** | HEVC BT.2020 (1000-nit) | **GPU-Next** (`vo=gpu-next`) | ✅ **PASS** | Clean Wayland compositing via GTK GL area without cursor flicker |
| **HLG** | HEVC BT.2100 | Legacy (`vo=libmpv`) | ✅ **PASS** | Smooth playback, no GL context lockups |
| **HLG** | HEVC BT.2100 | **GPU-Next** (`vo=gpu-next`) | ✅ **PASS** | `gpu-next` shaders executed cleanly on NV drivers (595.80) |
| **Dolby Vision** | HEVC Profile 8.1 | Legacy (`vo=libmpv`) | ✅ **PASS** | HDR10 base layer parsed cleanly |
| **Dolby Vision** | HEVC Profile 8.1 | **GPU-Next** (`vo=gpu-next`) | ✅ **PASS** | DoVi RPU metadata successfully applied |
| **Colorbars** | HEVC BT.2020 PQ | Legacy (`vo=libmpv`) | ✅ **PASS** | Zero rendering artifacts |
| **Colorbars** | HEVC BT.2020 PQ | **GPU-Next** (`vo=gpu-next`) | ✅ **PASS** | Monotonicity & neutral axis verified |

---

## 3. 🔬 Unit & Pipeline Validation Suite

The automated test suite in `tests/` was executed against the release build:

* **GPU Validation & Render Backend Suite**:
  * `tests/test_render_backend.py`
  * `tests/test_gpu_validation.py`
  * `tests/test_pixel_pipeline.py`
  * `tests/test_wayland_color_trace.py`
  * **Result**: `Ran 136 tests in 3.192s — OK` (100% PASS).

---

## 4. 📝 Architectural Observations

1. **GPU Vendor Policy Enforcement**:
   - On **Intel (iGPU)**, `Gtk.GraphicsOffload` remains `ENABLED`, taking advantage of hardware direct scanout under Wayland.
   - On **NVIDIA (dGPU)**, `Gtk.GraphicsOffload` is safely forced to `DISABLED` to prevent Wayland cursor flickering and buffer swap latency on proprietary drivers.
2. **GPU-Next Renderer Performance**:
   - `CINEHDR_RENDER_BACKEND=gpu-next` operates seamlessly with `libplacebo` v7.360.1.
   - HDR tone-mapping and Dolby Vision Profile 8 metadata mapping show zero dropped frames and accurate gamut representation.
3. **Flatpak Build Ergonomics**:
   - `run_dev.py` was enhanced with a robust fallback to invocation of `blueprint-compiler` and `glib-compile-resources` directly if `meson` is absent from the container execution environment.

---

## 5. Verdict

**`RELEASE ACCEPTED`**: Version `v1.8.5.2.0` passes all validation gates for both Intel and NVIDIA graphics hardware on Legacy and GPU-Next render backends.
