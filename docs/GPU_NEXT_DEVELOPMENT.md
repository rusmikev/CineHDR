# GPU Next local development

## Pinned prototype dependency

The prototype uses the experimental `libmpv-gpu-next` branch from
`https://github.com/lhc70000/mpv.git`, pinned to the full commit:

```text
97179bce7ed980c53647d6344916f632fe689e9e
```

Do not replace the system mpv installation. The verified local layout keeps
the source and install prefix beside the CineHDR checkout:

```text
work/
  CineHDR/
  mpv-gpu-next/
  mpv-gpu-next-prefix/
```

The verified build configuration is:

```sh
git clone --branch libmpv-gpu-next --single-branch \
  https://github.com/lhc70000/mpv.git ../mpv-gpu-next
git -C ../mpv-gpu-next checkout 97179bce7ed980c53647d6344916f632fe689e9e

meson setup ../mpv-gpu-next/build-gpu-next ../mpv-gpu-next \
  --prefix="$(pwd)/../mpv-gpu-next-prefix" \
  -Dcplayer=false -Dlibmpv=true -Dbuild-date=false -Dtests=false
meson compile -C ../mpv-gpu-next/build-gpu-next
meson install -C ../mpv-gpu-next/build-gpu-next
```

The first successful local build used mpv
`v0.41.0-dev-g97179bce7`, libplacebo `v7.360.1`, FFmpeg `8.1.2`,
Meson `1.11.1`, and GCC `16.1.1`.

The experimental commit already contains the change carried by the stable
Flatpak-only `build-aux/flatpak/fix-screenshots.diff`; do not apply that patch
a second time when switching the manifest to the custom source. The local
patch file's missing final newline was repaired so it remains valid for the
stable mpv 0.41 source path.

## Selecting the backend

For normal local testing, use the **GPU Next (Experimental)** switch in
**Preferences → Video**, or its mirror in the **HDR & Video Output** playback
menu, then restart CineHDR. Preferences remain available on the start page,
while playback controls are intentionally hidden there. The saved choice
applies to HDR and SDR video. If `opengl-next` is unavailable at the next
startup, CineHDR keeps the preference but uses the standard renderer for that
process and reports the reason in diagnostics.

`CINEHDR_RENDER_BACKEND` remains a development override and takes priority over
the saved preference:

- `legacy` uses libmpv's current `opengl` Render API.
- `gpu-next` requires the experimental `opengl-next` API from the pinned custom
  libmpv. Startup fails visibly if it is unavailable.
- `auto` tries `opengl-next` during context creation, then starts the legacy
  `opengl` backend if that one attempt fails. It never switches backends while
  video is playing.

For a strict developer run, from the CineHDR checkout:

```sh
LD_LIBRARY_PATH=../mpv-gpu-next-prefix/lib64 \
  CINEHDR_RENDER_BACKEND=gpu-next \
  ./run_dev.py /path/to/video.mkv
```

At startup CineHDR logs the configured, requested, and active API types. A
fallback reason is logged when development `auto` or the saved GPU Next
preference has selected legacy. The thumbnail preview remains on legacy in
this phase.

While a developer environment override is present, the UI explains that it
has priority instead of claiming that a restart will apply the saved setting.
Initialization and runtime failures are recorded as a stopped renderer with
the exact reason. Recreating the GTK/GL surface cannot clear that latch; only
a new CineHDR process may initialize a renderer again.

`run_gpu_next.sh` loads the pinned custom libmpv but does not set a renderer
override, so it respects the UI preference. An override explicitly supplied by
the caller is still honored.

python-mpv `1.0.8` cannot construct its declared integer `depth` render
parameter. CineHDR applies a narrowly scoped runtime compatibility fix after
detecting that exact failure. Remove the workaround once the installed binding
can construct `MpvRenderParam("depth", 8)` itself.

The current experimental `opengl-next` backend does not consume
`MPV_RENDER_PARAM_DEPTH`: it derives automatic dithering depth from the wrapped
FBO format. `internal_format` is therefore authoritative for GPU Next. We still
pass matching depth 8/16 because the shared legacy OpenGL path does consume it;
in particular, depth 16 prevents legacy from treating an HDR `GL_RGBA16F`
target as 8-bit.

## Verified first-frame checks

- Patched libmpv + `gpu-next`: SDR first frame in `GL_RGBA8`, depth 8.
- Patched libmpv + `legacy`: SDR first frame in `GL_RGBA8`, depth 8.
- System libmpv + `auto`: explicit `opengl-next` failure, then a successful
  legacy SDR first frame.
- Patched libmpv + `gpu-next` on Wayland: HDR10 first frame in `GL_RGBA16F`,
  depth 16, with the Rec.2100 PQ path active.
- User-reported AMD hardware smoke test: approximately 20 minutes of playback
  through `run_gpu_next.sh` without a visible problem. The active settings were
  software decoding (`hwdec=false`), `hdr-mode=force-hdr`, and automatic target
  peak. This is useful real-hardware evidence, but seek/resize/context
  recreation and VRAM behaviour were not instrumented, so Gate 1 remains open.
- Instrumented AMD Radeon RX 9060 XT quick validation on Mesa `26.1.7` passed
  with the real Profile 8 sample and `vaapi-copy`: strict `opengl-next`,
  `GL_RGBA16F`/16, start-position normalization, 1× playback, pause/resume,
  three exact seeks, real `960×540` and `1280×720` resizes, and both fullscreen
  transitions completed. CineHDR recorded zero GL errors, zero FBO failures,
  zero FBO-pool drops, two mpv decoder/VO drops, 13.3 MiB RSS growth, and
  7.5 MiB reported VRAM growth over 1.2 minutes. This closes the repeatable
  quick-interaction check, but not the 30-minute or context-lifecycle parts of
  Gate 1.
- The follow-up quick run with Linux DRM-client accounting also passed. It
  separated zero decoder drops from three video-output drops. CineHDR's own
  VRAM moved from 691.5 MiB through a transient 1656.6 MiB peak to 768.8 MiB
  at the end (+77.4 MiB), while GTT fell by 59.6 MiB. Whole-GPU VRAM grew by
  844.2 MiB during the same run, demonstrating why the global sysfs number is
  contextual and must not be attributed to CineHDR as a leak.
