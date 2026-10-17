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
cd "$(git rev-parse --show-toplevel)"

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
vfunc, explicitly allocates its sole `Gtk.GLArea` child, tracks logical width,
height and scale factor, and coalesces an explicit Render API redraw only after
a texture exists. The decision helper and the allocation callback are executed
in unit tests, including child allocation and the forced-redraw request. A
paused resize can therefore redraw the previous frame into a newly sized FBO
without changing media, context, or renderer. The settling evidence revision is
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

## Intel/NVIDIA smoke and Gate 2/3 status correction (2026-08-25)

The public 2026-08-23 supplemental summaries overclaimed their retained
evidence and are withdrawn as gate sign-off. The linked smoke JSON contains
eight legacy process/vendor observations and eight `STUB_UNSUPPORTED` GPU Next
runs. Its PASS predicate did not require `first_frame_render`, expected HDR
telemetry, or absence of file-open warnings, so those eight labels are
provisional smoke observations rather than proof that every fixture rendered.

The AMD measurements quoted by the removed summary have no committed
machine-readable Gate 2/3 reports and do not satisfy the complete ADR-0001
criteria. In particular, the short offscreen performance microbenchmark is not
the required ten-minute presented-frame comparison, file existence is not a
screenshot pixel oracle, the Profile 5 policy check is a mock rather than real
media evidence, and no accepted post-fix resource-settling report exists.

Current aggregate status:

- Gate 1 remains conditionally accepted with its recorded WARN; it is not color
  or Dolby Vision certification.
- Gate 2 is `OPEN`. Accepted FBO, VAAPI-copy, and GDK-publication rows remain
  useful independent evidence. Thresholds, dithering/workflow oracles,
  comparable performance, and resource settling require correction or evidence.
- Gate 2W cross-compositor aggregate is `CONDITIONAL`: the verified GNOME Shell 50.5
  composite-window domain is `CLOSED` on Candidate v14, while the KDE/KWin domain
  remains `UNAVAILABLE` on its previously tested stack. A single Run 7 launcher
  session passed all four SDR/HDR GDK publication states and Wayland
  ready -> set_image_description -> commit on the composite window for separate
  legacy and gpu-next processes, with three FBO pixel phases per backend.
  The sanitized [Run 7 decision](../validation-reports/gate2w-run7-adjudication-reconciled-mesa-20261011.json)
  records the evidence hashes and corrects the unmeasured Mesa version. Physical
  display output, direct scanout, and per-frame presentation remain unverified.
- Gate 3 is `OPEN`; Profile 5 remains disabled and Dolby Vision wording stays
  conservative.

All future hardware work follows
`docs/adr/0006-bounded-validation-governance.md`. The Intel/NVIDIA laptop agent
may execute an immutable test hand-off as an Operator, but the primary
Architect retains evidence interpretation and gate acceptance.

### Replacement Intel/NVIDIA smoke oracle

The local `cinehdr-vendor-smoke-v2` implementation replaces the historical
process-start predicate without reusing its results. A new path-safe
`Rendered first media frame` marker is emitted only when mpv exposes the loaded
path, positive video dimensions, and a non-negative media time during a Render
API call. The runner additionally requires the exact requested/active API with
no fallback, the pinned mpv/libplacebo/FFmpeg identities, the selected GL GPU,
pinned fixture content, matching HDR/Profile 8 telemetry, the full observation
window, and no file-open/application error.

Preflight now fails instead of silently skipping missing media. Reports omit
absolute media paths, retain raw-log hashes, and state their non-claims. One
command is restricted to a single GPU family, no more than two renderer
processes and no more than two fixture rows. Intel and NVIDIA both use the same
installed custom Flatpak; NVIDIA adds explicit PRIME selection rather than a
different native dependency stack.

Unit and source-contract tests pass locally. Real Intel/NVIDIA execution is
`NOT_RUN`: the current changes must first receive an exact source commit and a
matching rebuilt Flatpak commit. The later external hand-off will start with
the single HDR10 fixture, not recreate the old 16-process matrix. See
`docs/GPU_VENDOR_SMOKE_VALIDATION.md`.

## GNOME Wayland color-management trace capture (2026-09-24)

Following the offline trace parser correction for Wayland registry lifetimes in commit `e41fbeb`,
a single-shot Wayland client trace was captured on target hardware running GNOME
using the preflighted `validation-reports/capture_wayland_trace.sh` script
(SHA-256 `7d7b152dc403445a9e064aaa938653ee17bb89a30ee12223b6ce860101332f67`).

- **Version and Provenance**:
  - Source checkout HEAD: `e41fbeb1dad3b1b463c40030efba587f2341bfb7` (`codex/gpu-next-diagnostics`).
  - Installed Flatpak candidate: app commit `8c6c861c4832346c611f95d8d1edbd3982c5c69a59dcdf114b144eca9982fc74`
    (built from source commit `4831fefb4f7fe8df74462286b233b39eb579cae3`),
    runtime `b1935f7a673108616d4fd84564f15cefc9f637481472a8d4d2f0945375d41f4b`.
- **Protocol Submission Trace**:
  - Flatpak instance `248765847` was traced for 60 seconds (exit 124 timeout;
    `2026-09-24T13:29:47.522831Z` to `2026-09-24T13:30:47.526506Z`).
  - The offline parser (`tests/parse_wayland_color_trace.py`, SHA-256 `09775ed521e0810d071950982f1dcbe4eae01b5fd52dc340df85d9d1512c9748`)
    evaluated `wayland-client.raw.log` (SHA-256 `ba775d0a4ba212168ec6e07dea1614a6ce0d61913a2aa251812fa497c90b3578`)
    and reported status `PASS` (`wayland-trace-validation.json`, SHA-256 `bff2c74d58f1f7acd2fb4c75062368db8f27dbe314c5d8d542d040002d3f4255`).
  - The client bound `wp_color_manager_v1` (id 32), created an image description that received
    the `ready` event (id 51), and executed `set_image_description` followed by `commit` on
    toplevel surface `wl_surface#61` (`io.github.rusmikev.CineHDR`).
- **Evidence Gap and Adjudication**:
  - The capture session raw client log contains no stdout/stderr records of renderer activation.
  - No contemporaneous Copy Report from Video Output Diagnostics was recorded within the capture window.
  - Active API and Session status for this trace capture remain `Unknown`.
  - The separate earlier session Copy Report (12:54:34 UTC, instance 2622755033) certified `opengl-next`
    only for its respective instance; per ADR-0006, separate session observations cannot be spliced together.
  - The 1514 non-null buffer attaches logged on `wl_surface#61` are client-side protocol calls,
    not evidence of frames scanned out or presented on physical display.
  - Instance `248765847` was confirmed absent in `flatpak ps` output at `13:33:47 UTC` (`instance_absence_check.txt` -> `CONFIRMED_ABSENT`).
  - `KILL_TIMEOUT` in `cleanup_status.txt` was a script misclassification of the non-zero exit from `flatpak kill` (`ошибка: 248765847 не запущен`).
  - The exact termination mechanism remains unknown.
  - **Gate Status**: Gate 2W remains `OPEN`. The row requiring simultaneous proof of active `opengl-next`
    and Wayland color-management protocol submission is placed on `HOLD` ("нет свидетельства из той же сессии").
    Previously accepted independent rows remain unaffected.
  - **Hardware Budget**: The 2-attempt execution budget for GNOME Wayland trace capture is exhausted.
    A third run requires all ADR-0006 exception conditions: independent review of a proposal
    and both failures, an ADR/row revision, local regression evidence, a falsifiable brief,
    and explicit user approval. Reviewer eligibility follows `docs/MODEL_POLICY.md`;
    the 2026-09-25 routing revision does not reset this exhausted attempt budget.
