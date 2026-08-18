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