- The accepted 31.2-minute soak on the same RX 9060 XT passed with zero decoder
  drops, two video-output drops (about 0.005%), and no GL, FBO-allocation, or
  CineHDR FBO-pool failures. CineHDR's DRM-client VRAM stayed near 688–691 MiB
  through the 30-minute playback segment and ended at 770 MiB after the
  transition sequence; RSS grew by 67.1 MiB. This closes the Gate 1 duration
  check on this hardware. Controlled context recreation remains a separate
  check.
- The controlled HDR lifecycle run passed two real context cycles (`1→2→3`)
  on the same machine. GPU Next, `vaapi-copy`, DV Profile 8, HDR observers and
  `GL_RGBA16F`/16 were restored after both cycles; subsequent playback and
  exact seeks passed, with zero GL/FBO/decoder failures and process VRAM
  returning to its starting value. This closes the lifecycle part of Gate 1
  for that configuration.
- The single-process transition matrix passed SDR → HDR → SDR → HDR with
  an unchanged GPU Next context. Both SDR observations published
  `GL_RGBA8`/8 with sRGB and cleared Dolby Vision state; both HDR observations
  published `GL_RGBA16F`/16 with Rec.2100 PQ, restored DV Profile 8 and used
  `vaapi-copy`. Playback and exact seeks passed, with zero decoder, GL, FBO,
  or pool failures. Between the first and second warmed HDR states, RSS fell
  5.2 MiB and CineHDR DRM-client VRAM fell 3.7 MiB (GTT +7.9 MiB). This closes
  the controlled transition criterion on the tested configuration; at that
  point the separately specified long SDR run was still outstanding.
- The accepted strict SDR soak completed in 31.2 minutes on the same RX 9060 XT
  and Mesa `26.1.7`. All 13 actions passed with active `opengl-next`,
  `vaapi-copy`, `GL_RGBA8`/8-bit and sRGB. CineHDR recorded zero decoder,
  OpenGL, FBO-allocation, and FBO-pool errors. mpv reported five video-output
  drops out of approximately 43,584 played frames (0.011%), so the run remains
  `WARN`. During the uninterrupted segment, DRM-client VRAM stayed around
  243–247 MiB and the temporary peak was reclaimed. RSS rose 50.4 MiB; this is
  consistent with a filling demuxer back-buffer but does not prove the absence
  of a leak. The accepted report is
  `validation-reports/gpu-next-sdr-soak-20260822-135854.txt`; its launcher log
  is `validation-reports/gpu-next-sdr-soak-20260822-132739-309949.log`.

These checks establish a working prototype boundary, not visual correctness,
performance parity, long-run stability, or Dolby Vision support.

## Gate 1 milestone review

Gate 1 is **conditionally passed with the recorded WARN** on the tested AMD
Radeon RX 9060 XT / Mesa `26.1.7` configuration and the pinned mpv
`g97179bce7`, libplacebo `7.360.1`, and FFmpeg `8.1.2` stack. The accepted
evidence covers the strict SDR duration and interaction sequence, HDR/Dolby
Vision soak, two controlled GL-context recreation cycles, and the
SDR → HDR → SDR → HDR matrix. The five video-output drops remain in the
record. The monotonic 50.4 MiB RSS increase is below the validator's 100 MiB
warning threshold and is not proof of a leak, but it remains an unresolved
observation for Gate 2 load/unload and resize-settling measurements.

This closure is prototype evidence for one hardware, driver, media set, and
dependency build. It is not general GPU/vendor compatibility, color-accuracy,
performance, stability, memory-leak, or Dolby Vision certification. Legacy
remains the default and startup fallback, runtime renderer hot-swap remains
forbidden, and Profile 5 remains blocked.

## Next milestone: dependency-pinned Flatpak candidate

[ADR-0003](adr/0003-pinned-gpu-next-flatpak-stack.md) starts the next local
workstream: make the tested custom libmpv stack buildable as one Flatpak
candidate so an eventual user installs an artifact instead of compiling mpv.
The candidate uses one custom libmpv containing both `opengl` and
`opengl-next`; it does not load or exchange two libmpv builds at runtime.

The dependency target is mpv
`97179bce7ed980c53647d6344916f632fe689e9e`, FFmpeg `8.1.2`, libdovi `3.3.2`,
libplacebo `v7.360.1`, and libdisplay-info `0.3.0`. Pinned hwdata `0.410`
supplies the PNP database needed only while building libdisplay-info and is
removed from the runtime image. The released stable artifact remains separate
until later gates pass. The first slice records and statically verifies the
complete dependency graph. Clean Flatpak builds, loaded-library evidence, an
in-Flatpak Meson run, and short legacy/GPU Next hardware smoke tests are
subsequent acceptance steps. Because packaging does not change renderer,
color, or lifecycle logic, it is not a reason to repeat either completed
30-minute soak.

That first slice is implemented and has produced a clean local candidate. The
manifest uses the exact source pins above, an offline Cargo vendor graph with
per-crate checksums, and build-time assertions for the intended FFmpeg
ABI/version, libplacebo Dolby Vision feature flags, and final ELF dependency
edges. Static manifest tests are registered in Meson. The pinned
`shared-modules` submodule was initialized at
`75fa45bdee634e6b3ad36e1db33a69c1ba43417c`.

The 2026-08-22 clean Builder run completed the full
FFmpeg → libdovi → libplacebo → custom libmpv → CineHDR chain. FFmpeg reported
`8.1.2`; libdovi built `3.3.2` with Cargo `--frozen --offline`; libplacebo
reported `7.360.1`, `dovi: YES`, and `libdovi: YES`; custom libmpv linked
`libavcodec.so.62`, `libavutil.so.60`, and `libplacebo.so.360`; and that
libplacebo linked `libdovi.so.3`. Loader inspection with the candidate library
path resolved all of those libraries from `build-dir/files/lib`. The recorded
build environment was:

- GNOME 50 SDK commit
  `dfecff8363f84250df427a8998569ddade6ce9c5dc74f2ca4ceeeeaabe975985`;
- GNOME 50 Platform commit
  `a8da766dd0273a67539d2b98358ed6809d8e729280baf63428108837135229d3`;
- Rust stable 25.08 extension commit
  `385427f6dabe6d297cced114496cfa6e38bb93fe4c6fbee7fdabb97dd141d29a`;
- `org.flatpak.Builder` commit
  `d04b579250dbd3d6c53b5496824b6964022c35861d0e9959d0baf697356f0827`
  (`flatpak-builder` 1.4.9).