- **Local Artifacts**:
  Retained in `validation-reports/wayland-trace-MgsoJA/`:
  - `instance.txt` (SHA-256 `a3778c357879eeefd4caf4c4cfe488abbef88022977f761ed60075f21330db1f`)
  - `instance_info` (SHA-256 `816d5268ab762b137fb5bf8a7f1cacdbe55ef84dbdda54df953c120bc23ba435`)
  - `sampler_status.txt` (SHA-256 `16a5d7ca2e1613a570f97007a146205c9a1e522e1459d30cbc65b87343a2ff5b`)
  - `start_time.txt` (SHA-256 `1faec2f1814afce364555f2925918b2e854a4379c8e77f8d57cfdf6584beccb6`)
  - `end_time.txt` (SHA-256 `e888c0ad85bc488853b9540c9f880c574a90a18e8ea2c2b0e4621c30f94db856`)
  - `exit_code.txt` (SHA-256 `ca2ebdf97d7469496b1f4b78958f9dc8447efdcb623953fee7b6996b762f6fff`)
  - `cleanup_status.txt` (SHA-256 `fb53801bad2f74ac95374b4c3663d8100f7d24030ee0ebfa3e7510bd83aa4c05`)
  - `instance_absence_check.txt` (SHA-256 `3471b85a1a3051eac4c9e4337536811779a6c200ba9fd8710a92cc4d85e436e5`)
  - `wayland-trace-validation.json` (SHA-256 `bff2c74d58f1f7acd2fb4c75062368db8f27dbe314c5d8d542d040002d3f4255`)
  - `session_facts.json` (SHA-256 `cbfd57433c9fdaa93d1b6e62d95b990e5988868fe4c6bc891ef751f07a581908`)
  - `wayland-client.raw.log` (SHA-256 `ba775d0a4ba212168ec6e07dea1614a6ce0d61913a2aa251812fa497c90b3578`)
  Raw logs and private media paths remain strictly uncommitted.

### Attempt 3 (2026-09-26, Candidate v4): Narrow row acceptance

Following independent review of the two prior attempt failures, an ADR-0006
falsifiable execution brief was approved by the user for an exceptional third run
under GNOME on target hardware.

- **Candidate v4 Identity and Provenance**:
  - Source checkout base: commit `9010e5a4a473aa90ae35ef5bb22b27dacf373d6e`.
  - Candidate Flatpak bundle: `validation-reports/candidate-v4/io.github.rusmikev.CineHDR-candidate-v4.flatpak`
    (SHA-256 `54f71856ea224f85834dfb15d717d76ff4136288c7493d0d4cab0e12fcf439f2`).
  - Installed system Flatpak app commit: `8ed86c0b54694b75d1aaa9be75956d1d3a9d075901d90695e4e0d3d144970813`.
  - Installed runtime commit: `b1935f7a673108616d4fd84564f15cefc9f637481472a8d4d2f0945375d41f4b` (`org.gnome.Platform/x86_64/50`).
  - Target discrete GPU: `AMD Radeon RX 9060 XT (radeonsi, gfx1200, ACO, DRM 3.64, 7.2.7-200.fc44.x86_64)`.
  - Mesa / GL version: `OpenGL ES 3.2 Mesa 26.2.2 (git-3281a69a8b)`.
- **Target Hardware Execution and Telemetry**:
  - Script: `validation-reports/capture_wayland_trace_attempt3_corrected.sh`
    (SHA-256 `1139ef43bf88aa5dffad0e522e01fc4f9e2048b899fad2c465fbbb556adac3e6`).
  - Flatpak instance ID: `2470237098`.
  - Monitored session window: start `2026-09-26T07:47:04.757041Z`, end `2026-09-26T07:47:16.897379Z`.
  - Stop reason: `FACTS_VERIFIED` (controller received verified client HDR facts and Wayland protocol sequence).
  - Script exit code: `0`; process exit code: `130` (graceful SIGINT termination after facts acquisition).
  - Autonomous diagnostics export: `first-media-frame-confirmed` triggered at `2026-09-26T07:47:15.715399+00:00`
    (`video_output_diagnostics.json`, SHA-256 `164e8bca3f1004f17fdbb2079338c37d57669abea7d70427c4d0f9e14af6f9b7`).
    - Active API: `opengl-next`.
    - Session status: `active`.
    - Active decoder: `vaapi-copy`.
    - Render target: `GL_RGBA16F` (depth: 16).
    - GDK Color State: `Rec.2100 PQ (published texture)`.
    - Evidence status: `CONFIRMED` (gaps: `[]`).
  - Wayland protocol trace validation: offline parser evaluated `wayland-client.raw.log`
    (SHA-256 `2e9d5de1a514986d1cbcb88d0b6d6312725e44e065e7fae7b77f40c3b77b107c`)
    reporting status `PASS` (`wayland-trace-validation.json`, SHA-256 `fa5d05e219929be2637131a8a7b076f358ce9f996454bce35eebd1af66cef821`).
    - Protocol sequence verified on toplevel surface `wl_surface#61` (`set_image_description` at index 953, `commit` at index 968).
    - Surface application ownership verified: `io.github.rusmikev.CineHDR`.
    - Total attaches on surface: 5 (4 post-description).
- **Process Teardown and Cleanup Observation**:
  - `cleanup_status.txt` recorded raw status `FAILED_STILL_RUNNING` due to polling `flatpak ps` within 0.2s of `flatpak kill` while container teardown was underway.
  - Independent subsequent check confirmed instance `2470237098` was completely absent in `flatpak ps` at `2026-09-26T07:50:18.675659+00:00`.
  - The exact cause of the cleanup delay remains unproven without dedicated teardown profiling.
- **Evidence Adjudication and Gate Status**:
  - Narrow row status: `PASS` (`session_facts.json`, SHA-256 `ca73ebec0e1268bc3a64a719cbd9a818f4c8c276616a05efce77c4f75ecea725`).
    Simultaneous proof of active `opengl-next` renderer and Wayland color-management protocol submission within the exact same session is confirmed.
  - Gate 2W remains `OPEN`:
    - Explicit non-claims (boundaries of client protocol evidence, not added blockers):
      - Physical display output (display photometry / photon measurement) is not certified by protocol trace or client texture.
      - Compositor transformation / direct scanout / zero-copy presentation is not proven.
      - Per-frame client-to-compositor presentation linkage is not proven.
      - Pixel and color accuracy under Wayland compositor remains a separate verification track.
    - Missing evidence for the accepted Gate 2W matrix: passing GDK publication
      reports for SDR -> HDR -> SDR -> HDR linked to protocol captures in separate
      legacy and GPU Next processes on the GNOME stack. The earlier legacy GNOME
      trace already contains the protocol chain and a point-in-time `opengl`
      Copy Report; it lacks the publication matrix, just as this GPU Next
      first-frame capture does. Repeating an isolated first-frame trace cannot
      fill that gap. The earlier accepted GDK publication matrix remains separate
      evidence on its original stack; hardware attempt history is unchanged.
    - KDE/KWin remains `UNAVAILABLE` pending upstream support. The prior GNOME
      trace does not establish KDE's capability or submission behavior.
    - Resize, fullscreen, teardown, and a forced SDR/PQ surface-description
      alternation are outside this Gate 2W row's acceptance conditions. GTK may
      transform textures into its preferred surface encoding. Existing Gate 1/2
      lifecycle and publication rows retain their separate status.
