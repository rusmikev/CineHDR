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
| **Intel Raptor Lake Iris Xe** | Mesa Wayland | `legacy` & `gpu-next` | ✅ **PASS** | [`test_report.json`](file:///home/rusmikev/Downloads/Cine/test_report.json) |
| **Intel Raptor Lake Iris Xe** | Mesa Wayland | `legacy` & `gpu-next` | ✅ **PASS** | [`test_report.json`](test_report.json) |
| **NVIDIA RTX 3050 Laptop** | NV 595.80 Wayland | `legacy` & `gpu-next` | ✅ **PASS** | [`test_report.json`](test_report.json) |

*W9 Smoke Gate (2026-08-23): 16/16 PASS — 6-second process stability, GL context creation, clean shutdown confirmed on Intel iGPU and NVIDIA dGPU for both `opengl` and `opengl-next` backends. Connected display: Dell Alienware AW3225QF (HDMI-1, 4K QD-OLED, HDR10/Dolby Vision, `st2084_pq`, BT.2020, 10000 nit peak — HDR ACTIVE).*

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