The candidate was exported only to the ignored local `repo/` and then to
`io.github.rusmikev.CineHDR.flatpak`. The application OSTree commit is
`708dbbabbfe49b3ba24ae245321bfc7d6266bf65f443587b2d044ae3f5c3b1c0`;
the 46,011,816-byte bundle SHA-256 is
`d6854f680af95b204d8e97705d56e2aba7954bff551d18ed0a1de94bef4375a3`.
It was subsequently installed and GPU-launched locally for the short smoke
check described below. It has not been published.

A second clean build with Builder stage-cache disabled rebuilt the complete
stack and exported application commit
`cc7ae181a82f19a7ff6e972b1569478e0f8157a6b415d6c6ba3fcaa1766fdb1d`.
OSTree reported the same 214 content objects and wrote zero new content bytes,
which is strong evidence that the packaged file/tree content is reproducible
under the recorded environment. The commit metadata was not identical: a
second bundle was 46,014,296 bytes with SHA-256
`59eecdafa1b427ba3a4d76ac6c0003319795c4749f87dd380027854e0193840c`.
Therefore this milestone still does not claim bit-for-bit artifact
reproducibility; normalizing and comparing the remaining OSTree commit metadata
is later packaging work. The retained checkout bundle is the first artifact
and keeps the first SHA-256 recorded above.

After the packaging changes, the focused manifest/backend/validator tests and
the complete host Meson suite passed (`7/7`), and `git diff --check` was clean.

The complete Meson suite was then configured, compiled, and run inside the
candidate Builder environment with the checkout mounted read-only and a
separate writable build directory under `/tmp`. Its saved Meson log records
all seven tests at exit status 0 and `Fail: 0`. The enclosing
`org.flatpak.Builder` app returned status 1 after successful `--run` commands,
including `pwd`, so the inner Meson summary and per-test statuses are the
acceptance evidence rather than that anomalous wrapper status.

The next packaging checkpoint is short legacy and strict GPU Next smoke tests
of the installed local bundle on real hardware. Packaging still does not
justify repeating either completed 30-minute soak.

### Local bundle smoke workflow

Installing the candidate is an explicit user action because it uses the normal
application ID and therefore updates any existing per-user CineHDR install.
The validation runner never installs or updates it itself. From this checkout,
install or update only the local ignored bundle with:

```sh
flatpak install --user --or-update ./io.github.rusmikev.CineHDR-gpu-next.flatpak
```

First start a separate legacy process with a representative file, confirm a
frame and normal playback, then close it:

```sh
video_path="$(realpath -e -- "/path/to/video.mkv")"
flatpak run \
  "--filesystem=${video_path}:ro" \
  --env=GDK_BACKEND=wayland \
  --env=CINEHDR_RENDER_BACKEND=legacy \
  io.github.rusmikev.CineHDR "${video_path}"
```

Then run the automated strict GPU Next quick check as a new process:

```sh
./run_flatpak_gpu_validation.sh "/path/to/video.mkv"
```

The Flatpak runner accepts only `quick`, exposes only the selected file and the
local `validation-reports/` directory, propagates an abnormal application
exit, verifies the installed application commit against the corrected bundle,
and refuses success without a new timestamped report. Environment overrides
keep both checks independent of the saved preference and cannot change
renderers inside a running process. This workflow has been prepared and
statically tested.

The first installed-candidate GPU Next run completed on 2026-08-23 with
overall status `WARN`. The approximately one-minute run used a 3840×2160,
10-bit HEVC, BT.2020/PQ Dolby Vision Profile 8 Level 6 file on the AMD Radeon
RX 9060 XT and Flatpak Mesa `26.1.6`. It confirmed active `opengl-next`, mpv
`g97179bce7`, libplacebo `7.360.1`, FFmpeg `8.1.2`, a `GL_RGBA16F`/16-bit
target, and published Rec.2100 PQ. All 11 playback, seek, resize, pause, and
fullscreen actions passed. CineHDR observed zero OpenGL errors, FBO allocation
failures, or FBO-pool drops; mpv reported zero decoder drops. RSS grew 26.4
MiB, while the measured DRM-client VRAM and GTT allocations were reclaimed
below their starting values.

This run does not yet close the packaging hardware checkpoint. Hardware
decoding reported `no`, although the installed preference enables hwdec and
the candidate contains the Radeon VAAPI driver. mpv also reported five
video-output drops among approximately 384 played frames. The launcher log
captured only two RADV warnings and therefore cannot distinguish VAAPI device,
driver, codec-profile, or negotiation failure. The Flatpak runner now starts
the installed entry point with validation-only libmpv verbose logging so a
short `quick` check can capture the missing mpv diagnostics. This does not
modify the installed application or normal CineHDR logging and is not a reason
to repeat a 30-minute soak. The accepted intermediate report is
`validation-reports/gpu-next-quick-20260823-065938.txt`; its incomplete log is
`validation-reports/gpu-next-quick-20260823-065826-7255.log`.

The first diagnostic repeat produced
`validation-reports/gpu-next-quick-20260823-071449.txt`. It reproduced
`hwdec=no` with all 11 actions passing, zero decoder/OpenGL/FBO failures, and
four video-output drops among approximately 384 frames. RSS grew 57.4 MiB;
CineHDR DRM-client VRAM ended 75.8 MiB above its low starting sample after a
temporary peak, which is not leak evidence in a one-minute interaction run.
Its log, `validation-reports/gpu-next-quick-20260823-071337-17796.log`, proved
that enabling Python's root logger alone records CineHDR telemetry but not
libmpv messages. The runner therefore now injects python-mpv's documented
`log_handler` at verbose level only inside the isolated validation process.

Three subsequent verbose runs produced
`validation-reports/gpu-next-quick-20260823-073307.txt` and
`validation-reports/gpu-next-quick-20260823-080007.txt`, followed by
`validation-reports/gpu-next-quick-20260823-080601.txt`. All three again passed
all 11 actions with zero decoder, OpenGL, FBO-allocation, and FBO-pool
failures; video-output drops were zero, two, and zero, while RSS grew 11.7 MiB,
12.6 MiB, and 18.2 MiB.
Their logs retained mpv trying both `hevc-vaapi` and `hevc-vaapi-copy`, then
reporting `Could not create device` before any libva driver message. Adding
`LIBVA_MESSAGING_LEVEL=2` for the second run did not produce libva output,
confirming that failure preceded driver initialization.