- **Local Artifacts**:
  Retained in `validation-reports/wayland-trace-Z4GghM/`:
  - `session_facts.json` (SHA-256 `ca73ebec0e1268bc3a64a719cbd9a818f4c8c276616a05efce77c4f75ecea725`)
  - `wayland-trace-validation.json` (SHA-256 `fa5d05e219929be2637131a8a7b076f358ce9f996454bce35eebd1af66cef821`)
  - `video_output_diagnostics.json` (SHA-256 `164e8bca3f1004f17fdbb2079338c37d57669abea7d70427c4d0f9e14af6f9b7`)
  - `video_output_diagnostics.txt` (SHA-256 `32c46f79b490d76f00b8011d41b8ea6976e2e6a66b5740ba6ef2c98758472ffd`)
  - `wayland-client.raw.log` (SHA-256 `2e9d5de1a514986d1cbcb88d0b6d6312725e44e065e7fae7b77f40c3b77b107c`)
  - `instance.txt` (SHA-256 `ee9044c5c0084dbdb9c6b613acce2ad6e3b88aefbf5afda81bc73756dbd3f5dd`)
  - `instance_info` (SHA-256 `e84509710af804b367da4bd8f1e8f62b5f0f076fcfce35d98ac3ae4ccc46b349`)
  - `exit_code.txt` (SHA-256 `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa`)
  - `process_exit_code.txt` (SHA-256 `f5bde7eb9f6c71611dc5726e8aca3eb4eba3e386da49e0a4ed5c295a90a73a0d`)
  - `stop_reason.txt` (SHA-256 `33c555218a04b0414dffa51441fd217889f235272d0280a8b36c1a4b1b293d4d`)
  - `cleanup_status.txt` (SHA-256 `d7a91e51b28326614450b962eefeeb5c108ebc4ceea94e02dae9286b5e96c0c6`)
  - `SHA256SUMS` (SHA-256 `f2439c5823dcd6e20c9b655a7212457ecd61f9d2db56ef51c9fc56db694be111`)

### W4 screenshot matrix evidence boundary (2026-09-26)

The retained software-only W4 report (`validation-reports/gate2-w4-software-20260926/`)
passed on Mesa llvmpipe with active `opengl-next`/legacy `opengl` as selected
and `hwdec-current=no`; `hdr_hwdec_auto` is `NOT_RUN`. Its five PNGs passed
decode and structural checks. For the subtitle image, the observed outside-ROI
count and maximum channel difference were both zero; the validator's actual
criterion permits a maximum difference of one decoded 8-bit value per outside
pixel (`changed_outside` counts differences greater than one). The 50-pixel
inside-ROI threshold is a presence heuristic. The screenshot black-floor and
neutral-axis limits of two decoded 8-bit values are W4 structural tolerances;
ADR-0005's analytical PQ bounds apply to offscreen FBO readback. PNG `cHRM`
records chromaticities/primaries, not PQ or HLG transfer. Source transfer and
requested target transfer come from probe metadata; the PNG transfer cannot be
certified from `cHRM` alone. Pillow decodes these 16-bit RGB PNGs to 8-bit RGB
for the current patch checks. This standalone EGL probe does not exercise the
CineHDR GTK playback or Wayland submission path.

The standalone `hdr_hwdec_auto` row is accepted as a narrow `PASS` from one
user-authorized target-AMD-GPU run on 2026-09-26 (09:56:40–09:56:41 UTC, exit 0),
retained in `validation-reports/gate2-w4-hardware-20260926-095640/`. The
existing HEVC Main10 HDR fixture matched its accepted SHA-256. The raw probe
reported requested `auto`, active `vaapi-copy`, actual `opengl-next`, the target
GPU GL renderer, BT.2020/PQ input and target, and a complete `GL_RGBA16F`/16
FBO. Its 320×180, 16-bit RGB screenshot passed PNG decode and W4 structural
patch checks. CUDA loader errors in stderr occurred during auto probing before
mpv reported active VAAPI-copy decoding; they remain retained diagnostic text.

At the time of that run, the W4 runner's non-software check did not require an
active hardware decoder, so its aggregate PASS alone was insufficient. Independent adjudication
checked the raw decoder, API, target renderer, loaded stack, fixture provenance,
and screenshot. The GL renderer and `hwdec-current` do not identify the physical
decode GPU. Candidate v4 was not executed in this run. This standalone result
does not validate CineHDR GTK playback, Wayland submission, color accuracy,
direct scanout, or all of Gate 2/2W.
The software-only report above correctly retains `NOT_RUN` for its own hardware
cell; it is a distinct observation.

### CineHDR UI screenshot save observation (2026-09-27)

In one user-operated CineHDR session on the installed v4 app/runtime, pressing
`s` created `cine_0001.jpg` in the previously empty screenshot directory at
2026-09-27T02:22:44.816449Z. The 3840×2160, 8-bit RGB JPEG (388047 bytes;
SHA-256 `c5cceb24186f75bb853f2d892daf21c358b47c40bdea452803159edfc2f38954`)
passed Pillow verification/loading and visibly contained a video frame. This
accepts the UI save workflow. A fresh Copy Report at 02:28:57.795574Z
reported active `opengl-next`, session `active`, `vaapi-copy`, a target AMD GPU,
`GL_RGBA16F`/16, and a published Rec.2100 PQ GDK color state. A 02:30:23Z
instance check confirmed the same CineHDR process and app/runtime commits as
the 02:22:30Z screenshot session, supporting same-session renderer correlation;
it does not link the report to the saved frame instrumentally. The earlier
02:16:56Z Copy Report belonged to a different process. HDR/color accuracy,
subtitle inclusion, physical HDR output, and Gate 2/2W status are unchanged.
Evidence is retained in `validation-reports/ui-screenshot-20260927-022244/`.

### W4 decoder evidence correction and GNOME next step (2026-09-27)

`hdr_hwdec_auto` now requires both the recorded request `auto` and a concrete
active hardware backend from the pinned mpv interface. Software fallback,
missing/unknown values, request aliases and malformed values fail that cell
and the aggregate; both requested and active values remain in the report.
Other screenshot cells still permit software decoding, and `--software-only`
keeps the hardware cell `NOT_RUN`. The 32-test pixel-pipeline suite passed.
Offline replay accepts the retained real `vaapi-copy` probe and rejects a
labelled synthetic mutation to `no`; it does not create new hardware evidence.
The patch, regression logs and replay are retained in
`validation-reports/w4-hwdec-oracle-20260927/`.

