# L4 Compositor & Hardware Validation Matrix

Before merging to `main`, the pixel pipeline and Wayland presentation logic must be manually validated across actual display environments to ensure the compositor is passing the PQ surface through without implicit double tone-mapping or banding.

## Environments

| ID  | Compositor / DE    | GPU Driver          | Protocol           |
| --- | ------------------ | ------------------- | ------------------ |
| ENV1| KWin (KDE Plasma)  | AMD Mesa (AMDGPU)   | color-management-v1|
| ENV2| KWin (KDE Plasma)  | NVIDIA Proprietary  | color-management-v1|
| ENV3| Mutter (GNOME)     | AMD Mesa (AMDGPU)   | Wayland (legacy)   |
| ENV4| wlroots (Sway)     | AMD/Intel Mesa      | wlr-color          |

## Scenarios

For each environment, test the following:

1. **HDR10 1000-nit Passthrough (No Tone Mapping)**
   - Display target set to 1000 nits.
   - Assert visual brightness of 1000-nit patches matches reference.
   - Assert no banding on smooth gradients.

2. **HDR10 Tone Mapping**
   - Display target set to 400 nits.
   - Assert highlights (4000-nit patches) roll off smoothly and are not hard-clipped.
   - Assert shadows (0.01 - 1 nits) are not crushed to pure black.

3. **Double Tone Mapping Check**
   - Use `force-hdr` mode while the compositor is in SDR mode.
   - The image MUST appear heavily desaturated and washed out (since it's raw PQ being interpreted as sRGB). If it looks "normal", the compositor is silently tone mapping the surface, breaking the pipeline.

4. **Tearing and Presentation**
   - Move the window quickly.
   - Assert no tearing (proves GTK 4.16 `glFenceSync` or our CPU wait fallback is working).

## Sign-off

| Scenario | ENV1 | ENV2 | ENV3 | ENV4 |
| -------- | ---- | ---- | ---- | ---- |
| 1        |      |      |      |      |
| 2        |      |      |      |      |
| 3        |      |      |      |      |
| 4        |      |      |      |      |

## Automated Smoke Validation (W9)

| GPU Hardware | Driver / Session | Render Backends Tested | Smoke Status | Evidence |
| :--- | :--- | :--- | :---: | :--- |
| **Intel Raptor Lake Iris Xe** | Mesa Wayland (Flatpak) | `legacy` (`opengl`) | ⚠️ **PROVISIONAL** | `test_report.json` (local artifact) |
| **NVIDIA RTX 3050 Laptop** | NV 610.57.04 / 595.80 (Native PRIME) | `legacy` (`opengl`) | ⚠️ **PROVISIONAL** | `test_report.json` (local artifact) |
| **Intel + NVIDIA** | Flatpak / Native PRIME | `gpu-next` (`opengl-next`) | ℹ️ **STUB_UNSUPPORTED** | `test_report.json` (local artifact) |

*Evidence correction (2026-08-25): the JSON proves legacy process startup and
the expected GL vendor, but its historical PASS predicate did not require a
rendered first frame, expected HDR telemetry, or absence of file-open warnings.
Those eight labels are provisional until the runner is corrected. All eight
GPU Next configurations are `STUB_UNSUPPORTED` on the unpatched libmpv and are
not GPU Next coverage.*

The corrected `cinehdr-vendor-smoke-v2` oracle is implemented and locally
unit-tested, but has not been executed on the laptop. It requires the exact
pinned Flatpak, a loaded timed media-frame marker, strict API/GPU/runtime/HDR
evidence, clean file loading, and pinned fixture hashes. Until new reports are
adjudicated, the historical rows above remain `PROVISIONAL`/`STUB_UNSUPPORTED`.
See [`docs/GPU_VENDOR_SMOKE_VALIDATION.md`](docs/GPU_VENDOR_SMOKE_VALIDATION.md).

## Gate 2 & Gate 3 — EGL Harness Status

> [!IMPORTANT]
> Gates below require a native `mpv-gpu-next` build (outside Flatpak). Harnesses are implemented and ready in `tests/`.

| Gate | Harness | Status |
| :--- | :--- | :---: |
| **Gate 2 W3/W4/W5** — FBO pixel pipeline (Intel) | `tests/run_gate2_extended_matrix.py` | ⏳ PENDING native build |
| **Gate 2 W6** — A/B performance benchmark 120f (Intel) | `tests/run_performance_ab.py` | ⏳ PENDING native build |
| **Gate 3** — DoVi policy & peak sweep 100→1000 nit (Intel) | `tests/run_gate3_dovi_matrix.py` | ⏳ PENDING native build |
| **Gate 2 W3/W4/W5** — FBO pixel pipeline (NVIDIA) | `tests/run_gate2_extended_matrix.py` + PRIME env | ⏳ PENDING native build |
| **Gate 2 W6** — A/B performance benchmark 120f (NVIDIA) | `tests/run_performance_ab.py` + PRIME env | ⏳ PENDING native build |
| **Gate 3** — DoVi policy & peak sweep 100→1000 nit (NVIDIA) | `tests/run_gate3_dovi_matrix.py` + PRIME env | ⏳ PENDING native build |

## Interactive HDR Scenarios (Manual — Dell Alienware AW3225QF on HDMI-1)

| Scenario | Description | Status |
| :--- | :--- | :---: |
| **S1** | HDR Passthrough on HDMI-1: verify HDR badge, `st2084_pq` color state, no tone-mapping veil | ⏳ PENDING |
| **S2** | Display swap HDMI-1 ↔ eDP-1: verify seamless HDR↔SDR pipeline switch without crash | ⏳ PENDING |
| **S3** | Fullscreen & resize on HDMI-1 during playback: no hangs, no buffer leaks | ⏳ PENDING |
| **S4** | HDR mode toggle `auto / force-hdr / force-sdr` during playback: verify pipeline reacts | ⏳ PENDING |