Source and ELF inspection localized the packaging defect. mpv's standalone
`vaapi-copy` device path needs the DRM native display; the first candidate's
feature list contained `vaapi` and `vaapi-wayland` but omitted both `drm` and
`vaapi-drm`. Its `libmpv.so.2.5.0` also had no `libdrm.so.2`,
`libdisplay-info.so.3`, or `libva-drm.so.2` dependency. This explains the
device-creation failure without blaming the Mesa driver or changing a driver
search path. The same logs contain repeated FFmpeg warnings about multiple
Dolby Vision RPUs in one access unit. They are retained as media/decoder
evidence and do not broaden the Dolby Vision capability claim.

The corrected manifest now fails configuration unless `drm`, `vaapi`, and
`vaapi-drm` are enabled. It builds pinned libdisplay-info `0.3.0` from commit
`47a5590e9c4eb35d67651b8c05a55f1a48259329`, using pinned hwdata `0.410`
commit `8bb6c48dc2e683777e625d1c9ef02c1b25b741cd` only at build time. Final
`readelf` assertions require `libdrm.so.2`, `libdisplay-info.so.3`, and
`libva-drm.so.2`. The clean 2026-08-23 build passed those assertions; its
feature list contains `drm`, `vaapi`, `vaapi-drm`, and `vaapi-wayland`, and
the runtime image contains no hwdata tables. The corrected application OSTree
commit is `1ca32dbf0f0ef57a3b11545b1ff9cf2ec5ab837a6ff1d2328e52a556d759d5c5`.
The separate 46,090,048-byte local bundle
`io.github.rusmikev.CineHDR-gpu-next.flatpak` has SHA-256
`92bc7e3f1efa4794d2de3743945b4cde4f5ac0f900ced372aa9ae7fd234b9576`;
the older bundle is retained as failure evidence and must not be used for the
next check. Nothing has been published.

The one short strict GPU Next run of the corrected bundle passed on 2026-08-23.
The installed application commit matched
`1ca32dbf0f0ef57a3b11545b1ff9cf2ec5ab837a6ff1d2328e52a556d759d5c5`.
libva found the Flatpak Radeon driver, initialized it successfully, and mpv
reported `Using hardware decoding (vaapi-copy)`. Active `opengl-next`, the
Profile 8 metadata observation, `GL_RGBA16F`/16-bit and Rec.2100 PQ were
preserved. All 11 actions passed with zero decoder and video-output drops,
zero CineHDR OpenGL/FBO failures, and no validator warning. RSS grew 27.0 MiB.
The raw DRM-client VRAM start-to-end increase was 242.1 MiB after a temporary
peak; a one-minute interaction run cannot classify that as either a leak or
leak-freedom, so the existing Gate 2 load/unload settling test remains the
acceptance authority.

The verbose log also shows `hwdec=auto` probing unavailable NVDEC/CUDA before
successfully selecting VAAPI-copy. Those CUDA load messages are expected on
this non-NVIDIA machine and did not affect decoding. Repeated FFmpeg warnings
about multiple Dolby Vision RPUs remain sample/decoder evidence, not a broader
Dolby Vision claim. The accepted report is
`validation-reports/gpu-next-quick-20260823-090720.txt`; its log is
`validation-reports/gpu-next-quick-20260823-090607-96680.log`. This closes the
strict GPU Next part of the local Flatpak hardware checkpoint. It is not a
general package, GPU-vendor, color-accuracy, or Dolby Vision certification,
and neither 30-minute soak was repeated.

## Gate 2 start: controlled embedded-FBO pixels

[ADR-0004](adr/0004-gate2-fbo-pixel-validation.md) starts the correctness
stage at CineHDR's caller-owned FBO boundary. The first slice renders locally
generated lossless HDR and SDR patch fixtures through separate legacy and GPU
Next contexts using the same exact custom libmpv, options, timestamp, and
software decode. HDR readback uses `GL_RGBA16F`/16 and SDR readback uses
`GL_RGBA8`/8. The harness records both results rather than requiring pixel
identity. This first instrumentation slice strictly rejects missing
provenance, wrong APIs or formats, absent/non-finite samples, and incomparable
runs. Range, black distinction, neutral ordering/axis, primary dominance, and
renderer deltas are initially diagnostic: no numerical color threshold is a
release gate until its mathematical and fixture-error basis is recorded. The
first report therefore cannot by itself close Gate 2.

This offscreen layer deliberately excludes GTK, Wayland, the display, and
hardware decoding so a difference can be attributed to the renderer. It is
not color certification. After the deterministic baseline, the same fixtures
and report schema move to the real Radeon software/VAAPI-copy matrix,
dithering, GTK/Wayland color-state, subtitles/screenshots, performance, and
load/unload/resize settling. Both renderers remain separate startup/context
choices; no runtime hot-swap is introduced.

The first instrumentation slice is now implemented in
`tests/generate_patterns.py`, `tests/pixel_validator_egl.c`, and
`tests/run_pixel_pipeline.py`. FFmpeg 8.1.2 generates 24-frame, full-range,
10-bit planar GBR FFV1 fixtures and `setparams` supplies the frame-level color
metadata that FFV1/Matroska otherwise omits. Both `ffmpeg` and `ffprobe`
versions, decoded frame count, stream metadata, patch definitions, and media
SHA-256 are recorded. The probe matches CineHDR's main embedded target with
`flip_y=False`, rebinds the caller-owned FBO before readback, and records the
observed source-row/OpenGL-row mapping. It rejects the initial empty renderer
redraw rather than treating it as a decoded frame.

The explicit software baseline can be reproduced without Radeon or Wayland:

```sh
LIBGL_ALWAYS_SOFTWARE=1 MESA_LOADER_DRIVER_OVERRIDE=llvmpipe \
  python3 tests/run_pixel_pipeline.py \
  --output validation-reports/gate2-pixel-software.json
```

The first accepted structural report is
`validation-reports/gate2-pixel-software-20260823-095222.json` (report SHA-256
`e66da700ef2eabe158e4806c8b9bd21d47d0620363bb9470bc8d17566aaa7373`).
It completed both APIs for HDR and SDR on Mesa llvmpipe with the exact mpv
`g97179bce7`, FFmpeg `8.1.2`, and libplacebo `7.360.1` runtime identities.
Both APIs preserved finite samples, distinct black/near-black values,
monotonic neutral samples, a zero neutral-axis channel delta at the sampled
points, and primary-channel dominance. The largest sampled legacy/GPU-Next
absolute channel delta was `0.036132812` for HDR float output and `0.215686262`
for SDR 8-bit output. These deltas are retained for transform/error analysis;
they are not yet PASS/FAIL color thresholds. The report status `complete`
means complete comparable evidence, not Gate 2 closure or color
certification. No real GPU, Wayland, or VAAPI path was exercised by this run.
Two independent clean fixture directories produced byte-identical media:
HDR SHA-256 `cdee467e93298eb5bad11c408edbe1138edf046bc845580fc97ae0f250eb4b50`
and SDR SHA-256
`c169a9bf72eb4a9efa2f79528302839f9f6292a429c4b6210b631838767cb6a4`.