For Gate 2W, `validation-reports/wayland-legacy-offline-reconciliation-20260927.json`
links the existing legacy trace and Copy Report without replacing the missing
publication report. The next discriminating GNOME check must combine the
publication transitions and protocol capture, subject to ADR-0006 attempt
review. No new GPU/Wayland execution, acceptance-row closure or attempt-budget
reset was made during this correction.

### Gate 2W Run 4 Rerun Brief (ADR-0006 Governance, 2026-10-04)

- **Row ID & Attempt Number**: Gate 2W narrow row **"Client-side color-management submission for composite window"** (Wayland color-management protocol submission and GDK publication matrix correlation on GNOME composite path), Attempt 4 (exceptional rerun following Attempt 1, Attempt 2, and Attempt 3 under ADR-0006).
- **Target Stack & Build Identity**:
  - Application: `io.github.rusmikev.CineHDR`
  - Current installed candidate: Candidate v4 (app commit `8ed86c0b54694b75d1aaa9be75956d1d3a9d075901d90695e4e0d3d144970813`, runtime commit `0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792`).
  - Target Candidate for Run 4: **Candidate v5** (app commit `3f2897a04bb0dffc911818d05923b1cfaf05ed81f7692bb50e4e028923bef123`, bundle SHA-256 `a09de0b1fa0342b1d86c0d3c656cb656bdf2f80fce792f76db485b8d643716d8`, packaged in `validation-reports/candidate-v5/io.github.rusmikev.CineHDR-candidate-v5.flatpak`, pending separate user installation decision).
  - Runtime ref: `org.gnome.Platform//50` (commit `0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792`).
  - Container `libmpv.so.2.5.0` SHA-256: `8c40fbc4b0a8bb239336872d981b7a6df5bf4d2dc339530e478679902378a5fb`.
  - Pinned component stack: mpv `v0.41.0-dev-g97179bce7`, libplacebo `7.360.1`, FFmpeg `8.1.2`.
  - Display server & target hardware: GNOME Shell 50.5 on Wayland, target AMD discrete GPU (no `/dev/dri` write access requested; sandbox device access only).
  - **Observed Composite Path Context (from raw trace `wayland-trace-Z4GghM`)**:
    In raw trace `wayland-trace-Z4GghM`, `wp_color_management_surface_v1` and buffer attaches were observed on the toplevel window surface (observed as `#61` in Attempt 3, dynamically matched by tracing `set_app_id("io.github.rusmikev.CineHDR")`), while subsurface `wl_surface#71` received no video buffers. This reflects GTK4's composite window path under GNOME Shell 50.5 (Mutter) with `org.gnome.Platform//50` (GTK 4.16+). It documents this specific composite path without asserting that all other GTK/GNOME paths (such as subsurface offload) are impossible. No new gate is created; this is evaluated as a narrow row within Gate 2W.
  - **Build Identity & Separation Policy**:
    Modifying `src/gpu_validation.py` to support exact container and checkout paths creates a new git commit SHA. Candidate v4 cannot remain the target identity for Run 4 because it contains the legacy validator that rejects container path `/app/lib/` (it is not claimed that a host runtime update prevents v4). Future Candidate v5 OSTree commit SHA will NOT be hardcoded into Python source code; it will be pinned externally in the validation launcher (`run_wayland_publication_validation.sh`) and validated against `$XDG_RUNTIME_DIR/.flatpak/<instance-id>/info`.