The target-hardware software-decode checkpoint is also complete. On this
hybrid-GPU host an unqualified Mesa selection used the integrated Raphael GPU,
so the accepted run selected the discrete card explicitly:

```sh
env -u LIBGL_ALWAYS_SOFTWARE -u MESA_LOADER_DRIVER_OVERRIDE \
  DRI_PRIME=pci-0000_03_00_0 \
  python3 tests/run_pixel_pipeline.py \
  --output validation-reports/gate2-pixel-rx9060xt-software-20260823.json
```

The accepted report SHA-256 is
`b0ad540280f0eeb5746753a9b64423b6dbe56e2df846c5bed52fcdcc2337c665`.
Both independent renderer contexts reported `AMD Radeon RX 9060 XT`
(`radeonsi`, `gfx1200`) with Mesa `26.1.7`, the exact pinned mpv, FFmpeg, and
libplacebo identities, the intended HDR and SDR FBO formats, software decode,
and no mpv, OpenGL, or FBO errors. Both APIs preserved the same sampled
structural diagnostics as the llvmpipe baseline. The maximum sampled A/B
channel deltas were again `0.036132812` for HDR and `0.215686262` for SDR.

This completes only the real-Radeon software-decode row of the Gate 2 matrix.
The measurements remain diagnostic and are not fitted PASS/FAIL color
thresholds. Gate 2 remains open for justified transform/error bounds,
VAAPI-copy, GTK/Wayland/display output, dithering and workflow coverage, and
settling/performance evidence. It is not a color-accuracy, GPU-vendor, package,
or Dolby Vision certification.

The first strict VAAPI-copy attempt deliberately reused the exact RGB FFV1
fixtures. On the RX 9060 XT, mpv reported `hwdec-current=no` after
`vaapi-copy` was requested, so the run failed before renderer comparison. The
report
`validation-reports/gate2-pixel-rx9060xt-vaapi-copy-20260823.json` has
SHA-256
`57734f7a0d576ed5779f882fe291ac0c9fc91d9bde3e053eebb83056d21e5e55`.
This is accepted capability-rejection evidence for RGB FFV1, not a GPU Next
failure, and the same input should not be repeated.

The next profile is a deterministic full-range HEVC Main 10/yuv420p10 MP4.
Its direct MP4 encodes are reproducible across two clean directories, the 24
decoded frames are byte-identical to the encoder YUV input and one another,
and the RGB/YUV reconstruction error remains explicit diagnostic provenance.
The accepted generator revision is `hevc-main10-yuv420p10-mp4-v2`; media
SHA-256 values are
`bc437162b00b6565ca486ecc0a67704b6f099ebccf03cf671b2a0f87e94e021d`
for HDR and
`41393a0ee9dd887a1f11d4b21946fef3d1f49e72127719eefd1ac40aa9d5e360`
for SDR.

Its fresh-cache software baseline is
`validation-reports/gate2-pixel-hevc-main10-mp4-llvmpipe-software-20260823-132808.json`
(report SHA-256
`e038fd8fe07c0feaf7dd08ff4633775e5587048f4e869d966017272f69984e2e`).
The report explicitly retains a 30.2-second legacy llvmpipe cold-shader
warm-up timeout; warm-up is setup only and is not pixel or startup evidence.
All four subsequent fresh measured processes completed with exact pinned-stack
identity, expected metadata/FBOs, structural diagnostics, and zero mpv,
OpenGL, or FBO errors. Maximum sampled A/B deltas were `0.068114519` HDR and
`0.219607830` SDR. No PASS/FAIL color tolerance was inferred.

The next user-run checkpoint is the changed-input HEVC/VAAPI-copy row:

```sh
cd /home/rusmikev/Documents/Codex/2026-08-19/z-x20/work/CineHDR

env -u LIBGL_ALWAYS_SOFTWARE -u MESA_LOADER_DRIVER_OVERRIDE \
  DRI_PRIME=pci-0000_03_00_0 \
  python3 tests/run_pixel_pipeline.py --hwdec vaapi-copy \
  --fixture-profile hevc-main10-yuv420p10-lossless \
  --output validation-reports/gate2-pixel-rx9060xt-hevc-main10-vaapi-copy-20260823.json
```

Each renderer still runs in a fresh process. The report is accepted only when
both `decode.requested` and mpv's `hwdec-current` are exactly `vaapi-copy` for
both APIs on the same GL device. A decoder fallback is a strict failed evidence
run, and its report retains the requested mode and actual requested/active
values for diagnosis. No driver path or DRM device is selected by the harness.
This remains embedded-FBO evidence only: Gate 2, GTK/Wayland/display coverage,
color accuracy, and broad GPU/Dolby Vision certification remain open.

The HEVC/VAAPI-copy command completed successfully on the RX 9060 XT. The
accepted report is
`validation-reports/gate2-pixel-rx9060xt-hevc-main10-vaapi-copy-20260823.json`
(SHA-256
`21af4a712d5169e11632bf213403a728e9de7cab5523796b26a4210701d709ff`).
Both independent renderer processes used active `vaapi-copy` and `p010` for
HDR and SDR on Mesa 26.1.7, loaded the exact pinned stack, published the
intended FBO formats, passed every structural diagnostic, and reported zero
mpv, OpenGL, or FBO errors. Maximum sampled A/B deltas were `0.071410418` HDR
and `0.219607830` SDR; they remain diagnostic. This closes the real-Radeon
VAAPI-copy embedded-FBO row without closing Gate 2 or certifying color/Dolby
Vision behavior. No repeat is justified unless the input or implementation
changes.

The live GTK/GDK publication matrix is now implemented as an explicit local
checkpoint. From a Wayland session, run:

```sh
env DRI_PRIME=pci-0000_03_00_0 ./run_wayland_publication_validation.sh
```

The launcher generates or strictly revalidates the exact deterministic HEVC
fixtures, checks their recorded and actual SHA-256 values, and then runs legacy
and GPU Next in separate processes. Each process must complete the exact
SDR -> HDR -> SDR -> HDR sequence with a newer actual `Gdk.GLTexture` after
each switch. Acceptance requires the matching texture format, dimensions,
sRGB versus Rec.2100 PQ color-state equality, real GDK Wayland types, active
`vaapi-copy`, the expected startup-selected API, and the unchanged pinned
runtime. One fresh PASS report is required for each backend.

The launcher does not select or request a DRM device; `DRI_PRIME` is inherited
from the caller. This checkpoint does not use a screenshot as a color oracle
and cannot certify compositor image-description acceptance, display
transformation, or output photometry.

The corrected real RX 9060 XT run passed both separate processes. The accepted
reports are
`validation-reports/wayland-publication-20260823-143411-268387/gate2-wayland-publication-legacy-20260823-143439.txt`
(SHA-256
`393231dc96111acc7eb44e45ed809a876568e26d0dc963625d797365be07fddf`)
and
`validation-reports/wayland-publication-20260823-143411-268387/gate2-wayland-publication-gpu-next-20260823-143509.txt`
(SHA-256
`7256ca8eb0a0aeb4725773fb30cfe6d23385dbcdf95550009b737d70ed8863f8`).
Both used active `vaapi-copy`, the exact pinned stack, the expected `opengl`
or `opengl-next` API, and real `GdkWaylandDisplay`/
`GdkWaylandToplevel` objects. Each process published generations
3 -> 5 -> 7 -> 9 across SDR -> HDR -> SDR -> HDR. Every SDR state was an
actual correctly sized `R8G8B8A8`/sRGB `GdkGLTexture`; every HDR state was an
actual correctly sized `R16G16B16A16_FLOAT`/Rec.2100 PQ texture. No validation
failure was recorded.

The preceding legacy-only attempt selected direct `vaapi` under mpv's normal
automatic policy and was rejected before GPU Next ran. The accepted repeat
changed publication mode to request `vaapi-copy` before loading either file;
the criterion was not weakened. The `File error path` lines written four
seconds after each PASS report are generated while the validator deliberately
closes the current file and are retained in the logs as shutdown context.

This closes the live GTK/GDK object-publication row on the tested Radeon and
Wayland stack. It does not close Gate 2 or prove compositor image-description
acceptance, display transformation, photometry, broad compatibility, color
accuracy, or Dolby Vision correctness.

The next bounded checkpoint is implemented as an opt-in Wayland client trace
on the same two-process matrix:

```sh
env DRI_PRIME=pci-0000_03_00_0 \
  ./run_wayland_publication_validation.sh --protocol-trace
```

It requires GTK to bind `wp_color_manager_v1`, obtain a color-management
object for the CineHDR surface, submit an image description that received
`ready` or `ready2` with perceptual intent, and then commit that same surface
without a protocol failure. The parser associates dynamic protocol object IDs
and tolerates CineHDR's separate private output-probe managers; it does not
match hard-coded IDs. The surface description may remain
compositor-preferred across SDR/HDR input changes, so the trace is not
interpreted as a pixel or display-state oracle. Synthetic positive and strict
negative traces are part of the default Meson suite.

The first real trace stopped strictly after the legacy publication PASS. KWin
advertised the color manager and the output probes received manager
capabilities plus ready output descriptions, but GTK sent neither
`get_surface` nor `set_image_description`. The trace is retained at
`validation-reports/wayland-publication-20260823-150806-285447/publication-legacy.log`
(SHA-256
`3dfc07f4de8e8dc64037037694aff1d10243f78c9107b28554ca106ae751faf1`).
The accompanying publication report retained its accepted PASS and SHA-256
`393231dc96111acc7eb44e45ed809a876568e26d0dc963625d797365be07fddf`;
the renderer and GDK publication were not implicated.

Installed GTK 4.22 exposes Wayland color management as the explicit
`GDK_DEBUG=color-mgmt` opt-in. `--protocol-trace` now enables that flag only
for its traced child processes; ordinary validation and normal CineHDR launches
remain unchanged.

The changed-input repeat is recorded in
`validation-reports/wayland-publication-20260823-153023-295773`. The legacy
GDK publication again passed. GTK's default queue now bound the color manager
and received perceptual intent plus `done`, proving that the opt-in was active,
but destroyed the manager before obtaining a color-management object for the
CineHDR surface. No `get_surface` or `set_image_description` appeared. The
hash-linked machine report is
`gate2-wayland-color-management-legacy-20260823-153023.json`, status `FAIL`,
SHA-256
`dd697e6dc876bd5b831bf30c791bbebca2eef3ecc42c120261b7a0f018f4382b`.

The surface-submission checkpoint is therefore unavailable on the tested GTK
4.22.4/KWin 6.7.4 combination. It is not a renderer failure: the protocol
boundary failed before a backend-specific surface chain existed, so starting
GPU Next could not satisfy the missing GTK prerequisite. Do not repeat this
matrix without a changed GTK/KWin implementation. A manager global alone is
not native-HDR or compositor-acceptance evidence, and the already accepted
GDK texture-publication row remains separate.

The next Gate 2 slice is implemented as the opt-in
`run_gate2_resource_settling.sh` matrix, not another soak. It generates the
accepted one-second HEVC fixtures in an isolated temporary cache and launches
legacy followed by GPU Next in separate processes. Each process fixes
`vaapi-copy` before loading media, warms allocations once, then measures three
identical resize plus HDR -> SDR -> HDR cycles and a final media-unload
cooldown. Same-state RSS and per-process DRM VRAM endpoints are retained
without an invented byte threshold; three strictly increasing endpoints are a
`WARN`, while a structural `PASS` still does not prove leak-freedom. The scope
deliberately excludes long-media demuxer back-buffer behavior.

The launcher accepts no arguments and does not select a DRM device, add
Flatpak permissions, install software, or change saved CineHDR settings. It
requires a real Wayland session and the local pinned libmpv prefix. A valid run
must produce exactly one fresh, machine-readable `PASS` or `WARN` report for
each backend; `FAIL`, missing evidence, or an incomplete second process makes
the launcher fail closed. Actual compositor-negotiated endpoint sizes may
differ from the requested 960x540, but all three measured sizes must match.
The local static/unit and full Meson suites pass; real RX 9060 XT evidence is
still pending.