- **Retained Three-Attempt History & Failure Signatures**:
  1. *Attempt 1 (2026-09-24, `wayland-trace-20260924-133500`)*: Initial passive Wayland trace under GNOME. A contemporaneous user Copy Report exists with sample timestamp falling strictly within the trace capture interval (`validation-reports/wayland-legacy-offline-reconciliation-20260927.json`). Failure signature / limitation: lacks a complete dynamic GDK publication matrix from the same process.
  2. *Attempt 2 (2026-09-24, `wayland-trace-MgsoJA`)*: Single-shot scripted trace capture. Protocol parser PASS (`ready -> set_image_description -> commit` on surface #61), but contemporaneous Copy Report missing; renderer API and session status unknown. Two-run budget exhausted -> `HOLD` ("нет свидетельства из той же сессии").
  3. *Attempt 3 (2026-09-26, `wayland-trace-Z4GghM`, session facts SHA-256 `ca73ebec0e1268bc3a64a719cbd9a818f4c8c276616a05efce77c4f75ecea725`)*: Exceptional user-authorized run on Candidate v4 with autonomous diagnostics export. Accepted narrow row (first-frame `opengl-next` + `wp_color_management_surface_v1` submission on surface #61). Gate 2W overall remains OPEN: observation covered only a single first frame, lacking the full SDR -> HDR -> SDR -> HDR publication sequence and separate legacy backend process correlation.
- **The Single Material Change**:
  - Transition from single first-frame observation to an automated two-process sequence running both `legacy` (active `opengl`) and `gpu-next` (active `opengl-next`) in separate, clean Flatpak sandboxes, executing the complete 4-step publication sequence (`sdr -> hdr -> sdr -> hdr`), capturing simultaneous client Wayland protocol trace and GDK publication reports in the exact same process with verified container instance identity (`$XDG_RUNTIME_DIR/.flatpak/<inst>/info`).
- **Expected Discriminating Observation**:
  - Both backends produce:
    1. GDK publication report (`gate2-wayland-publication-${backend}-*.txt`) with `Overall result: PASS` across all 4 state transitions (`GL_RGBA8`/8 for SDR, `GL_RGBA16F`/16 for HDR).
    2. Wayland color trace report (`gate2-wayland-color-management-${backend}.json`) with status `PASS`, satisfying the binding ADR-0004 oracle: `ready` (or `ready2`) -> `set_image_description` (with perceptual intent) -> `commit` on the dynamically identified CineHDR window surface (`set_app_id`) in each backend process without protocol errors.
       *(Reconciliation with ADR-0004: ADR-0004 explicitly establishes that the surface description is not required to alternate between sRGB and PQ on the wire, because GTK may legally transform both texture states into a single compositor-preferred surface encoding. Stricter multi-phase surface alternation is separated into an exploratory sub-row so compliant composite GTK implementations are not falsely rejected.)*
    3. Provenance & Cleanup: Publication report verifies `Session ID`, `Flatpak instance ID`, and `Process ID` matching launcher and container metadata; process exit code 0; `cleanup_status.json` verifies clean termination of the test instance; machine-readable `session_facts.json` verifying `container_session_correlated: true` and `same_process_wire_correlated: false` (with wire-level PID transmission explicitly disclaimed).
       *(Note on cross-layer correlation: client protocol events prove client-side protocol submission only. Because GTK4 provides no shared monotonic timestamp or presentation-time marker tying an internal `GdkGLTexture` publication generation to a specific `wl_surface.commit`, the per-frame presentation correlation sub-row explicitly remains `OPEN`, never inferred `PASS`.)*
    4. Aggregate gate report (`gate2w_summary.json`) proving both backends completed with `PASS` in the same validation run (with Gate 2W aggregate remaining `OPEN` / `PARTIAL_NOT_CLOSED`).
- **Command & Stop Condition**:
  - Command: `./run_wayland_publication_validation.sh --backend all --protocol-trace`
  - Prerequisites (Why Run 4 cannot be executed yet):
    - Independent review and ADR acceptance of this composite window governance amendment.
    - Implementation of exact path validation in `src/gpu_validation.py` (completed offline with unit tests).
    - Implementation and offline unit testing of the primary composite window oracle (`validate_composite_window_trace`) and its integration into `evaluate_session_results`, with positive tests for Attempt 3 composite path and negative controls (wrong surface, order inversion, missing manager capabilities, protocol errors) (completed offline).
    - Independent code and test review of the composite window oracle implementation and test coverage.
    - Local regression testing confirming rejection of old Candidate v4 and false PASS traces (completed).
    - Build and installation of Candidate v5 with updated `expected_app_commit`.
    - Both fixtures verified: HDR (`bc437162b00b...`) and SDR (`41393a0ee9dd...`).
    - Explicit user authorization under ADR-0006.
  - Duration & timeout: ~20–30s per backend, hard timeout 60s per backend.
  - Stop condition: stops automatically when publication report is emitted, or on timeout/SIGINT/SIGTERM; cleans up test instance only, strictly protecting user instances.
- **Explicit Non-Claims**:
  - Display photometry (photon emission measured on physical panel) is NOT certified and remains `OPEN`.
  - Direct scanout / zero-copy compositor bypass is NOT claimed and remains `OPEN`.
  - Per-frame client-to-compositor presentation correlation is NOT claimed and remains `OPEN`.
  - KDE/KWin support is NOT claimed (remains `UNAVAILABLE` pending upstream).
- **Next Action**:
  - On PASS: Advance Gate 2W towards closure on GNOME; update documentation and evidence matrix.
  - On FAIL: Place row on strict `HOLD`; retain all raw artifacts; no further hardware rerun without root-cause review.

### Design Resolution: Five Reviewer Blockers

1. **Separation of Trusted Paths and Deployment Identity**:
   - Host `libmpv` is verified canonically relative to the checkout root: `repo_root.parent / "mpv-gpu-next-prefix" / "lib64" / "libmpv.so.2.5.0"` (or symlink `libmpv.so.2`), matching `meson setup build --prefix="$(pwd)/../mpv-gpu-next-prefix"`.
   - Container `libmpv` is verified inside a verified container context (readable `/.flatpak-info` matching `[Application] name=io.github.rusmikev.CineHDR`, read-only `/app` mount) against exact canonical path `/app/lib/libmpv.so.2.5.0` (or symlink `/app/lib/libmpv.so.2`) via `os.path.realpath`. Suffix matching (`.endswith()`) is prohibited.
   - Pinned OSTree `app-commit` and `runtime-commit` are enforced externally by the launcher via `.flatpak/<instance-id>/info`, never hardcoded into CineHDR's Python source code.
2. **Promotion and Staging Sequence (Run 4 Preconditions)**:
   - Step 1: Independent ADR amendment acceptance.
   - Step 2: Implementation of exact path checking and container context validation in `src/gpu_validation.py`.
   - Step 3: Implementation and offline unit testing of primary composite window oracle (`validate_composite_window_trace`) and integration into `evaluate_session_results`, along with positive tests for Attempt 3 composite path and negative controls.
   - Step 4: Independent code and test review of composite window oracle implementation and test coverage.
   - Step 5: Local negative and positive unit/regression testing (reproducing rejection of Candidate v4's legacy check and false traces).
   - Step 6: Build and local installation of Flatpak Candidate v5.
   - Step 7: Pinning Candidate v5 commit SHA in launcher script and rerun brief.
   - Step 8: Separate explicit user approval for live hardware Run 4 under ADR-0006.
   - *(Note: Candidate v4 is rejected solely because its code contains the old validator that rejects `/app/lib/`; it is not claimed that a host runtime update prevents v4).*
3. **Phase-by-Phase Wayland Protocol and Surface Correlation (Reconciled with ADR-0004)**:
   - Dynamic surface identification: The window surface is dynamically identified from the trace via `xdg_wm_base.get_xdg_surface` -> `xdg_surface.get_toplevel` -> `xdg_toplevel.set_app_id("io.github.rusmikev.CineHDR")`, avoiding hardcoded surface IDs (such as `#61`).
   - Primary row vs stricter row separation:
     - Primary row: GDK publication verifies 4 states (`sdr -> hdr -> sdr -> hdr`), and Wayland protocol submission verifies the ADR-0004 oracle `ready -> set_image_description -> commit` on the dynamically identified window surface in each backend process without protocol errors. An unchanging / constant surface color descriptor across transitions does NOT fail the primary row.
     - Stricter extension row: Evaluates whether GTK dynamically switches surface descriptions between sRGB (`tf=9`) and PQ (`tf=11`) across the 4 phases on the wire, separated so as not to contradict ADR-0004 or falsely reject valid wide-gamut surface configurations.
   - Provenance: GDK publication report includes `Session ID`, `Flatpak instance ID`, and `Process ID`, matched strictly by `evaluate_session_results`. Verified as `container_session_correlated: true` and `same_process_wire_correlated: false`; wire-level PID embedding is not present in libwayland wire protocol and is explicitly disclaimed.
4. **Backend Row Status vs Aggregate Gate 2W Separation**:
   - Partial execution (`--backend legacy`) records `legacy/session_facts.json` only.
   - Aggregate Gate 2W summary (`gate2w_summary.json`) remains `PARTIAL_NOT_CLOSED` / `OPEN` and cannot declare Gate 2W closed without clean PASS on both `legacy` and `gpu-next`.
5. **Robust Teardown and User Instance Isolation**:
   - Trap `EXIT`, `INT`, `TERM`, `HUP` in launcher script.
   - Catalog pre-existing user Flatpak instances into immutable `user_instances` at launcher start; prohibit sending signals to any instance in that set.
   - Terminate only confirmed numeric test instance ID verified through `.flatpak/<id>/info`.
   - Fail-closed if `flatpak ps` fails during cleanup or instance polling, or if `cleanup_status.json` is missing or not `CLEAN`.
   - Record teardown telemetry (trigger, instance ID, protected instances, outcome) in `cleanup_status.json`.

### Gate 2W Run 5 proposal — GNOME composite window (2026-10-04; HOLD)

**Decision state:** the Run 5 row revision and bounded execution brief received independent `gpt-6-astra` high architecture review (`ACCEPT`, 2026-10-04) after one focused correction pass. This accepts the technical exception and retained-failure analysis, but does not authorize installation or execution. Gate 2W remains `OPEN`; the Run 4 multi-phase row remains `HOLD`. This is one continuation of the same GNOME attempt family, not a reset of its hardware budget. Separate explicit user approval under ADR-0006 is still required for the v6 installation and this one Run 5 hardware execution. The prior independent `ACCEPT` verdict for the *oracle correction* below covers code and tests only.

**Falsifiable hypothesis and Run 4 failure signature.** Run 4 on Candidate v5 executed only `legacy`, stopping at initial SDR publication after 6.9 s. Its report recorded an active `opengl` renderer, `GdkGLTexture` 1400×788 R8G8B8A8/sRGB, `vaapi-copy`, and libplacebo `v7.360.1 (v7.360.1)`, but returned `FAIL`: the validator expected 2240×1260 from integer GDK scale 2 and rejected the tagged libplacebo version. The actual renderer used cached fractional scale 1.25 (1120×630 → 1400×788 after Python rounding); no HDR phase or `gpu-next` process ran. The process exited 0, and `cleanup_status.json` says `CLEAN`. These facts establish an oracle false rejection at this first boundary; they do **not** establish that the unobserved later phases will pass. Raw evidence: `validation-reports/wayland-publication-20261004-182917-350221/legacy/` (`SHA256SUMS` retained), report SHA-256 `5a018fdb01f5245c69e38099e92466291ffba047502fbcd33eaa9a5986ccdb47`.

**Single coherent material correction.** Candidate v6 changes the publication oracle for the same first boundary: `expected_scaled_texture_dimensions()` now uses the widget's cached scale, rounding, size caps and minimum clamp; libplacebo identity is an exact allowlist of six 7.360.1 spellings, including the observed tagged string. It does not relax renderer/API, four-state publication, Wayland protocol, provenance or cleanup requirements. Local regressions reject the old 2240×1260 expectation, off-by-one dimensions and foreign/dirty libplacebo strings. Independent review of this correction: a retained local review artifact, SHA-256 `ca1a5ce87905fcfe988138451c1747b0e20491654ba0233e0819c37b80e10267`, verdict `ACCEPT` (code/test scope). Candidate v6 SDK Flatpak build completed with 13/13 Meson suites passing; host tests of the corrected oracle passed. No v6 hardware execution has occurred.

**Pinned target and rollback.** Candidate v6 bundle: `validation-reports/candidate-v6/io.github.rusmikev.CineHDR-candidate-v6.flatpak`, SHA-256 `b0a0e98d9eff079d6ec67155a04e696e68fe8f1a57169c094e32398b04571456`, OSTree app commit `279ea47b65b92a022f79696175d6f2f8d440a59db87e0c1532f129714c9074c1`, runtime `org.gnome.Platform//50` commit `0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792`, source snapshot tree `0982c5dc76e6a3bf797f39ba452db6452a4d2936`. `validation-reports/candidate-v6/SHA256SUMS` covers the bundle, patch, snapshot manifest and build log. Current installed app remains Candidate v5, commit `3f2897a04bb0dffc911818d05923b1cfaf05ed81f7692bb50e4e028923bef123`. Its rollback bundle is `validation-reports/candidate-v5/io.github.rusmikev.CineHDR-candidate-v5.flatpak`, SHA-256 `a09de0b1fa0342b1d86c0d3c656cb656bdf2f80fce792f76db485b8d643716d8`. The repo HEAD `9010e5a4a473aa90ae35ef5bb22b27dacf373d6e` is a base commit, **not** the v6 source identity; the candidate was built from an isolated snapshot of uncommitted work.

**Prerequisites, only after review and explicit approval.** On the GNOME Wayland session, verify both bundle hashes and Candidate v6 `SHA256SUMS`; confirm the HDR fixture SHA-256 `bc437162b00b6565ca486ecc0a67704b6f099ebccf03cf671b2a0f87e94e021d` and SDR fixture SHA-256 `41393a0ee9dd887a1f11d4b21946fef3d1f49e72127719eefd1ac40aa9d5e360`. Query `flatpak ps --columns=instance,application`; if any CineHDR user instance is present, stop and let the user close it normally. Do not kill it. Install v6 through the normal Polkit flow and verify `flatpak info --system --show-commit io.github.rusmikev.CineHDR` equals the full v6 commit and runtime equals the pinned commit. The launcher has historical “Candidate v5” display text, but its operative identity check compares `EXPECTED_APP_COMMIT` with the installed full commit; do not treat that display label as proof of identity. Run its `--dry-run` as a smoke check; note that dry-run reports fixture status but does not reject an unverified fixture, so verify both hashes separately. Pin `DRI_PRIME=pci-0000_03_00_0` for the RX 9060 XT at PCI 0000:03:00.0, the full runtime commit override, host launcher SHA-256 `37315fe8acd177aa91c68cb2e7bdd3d04fbd68879d5a0f51d66e3ca965c53d01`, and parser SHA-256 `dcea73a71891b9bf1f4d17c2756a739efcec5d3523ebc761cb3259fd2def211a`. The host launcher differs from the v6 source snapshot only by private artifact permissions and accurate aggregate reason text; the Flatpak app bundle is unchanged.

```bash
set -euo pipefail
umask 077
cd "$(git rev-parse --show-toplevel)"
(cd validation-reports/candidate-v6 && sha256sum -c SHA256SUMS)
printf '%s  %s\n' a09de0b1fa0342b1d86c0d3c656cb656bdf2f80fce792f76db485b8d643716d8 validation-reports/candidate-v5/io.github.rusmikev.CineHDR-candidate-v5.flatpak | sha256sum -c -
printf '%s  %s\n' 37315fe8acd177aa91c68cb2e7bdd3d04fbd68879d5a0f51d66e3ca965c53d01 run_wayland_publication_validation.sh | sha256sum -c -
printf '%s  %s\n' dcea73a71891b9bf1f4d17c2756a739efcec5d3523ebc761cb3259fd2def211a tests/parse_wayland_color_trace.py | sha256sum -c -
printf '%s  %s\n' 41393a0ee9dd887a1f11d4b21946fef3d1f49e72127719eefd1ac40aa9d5e360 validation-reports/fixtures/cinehdr-gate2-hevc-main10-yuv420p10-lossless-sdr.mp4 | sha256sum -c -
printf '%s  %s\n' bc437162b00b6565ca486ecc0a67704b6f099ebccf03cf671b2a0f87e94e021d validation-reports/fixtures/cinehdr-gate2-hevc-main10-yuv420p10-lossless-hdr.mp4 | sha256sum -c -
lspci -D | grep -F '0000:03:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Navi 44'
flatpak ps --columns=instance,application
active_apps="$(flatpak ps --columns=application)"
if printf '%s\n' "$active_apps" | grep -Fxq io.github.rusmikev.CineHDR; then echo 'Close the user CineHDR window first' >&2; exit 1; fi
pkexec flatpak install --system --reinstall -y validation-reports/candidate-v6/io.github.rusmikev.CineHDR-candidate-v6.flatpak
test "$(flatpak info --system --show-commit io.github.rusmikev.CineHDR)" = 279ea47b65b92a022f79696175d6f2f8d440a59db87e0c1532f129714c9074c1
test "$(flatpak info --system --show-commit org.gnome.Platform//50)" = 0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792
EXPECTED_APP_COMMIT=279ea47b65b92a022f79696175d6f2f8d440a59db87e0c1532f129714c9074c1 EXPECTED_RUNTIME_COMMIT=0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792 DRI_PRIME=pci-0000_03_00_0 ./run_wayland_publication_validation.sh --backend all --protocol-trace --dry-run
operator_dir="$(mktemp -d validation-reports/run5-operator.XXXXXX)"
: > "$operator_dir/pre_run.marker"
set +e
timeout --signal=TERM --kill-after=10s 180s env DRI_PRIME=pci-0000_03_00_0 EXPECTED_APP_COMMIT=279ea47b65b92a022f79696175d6f2f8d440a59db87e0c1532f129714c9074c1 EXPECTED_RUNTIME_COMMIT=0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792 ./run_wayland_publication_validation.sh --backend all --protocol-trace > "$operator_dir/launcher.stdout-stderr.log" 2>&1
run_rc=$?
flatpak ps --columns=instance,application > "$operator_dir/flatpak_ps_after.txt" 2>&1
ps_rc=$?
set -e
printf '%s\n' "$run_rc" > "$operator_dir/launcher.exit_code.txt"
printf '%s\n' "$ps_rc" > "$operator_dir/flatpak_ps_after.exit_code.txt"
while IFS= read -r -d '' run_dir; do
  while IFS= read -r -d '' backend_dir; do
    (cd "$backend_dir" && find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 -r sha256sum > SHA256SUMS)
  done < <(find "$run_dir" -mindepth 1 -maxdepth 1 -type d -print0)
  (cd "$run_dir" && find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 -r sha256sum > SHA256SUMS)
done < <(find validation-reports -maxdepth 1 -type d -name 'wayland-publication-*' -newer "$operator_dir/pre_run.marker" -print0)
(cd "$operator_dir" && sha256sum launcher.stdout-stderr.log launcher.exit_code.txt flatpak_ps_after.txt flatpak_ps_after.exit_code.txt > SHA256SUMS)
printf 'Operator artifacts: %s\nLauncher exit: %s; flatpak ps exit: %s\n' "$operator_dir" "$run_rc" "$ps_rc"
test "$run_rc" -eq 0
test "$ps_rc" -eq 0
```

The final command is **one** real-hardware execution, not an automatic retry. The launcher runs separate `legacy` then `gpu-next` processes, normally about 20–30 s per backend. Its internal 60 s monitor applies to each backend; the enclosing `timeout` caps the whole launcher at 180 s plus a 10 s forced-stop grace because internal waits are not themselves hard-bounded. Exit 124/137, a lingering verified test instance, or failed post-run `flatpak ps` is a failure requiring preserved evidence and manual review, never an inferred clean shutdown. It stops at the first failed backend; no unchanged rerun is allowed. If preflight, fixture, instance, installation or identity verification fails, stop before the final command. On any live failure, retain artifacts, keep the row on `HOLD`, and conduct root-cause review before proposing another run. Restore Candidate v5 with `pkexec flatpak install --system --reinstall -y validation-reports/candidate-v5/io.github.rusmikev.CineHDR-candidate-v5.flatpak` only after confirming its hash and no user CineHDR instance; verify its full app commit afterward.

**Discriminating result and required artifacts.** For **each** backend, the report must show active API `opengl`/`opengl-next` respectively and `Overall result: PASS` for SDR → HDR → SDR → HDR publication, including expected texture dimensions, formats and color-state equality. The primary composite-window protocol oracle must report `ready`/`ready2` → `set_image_description` → `wl_surface.commit` on the dynamically identified CineHDR toplevel surface, with no fatal protocol error. Require one verified Flatpak instance per backend, full app/runtime commit match, process exit 0, `cleanup_status.json` `CLEAN` with confirmed instance termination, and `container_session_correlated: true`; `same_process_wire_correlated` remains `false`. Retain each backend's raw `wayland-client.raw.log`, publication report, protocol JSON, `instance.txt`, `instance_info`, start/end times, process exit code, cleanup status, `session_facts.json`, plus aggregate `gate2w_summary.json` when both backends finish, in a private `validation-reports/wayland-publication-*` directory. The launcher does not generate `SHA256SUMS`; the command above captures its stdout/stderr and exit code regardless of PASS/FAIL, records post-run `flatpak ps`, and hashes every artifact actually present in each newly created backend directory, plus the aggregate and Operator logs. A missing artifact remains missing and fails acceptance. A nonzero or missing result at any boundary is a fail/unknown, not an inferred pass. If both backend rows pass, the aggregate report records `GATE2W_EVIDENCE_GATHERED` with `gate2w_closed: false` pending evidence adjudication. Physical HDR output, direct scanout and per-frame presentation remain unverified non-claims; they are not added acceptance criteria for this client-side row.

### Gate 2W Run 5 result — GNOME / Candidate v6 (2026-10-04)

Exactly one user-authorized hardware execution of the accepted Run 5 brief was performed on app commit `279ea47b65b92a022f79696175d6f2f8d440a59db87e0c1532f129714c9074c1` and runtime commit `0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792`. The command exited `1` after the `legacy` backend; `gpu-next` was **not run**. The test instance `4270724640` exited with process code `0`, cleanup `CLEAN`, and was absent in post-run `flatpak ps`. The enclosing timeout was not reached.

- **Observed, limited components:** Initial SDR publication passed on active `opengl`, `GdkGLTexture` 1400×788 R8G8B8A8/sRGB with `vaapi-copy`; this confirms that Candidate v6 no longer falsely rejects the fractional-scale dimensions or tagged libplacebo string at the Run 4 failure boundary. Offline analysis of the same raw Wayland trace found the primary composite-window client protocol sequence (`ready` at index 584 → `set_image_description` at 662 → `commit` at 678, toplevel `wl_surface#61`). That protocol-only observation does not override the failed publication prerequisite.
- **Failure signature:** The subsequent SDR→HDR switch produced an application `end-file` error for the HDR fixture. The published texture remained SDR, and the report marked the HDR step `FAIL` (`HDR output state did not follow the source in auto mode`; render target and texture format/color state did not match required HDR state). The HDR file existed, matched its pinned SHA-256, and decoded one frame with host FFmpeg; those checks do not establish why the Flatpak/libmpv process reported a file error. The exact mpv file-error code was not logged. Root cause remains `UNVERIFIED`.
- **Evidence and status:** Raw report `validation-reports/wayland-publication-20261004-202817-446169/legacy/gate2-wayland-publication-legacy-20261004-202830.txt` (SHA-256 `df697831740f519331c4bd623a02031e33f7f19934b540d26bd49abcfd0032b8`), raw trace SHA-256 `334092eed1a25419d50cb9f12d472c29cc7b88f881e98f74e24182dee95bfae3`, and sanitized machine-readable `validation-reports/run5-operator.VXrwLA/run5_observation.json` (SHA-256 `812247f4548c3c2565b8adb53f8d756280bb3cb297cca808c289024c5f79b734`) are retained under private directories with `SHA256SUMS`. Gate 2W remains `OPEN`; the four-state GNOME row remains `HOLD`. No automated retry is authorized.
- **Rollback:** Candidate v5 was reinstalled from the previously verified bundle. Installed app commit is again `3f2897a04bb0dffc911818d05923b1cfaf05ed81f7692bb50e4e028923bef123`; runtime remains `0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792`. `validation-reports/run5-operator.VXrwLA/rollback_status.json` records the verified rollback (SHA-256 `435da43648c1305b0cae45c661a235f43474c06f72bba4008439866363fdf77b`). No CineHDR instance was left running.

Next work is offline root-cause analysis of the HDR `end-file` error and a discriminating local regression or container read check. Any further real-GPU/Wayland execution requires a new bounded brief, independent review under ADR-0006, and separate explicit user approval; it must not repeat Run 5 unchanged.

### Run 5 offline diagnosis and correction scope (2026-10-04)

The installed Flatpak read both pinned fixtures, and CPU-only libmpv decoded
them to EOF; an isolated playlist transition also loaded HDR. Those checks
exclude basic file access and software decode, but do not explain the live
`end-file` error in the VAAPI/GTK path. Its reason remains **UNKNOWN**.

Separately, static control-path analysis found a deterministic validation
conflict: `publication` temporarily sets HDR mode to `auto`, but without the
test-only `CINEHDR_FORCE_SURFACE_SUBMISSION=1` override the controller must
retain SDR even for the HDR fixture. Independent architecture review accepted
a child-only override with explicit report disclosure and unchanged GDK and
Wayland oracles; ADR-0006 records the bounds. The updated mpv log captures a
sanitized error label instead of a personal media path for a future bounded
observation. Offline tests, not a hardware rerun, decide this correction's
readiness. Gate 2W remains `OPEN` and the four-state row stays `HOLD`.

Candidate v7 is now built from isolated source commit
`04fe6838162c1c54d2f1ce93d94bf7acb889fcdf` (OSTree app commit
`9b299923b474c007802ae12bd23394c95ba09dcfa7a62fb1f0bf293ae85713b4`);
the offline Flatpak build passed 13/13 suites. Its private bundle and
provenance are in `validation-reports/candidate-v7/`. An independent Astra
high review accepted the bounded `run6_brief.md` after two preflight fixes:
fail closed on `flatpak ps` errors and pin the host protocol parser hash. The
proposed Run 6 was scoped to **legacy only**, stopped on failure, recorded the
mpv error label, and restored v5 only after confirmed test-instance absence.
Its result is recorded below; Gate 2W remains `OPEN`, row `HOLD`.

### Gate 2W Run 6 result — GNOME / Candidate v7 (2026-10-10)

One explicitly user-approved `legacy` hardware execution used Candidate v7 app
commit `9b299923b474c007802ae12bd23394c95ba09dcfa7a62fb1f0bf293ae85713b4`
and runtime commit `0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792`.
The first installation command failed before installation because `pkexec`
changed cwd and its relative bundle path could not be found. The brief was
corrected to use absolute install/rollback paths and independently reaccepted;
that pre-run error did not consume a hardware attempt. v7 was then installed
and verified, the dry-run preflight passed, and exactly one `legacy` process ran.

- **Observed:** initial SDR publication passed. The SDR→HDR switch also passed
  with actual 1400×788 `GdkGLTexture` in `R16G16B16A16_FLOAT`, Rec.2100 PQ,
  active `opengl` and `vaapi-copy`. This confirms that the isolated test
  override removed the deterministic Run 5 SDR gate for this test execution.
- **Failure:** the HDR→SDR switch failed. The controller observed SDR source
  and inactive HDR output, but the published texture and render target remained
  HDR. The raw log also contains an mpv `end-file` label,
  `video output initialization failed`, immediately before Wayland window
  destruction. The validation report was already written about four seconds
  earlier, and its completion path closes the window after four seconds.
  The pinned mpv source can emit this same error when Render API context
  teardown kills the video track. The event's causal role in the earlier
  HDR→SDR failure is therefore **UNVERIFIED**; it must not be used as the
  failure explanation.
  The fourth HDR state and GPU Next backend did not run.
- **Independent component:** offline parsing of the retained client Wayland
  trace found a primary composite-window ready→set→commit chain on the CineHDR
  toplevel surface (ready index 584, set 676, commit 692). This protocol
  component is recorded separately and does not override the failed
  publication prerequisite or close Gate 2W.
- **Provenance and cleanup:** raw report SHA-256
  `7903f25893dba52dbf17f811cce9c6a74c5644ba0be1b95a6096bce243b38cef`,
  raw trace SHA-256
  `7ef795514a514d0e5b5fd73c1393937cad5c3d29b00ec9c54152cf4796cd73eb`.
  Private `validation-reports/run6-operator.VrRGGr/run6_observation.json`
  retains the sanitized machine-readable result; its SHA-256 is
  `76dd2d3b407ada40f5f5b3761a80d39987e9bf060f672b46675184994b3595ac`.
  The test instance exited 0, cleanup was `CLEAN`, post-run `flatpak ps`
  confirmed absence, and Candidate v5 was restored and verified at commit
  `3f2897a04bb0dffc911818d05923b1cfaf05ed81f7692bb50e4e028923bef123`.

Result: four-state GNOME row `HOLD`, Gate 2W `OPEN`. Do not repeat Run 6 or
start GPU Next on this evidence. The specific offline hypothesis is that a
paused media switch can publish a frame before the controller updates its HDR
decision: `_on_hdr_controller_change()` queues GTK compositing but does not
schedule a new FBO render. This is not yet a confirmed live root cause. Any
color-pipeline or render-synchronization correction requires an ADR, independent
review, and a new bounded validation brief under ADR-0006 before hardware use.

### Gate 2W Run 7 final GNOME adjudication (2026-10-11)

Candidate v14 was tested in one GNOME Wayland launcher session with separate
`legacy` and `gpu-next` Flatpak processes. Both passed the four-state GDK
publication matrix, the composite-window Wayland color-management submission
oracle, and the three-phase FBO pixel oracle; both exited 0 with clean cleanup.
Observed repeat-SDR maximum deltas were 0.003922 and 0.000000, respectively,
below the predeclared 0.020 bound. This accepts the bounded GNOME client path;
it does not certify physical HDR output or individual presented frames.

The original decision and its first superseding record remain in
`validation-reports/` as audit history. The current, sanitized
`gate2w-run7-adjudication-reconciled-mesa-20261011.json` (SHA-256
`70f8e5f1b658f174439826f378ec93254e17f279e917c35577c4b844f0758a94`)
sets GNOME `CLOSED`, cross-compositor Gate 2W `CONDITIONAL`, and Gate 2 and
Gate 3 `OPEN`. It records that the Mesa release was not captured in Run 7.
Raw traces and media fixtures remain local and are not published.

### Paused HDR output republication and first-frame evidence cleanup (2026-10-17)

Following accepted ADR-0007, the asynchronous property readback (`_readback_target_properties`)
was removed from the active render and publication path. Target color options (`target-prim`,
`target-trc`, `target-peak`) are set synchronously at render entry via mpv options cache,
eliminating property-readback races and timeouts during paused HDR/SDR transitions.

Render generation protection and first published sample retention are preserved:
- Generation tracking and `_generation_ready` guarantee that a stale frame does not
  supplant a newly requested render target.
- In `src/gpu_validation.py`, the first published capture is strictly recorded and
  protected against subsequent replacement.
- Three-way evidence semantics are enforced: missing GDK accessors, unpopulated epoch,
  or unknown media path remain sticky `UNVERIFIED` across subsequent recovery, while
  proven format, tag, or epoch mismatches strictly remain `FAIL`.

Both `legacy` and `gpu-next` backends passed the local client GDK publication matrix
and paused transition FBO pixel oracle on GNOME Wayland (0 LSB repeat-SDR delta,
consistent `R8G8B8A8`/sRGB and `R16G16B16A16_FLOAT`/Rec.2100 PQ states, active APIs
`opengl` and `opengl-next`).

Scope limitation: This confirms only the measured client-side texture publication and
FBO pixel readback boundaries in the development overlay. It does not certify physical
display HDR output, compositor presentation transformations, or packaged application
distribution.