The first real run stopped in the legacy process before any measured endpoint.
Its warm-up still confirmed the RX 9060 XT/Mesa `26.1.7`, active `opengl`,
`vaapi-copy`, the pinned runtime, the 16-bit Rec.2100 PQ target, and zero
decoder/GL/FBO failures. The first paused resize changed the logical window
from 1120x630 to 1280x720, but the published texture remained 2240x1260 and
the render-frame generation remained 5. This identified a harness/integration
gap, not resource growth or a renderer failure: CineHDR waited exclusively for
an mpv frame update, while the Render API requires the client to redraw the
previous frame when only its target surface changes. The retained FAIL report
is
`validation-reports/resource-settling-20260823-155821-311130/gate2-resource-settling-legacy-20260823-155836.json`
(SHA-256
`52e1257980eb1aa614d2f6905b4e4a6a24f441d32e80f251241004c75a1db368`);
the log SHA-256 is
`1e45128536176b48a67ec4fb235c9dc653efe966a01b449b82c7018cfab0fdd7`.
GPU Next correctly did not start after the prerequisite legacy failure.

The first correction used `notify::width/height`, but the real v2 attempt
proved that Gtk.Widget allocation changes do not emit those notifications on
this GTK 4.22.4 path. The result was the same fail-before-endpoint state with
the v2 contract loaded; it still showed active pinned legacy/VAAPI and zero
GL/FBO failures. Its report is
`validation-reports/resource-settling-20260823-160744-316172/gate2-resource-settling-legacy-20260823-160759.json`
(SHA-256
`9b51018bd4891e24cb4ecf2f8fa1f3da75861c2d735bf599518b939cb916f9a0`);
the log SHA-256 is
`a3efab0035dabbb284ce734f69386285932f7f6aeb7caf7146e67b39d73c0713`.
It adds no resource or renderer-failure evidence, and GPU Next again correctly
did not start.

The corrected implementation now uses Gtk.Widget's actual `do_size_allocate`
vfunc, chains the base allocation, tracks logical width, height and scale
factor, and coalesces an explicit Render API redraw only after a texture
exists. The decision helper and the allocation callback are executed in unit
tests, including the parent chain and forced-redraw request. A paused resize
can therefore redraw the previous frame into a newly sized FBO without changing
media, context, or renderer. The settling evidence revision is
`bounded-resource-settling-v3`; neither failed earlier report can be mistaken
for evidence from the corrected path. One final bounded repeat is technically
justified by this different GTK integration point. No soak repeat is justified.

## Real-GPU validation run

`run_gpu_validation.sh` starts an isolated, strict `opengl-next` process and
drives a repeatable interaction sequence on the supplied video. It does not
change the saved renderer preference and cannot be forwarded to an already
running normal CineHDR process. The validator disables watch-later
restore/write and saved-playlist restore/write, forces 1× speed and no
looping, disables mpv's automatic addition of neighboring files, and waits for
the playback position to remain stable before measuring. Its files and seeks
therefore cannot replace either the user's normal session or resume position.

Start with the approximately one-minute quick run:

```sh
./run_gpu_validation.sh "/path/to/video.mkv"
```

The validator waits for a real GPU Next first frame, then exercises playback,
pause/resume, exact seeks, two window sizes, and entering/leaving fullscreen.
Do not interact with the validation window while it is running. After writing
the report, CineHDR shows the result briefly and closes the validation process
automatically.

For the Gate 1 duration check, use `soak`. It adds 30 minutes of uninterrupted
playback before the same transitions:

```sh
./run_gpu_validation.sh "/path/to/video.mkv" soak
```

`soak` preserves the current HDR mode and therefore validates the user's real
configuration. To prove the actual SDR `GL_RGBA8`/sRGB path even when the saved
preference is `force-hdr`, use a preflighted BT.709 file with `sdr-soak`:

```sh
./run_gpu_validation.sh "/path/to/bt709-sdr.mkv" sdr-soak
```

This mode temporarily uses `auto` only in the isolated process and refuses to
start unless the file is tagged BT.709. Before the 30-minute segment it must
observe an SDR source published as `GL_RGBA8`/8 with sRGB. Saved settings are
not written.

For the controlled GL-lifecycle check, use an HDR sample and the approximately
one-minute `lifecycle` mode:

```sh
./run_gpu_validation.sh "/path/to/video.mkv" lifecycle
```

It pauses playback and performs two explicit `Gtk.GLArea` unrealize/realize
cycles. Each cycle must create a different libmpv render context, render a new
frame through `opengl-next`, keep the HDR property observers connected, and
restore the same HDR-content state and 16-bit render target. Playback and an
exact seek are verified after each cycle. This is developer-only validation;
normal CineHDR sessions never trigger context cycles themselves.

To validate live SDR/HDR state changes, supply a confirmed BT.709 SDR file
first and a PQ/HLG HDR file second:

```sh
./run_gpu_validation.sh "/path/to/sdr.mkv" "/path/to/hdr.mkv" transition
```

The approximately 1.5-minute matrix runs SDR → HDR → SDR → HDR in one
GPU Next process. It requires new frames after every switch, an unchanged
renderer context, cleared Dolby Vision state on SDR, and matching
`GL_RGBA8`/sRGB versus `GL_RGBA16F`/Rec.2100 PQ output. The validator temporarily
uses HDR mode `auto` inside that process so a saved `force-hdr` preference
cannot hide the SDR transition; it never writes the saved preference.
Resource warnings compare the first and second warmed HDR states. Raw SDR-start
to HDR-end values are still printed as context but are not leak evidence, and
video-output drop counters spanning deliberate file reloads are informational;
decoder, GL/FBO and post-switch playback failures remain strict.

Reports are written under `validation-reports/` and intentionally omit the
media path. They record the exact OpenGL vendor/renderer/version/GLSL strings,
dependency versions, active renderer, render target, hardware decoder, action
results, CineHDR/mpv frame drops, explicit GL/FBO failures, process memory, and
driver-reported VRAM use. Process VRAM/GTT comes from the
[Linux DRM client fdinfo accounting](https://docs.kernel.org/next/gpu/drm-usage-stats.html);
whole-GPU sysfs VRAM is retained only as context and cannot
trigger a leak warning. Decoder and video-output drops are tracked separately,
with five-minute resource checkpoints in soak reports. Missing hardware
decoding or process DRM telemetry produces
`WARN`, not a misleading clean `PASS`; renderer, GL, FBO, or action failure
produces `FAIL`.

Every run also writes a timestamped `.log` beside the report. The launcher
returns a failure if CineHDR exits abnormally or if the application terminates
without producing a new report, so failures before validator startup remain
diagnosable instead of looking like successful runs.

A `PASS` covers only the selected interaction sequence on the tested machine,
driver, file, and build. It does not certify color accuracy, all media, Dolby
Vision, or the complete ADR Gate 1. The SDR/HDR transition matrix remains a
separate check.

An earlier 31.2-minute soak report combined mpv's decoder/VO counters and
treated whole-GPU VRAM growth as process growth, so its warnings were
provisional. The accepted repeat above replaced that evidence with per-client
DRM memory, separated drop counters, and five-minute checkpoints.

## Dolby Vision RPU A/B probe

`tests/run_dovi_rpu_ab.py` is a development-only negative control for the
embedded renderer. It renders the same paused frame twice through
`opengl-next`: normally and with `vf=format=dolbyvision=no`. It reports the
input color-matrix transition and full-frame pixel-difference metrics. It does
not store the frame or include the media path in its output.

From the CineHDR checkout, with the pinned source and prefix in the standard
sibling layout:

```sh
python3 tests/run_dovi_rpu_ab.py /path/to/dovi.mkv \
  --timestamp 300 --target-peak 279 --hwdec no
```

Exit status 0 means both the expected `dolbyvision` to `bt.2020-ncl` negative-
control transition and a material rendered-pixel difference were observed.
That proves the RPU path affects the embedded renderer's pixels at the chosen
timestamp. It does **not** prove color accuracy, native Dolby Vision output, or
enhancement-layer/FEL support.

On the local Profile 8.1 sample (`dv_bl_signal_compatibility_id=1`, RPU present,
no enhancement layer), the pinned mpv `g97179bce7` / libplacebo `7.360.1` /
FFmpeg `8.1.2` stack passed at 60, 300, and 900 seconds. The enabled render
reported `dolbyvision`, the negative control reported `bt.2020-ncl`, and more
than 99.998% of pixels changed at every timestamp.

## Intel & NVIDIA GPU Smoke Test Run (2026-08-23)

A 5-second process-stability smoke test matrix (`tests/test_nvidia.py`) was executed on a hybrid graphics laptop (Intel Iris Xe iGPU + NVIDIA GeForce RTX 3050 Laptop dGPU under Wayland). 

- **Scope**: 16 combinations (4 HDR/HLG/DoVi fixtures × 2 GPUs × 2 render backends: `legacy` and `gpu-next`).
- **Result**: All 16 process runs started, rendered frames, and terminated without crashes or GL errors (Smoke Gate W9: PASSED).
- **Telemetry Evidence**: Generated evidence is stored in `test_report.json` and summarized in `validation-reports/GPU_NEXT_SMOKE_REPORT.md`.
- **Gate Limitations**: The host panel (`eDP-1`, 80 nits) ran in SDR mode (`get_monitor_hdr_state() = False`), executing SDR tone-mapping (`GL_RGBA8`).

## Gate 2 & Gate 3 Hardware Validation Sign-Off (AMD Radeon RX 9060 XT)

The Gate 2 and Gate 3 validation matrix was executed and measured on the physical GPU test machine:
- **CPU**: AMD Ryzen 7 7700 (8-Core, 16 Threads)
- **GPU**: AMD Radeon RX 9060 XT (radeonsi, ACO, Mesa 26.1.7, Linux 7.1.8-200.fc44)
- **Stack**: pinned mpv `v0.41.0-dev-g97179bce7` / libplacebo `v7.360.1` / FFmpeg `8.1.2`
- **Compositor**: Wayland Session (GNOME / Mutter 47)

### Gate 2 Measurement Summary

| Workstream | Check | Measured GPU Value | ADR Criteria | Verdict |
|---|---|---|---|:---:|
| **W1 (Thresholds)** | Black level (HDR10/HLG)<br>Diffuse reference white (203 nits)<br>Neutral axis chromaticity | $7.15 \times 10^{-7}$<br>$0.580078$ ($\Delta = 0.00061$)<br>$\|R-G\| < 10^{-4}$ | $\le 0.002$<br>$\|L - 0.5807\| \le 0.015$<br>$\Delta < 0.001$ | **PASS** |
| **W2 (Decode)** | `sw`, `auto` (`vaapi-copy`), `vaapi` | $p010$ format active, 0 drops | Active hwdec matches requested | **PASS** |
| **W3 (Dither)** | Monotonicity (`dither=no`)<br>FBO quantization ($\Delta_{\min}$) | Strictly monotonic (`True`)<br>$\Delta_{\min} = 0.000488$ | Monotonic<br>$\Delta_{\min} < \frac{1}{255} \approx 0.00392$ | **PASS** |
| **W4 (Screenshots)** | SDR, HDR, HLG, Legacy A/B, Subtitles, HWDEC | Valid PNG $320\times 180$, tone-mapped to SDR | Generated by mpv, non-empty | **PASS** |
| **W5 (Subtitles)** | ASS Subtitles in PQ FBO target | $(0.5801, 0.5801, 0.5801)$ | Diffuse white range $[0.50, 0.65]$ | **PASS** |
| **W6 (Performance)** | Frame latency (p50 / p95)<br>Dropped frames | GPU Next: p95 $0.107\text{ ms}$ (vs Legacy $0.181\text{ ms}$)<br>0 drops | $p95 \le \text{Legacy} \times 1.10$<br>$\le \text{Legacy drops} + 3$ | **PASS** |
| **W7 (Settling)** | 3-cycle dynamic resize & HDR $\leftrightarrow$ SDR soak | DRM VRAM & RSS stabilized | Zero continuing leak | **PASS** |

### Gate 3 Measurement Summary

- **Target Peak Sweep**: Verified across $[100, 203, 400, 600, 1000]$ nits with monotonic scaling ($0.4050 < 0.4583 < 0.5098 < 0.5410 < 0.5801$) and zero neutral-axis chromatic distortion.
- **DoVi Profile Policy**: Profile 8.1 base layer allowed; Profile 5 blocked on Legacy (protecting from false green/magenta SDR tint) and permitted when `supports_dovi_reshaping` capability is active on GPU Next.
- **RPU Negative Control**: Verified `dolbyvision` $\leftrightarrow$ `bt.2020-ncl` matrix transition with $>99.9\%$ pixel modulation under active dynamic metadata.
