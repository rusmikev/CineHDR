# ADR-0003: Pinned GPU Next Flatpak dependency stack

- **Status:** Accepted for experimental packaging
- **Date:** 2026-08-22
- **Scope:** Local GPU Next candidate builds; no public release
- **Builds on:** [ADR-0001](0001-gpu-next-render-api.md) and
  [ADR-0002](0002-user-facing-gpu-next-selection.md)

## Context

Gate 1 evidence was produced with custom mpv commit
`97179bce7ed980c53647d6344916f632fe689e9e`, libplacebo `7.360.1`,
FFmpeg `8.1.2`, and libdovi `3.3.2`. The existing Flatpak manifest instead
builds released mpv `0.41.0`, uses the GNOME 50 platform FFmpeg (libavcodec
61 rather than the validated libavcodec 62), and applies a screenshot patch
already present in the experimental mpv commit. A user therefore cannot
currently obtain the validated GPU Next dependency stack from the Flatpak
build alone.

The tested host libplacebo reports both `PL_HAVE_DOVI=1` and
`PL_HAVE_LIBDOVI=1` and links `libdovi.so.3`. Reproducing only the three
headline version strings would silently omit part of that dependency build.

The first installed candidate exposed a second dependency boundary: FFmpeg
advertised HEVC VAAPI methods, but mpv failed to create both `hevc-vaapi` and
`hevc-vaapi-copy` devices before libva initialized. The binary contained
`vaapi` and `vaapi-wayland`, but not mpv's `drm` and `vaapi-drm` features or
the corresponding `libdrm`, `libdisplay-info`, and `libva-drm` dependencies.
The standalone `vaapi-copy` path therefore could not obtain its DRM native
display inside Flatpak.

## Options considered

1. Build custom mpv against the platform FFmpeg. This is smaller, but it does
   not reproduce the validated FFmpeg ABI/version or the version-qualified
   Dolby Vision path evidence.
2. Bundle one complete pinned custom dependency stack. This makes the
   candidate self-contained and lets both renderers use identical libraries.
3. Install stable and custom libmpv together and select a shared object at
   runtime. This creates unsafe process/lifetime ambiguity and conflicts with
   the startup-only renderer boundary.
4. Maintain a second, duplicated application manifest or application ID. This
   isolates experiments but invites manifest drift and unnecessary settings
   and data migration before a public distribution decision exists.

## Decision

The local experimental Flatpak candidate contains one custom libmpv built from
the exact `lhc70000/mpv` commit
`97179bce7ed980c53647d6344916f632fe689e9e` (parent/base
`62f1494661b362eac02e7d6c2f9173f53c0f278f`). Its dependency graph pins and
builds FFmpeg `8.1.2`, libdovi `3.3.2`, and libplacebo `7.360.1` before mpv.
All remote archives and Cargo inputs require exact revisions and checksums.
The custom mpv source must not receive `fix-screenshots.diff` because that
change is already present.

mpv configuration must fail unless `drm`, `vaapi`, and `vaapi-drm` are
enabled. The graph therefore also pins libdisplay-info `0.3.0` commit
`47a5590e9c4eb35d67651b8c05a55f1a48259329`. Its build-only PNP database is
supplied by hwdata `0.410` commit
`8bb6c48dc2e683777e625d1c9ef02c1b25b741cd`, then removed from the runtime
image. This is a package capability correction, not a driver-path override;
the manifest and validation runner do not set `LIBVA_DRIVERS_PATH`.

The same libmpv supplies legacy `opengl` and experimental `opengl-next`.
`legacy` remains the default and the creation-time fallback. Renderer choice
remains immutable after process startup; no shared-library or render-context
hot-swap is introduced. The released stable artifact remains unchanged while
this manifest is a local candidate.

Packaging the RPU-capable code path does not complete Dolby Vision validation.
Profile 5 remains blocked, enhancement-layer/FEL support remains unsupported,
and the only allowed pre-Gate-3 description remains the version-qualified,
experimental mapping of RPU metadata to rendered Rec.2100 PQ with color
validation pending.

This milestone targets dependency-pinned repeatability, not bit-for-bit
reproducibility. The GNOME 50 runtime/SDK branch and builder environment must
also be recorded by commit/digest before a stronger reproducibility claim.

## Consequences and risks

- Gate 2 can compare `legacy` and `gpu-next` from the same mpv, FFmpeg,
  libplacebo, and libdovi stack, separating renderer differences from
  dependency differences.
- The unmerged mpv API and the extra Cargo supply chain remain prototype risks.
- A reduced FFmpeg feature configuration could regress existing media and
  network workflows; its explicit build options and a playback matrix are
  acceptance requirements.
- VAAPI method exposure in FFmpeg is insufficient evidence that mpv can create
  a native device. The package must preserve mpv's DRM feature boundary and
  verify it at build time.
- Legacy `opengl` in the custom binary is the rollback renderer for a running
  candidate, but it is not byte-identical to released mpv `0.41.0`.
- The dependency graph must fail its build if the intended FFmpeg/libplacebo/
  libdovi features or versions are absent; version labels alone are not
  sufficient.

## Measurable validation

- Two clean builds use identical source pins, archive/Cargo checksums, build
  options, and recorded SDK commit.
- Build and runtime diagnostics report mpv `g97179bce7`, FFmpeg `8.1.2`,
  libplacebo `7.360.1`, and libdovi `3.3.2`.
- libplacebo reports `PL_HAVE_DOVI=1`, `PL_HAVE_LIBDOVI=1`, and
  `pl_has_libdovi=1`.
- `readelf`, loaded-library diagnostics, or equivalent evidence proves that
  `/app` libmpv resolves the pinned `/app` FFmpeg, libplacebo, and libdovi,
  not platform copies.
- The final libmpv feature list contains `drm`, `vaapi`, and `vaapi-drm`, and
  its ELF dependencies include `libdrm.so.2`, `libdisplay-info.so.3`, and
  `libva-drm.so.2`. A short real-hardware check must report active
  `vaapi-copy`; FFmpeg method enumeration alone does not satisfy this item.
- The CineHDR Meson suite passes inside the candidate build. Legacy startup,
  saved-preference creation fallback, and strict `opengl-next` startup pass.
- Gate 2 compares both renderer APIs from this same candidate artifact. No new
  30-minute soak is required until a concrete packaging or renderer regression
  gives a technical reason; the first hardware packaging check is a short
  strict smoke run.

## Implementation evidence: first local candidate

The first clean candidate build completed on 2026-08-22. Its build environment
was recorded at GNOME 50 SDK commit
`dfecff8363f84250df427a8998569ddade6ce9c5dc74f2ca4ceeeeaabe975985`,
GNOME 50 Platform commit
`a8da766dd0273a67539d2b98358ed6809d8e729280baf63428108837135229d3`,
Rust stable 25.08 extension commit
`385427f6dabe6d297cced114496cfa6e38bb93fe4c6fbee7fdabb97dd141d29a`,
and Builder commit
`d04b579250dbd3d6c53b5496824b6964022c35861d0e9959d0baf697356f0827`.

Build-time `readelf` assertions proved that the installed `libmpv.so.2`
requires `libavcodec.so.62`, `libavutil.so.60`, and `libplacebo.so.360`, and
that the installed `libplacebo.so.360` requires `libdovi.so.3`. Inspection with
the candidate library path resolved the complete chain from the candidate's
own `build-dir/files/lib`. The ignored local bundle is 46,011,816 bytes with
SHA-256
`d6854f680af95b204d8e97705d56e2aba7954bff551d18ed0a1de94bef4375a3`.
This satisfies the first-build and dependency-linkage parts of measurable
validation. Focused tests and the complete host Meson suite passed 7/7 after
the packaging changes. A second Meson configure/build/test cycle in the
candidate Builder environment also recorded all seven tests at exit status 0.

The complete stack was then rebuilt with Builder stage-cache disabled. The
second export produced application commit
`cc7ae181a82f19a7ff6e972b1569478e0f8157a6b415d6c6ba3fcaa1766fdb1d`;
OSTree found the same 214 content objects already in the local repository and
wrote zero new content bytes. This closes the two-build packaged-content
comparison. It does not close bit-for-bit artifact reproducibility: differing
commit metadata changed the application commit and produced a 46,014,296-byte
second bundle at SHA-256
`59eecdafa1b427ba3a4d76ac6c0003319795c4749f87dd380027854e0193840c`,
instead of the first bundle SHA-256. Commit-metadata normalization and short
real-hardware legacy/GPU Next smoke checks remain open.

The first installed-candidate GPU Next quick check ran on 2026-08-23 on the
RX 9060 XT with Flatpak Mesa `26.1.6`. The exact pinned version diagnostics,
active `opengl-next`, DV Profile 8 metadata path, 16-bit Rec.2100 PQ target,
and all 11 interaction steps passed with no decoder, OpenGL, or FBO failures.
The result remains `WARN` and does not close the hardware packaging check:
hardware decoding reported `no`, mpv reported five video-output drops, and the
original launcher log did not retain mpv's diagnostic stream. The runner now
injects a verbose python-mpv log handler only inside the validation process,
without modifying the installed application. An initial diagnostic repeat
reproduced `hwdec=no`, passed all 11 actions, and reduced the reported
video-output drops to four, but Python root logging alone still did not
subscribe to libmpv messages. That result justified a short verbose diagnostic
repeat for concrete VAAPI negotiation evidence; it did not justify repeating
the completed 30-minute renderer soaks.

The validation-only verbose handler then captured three further runs. mpv tried
both `hevc-vaapi` and `hevc-vaapi-copy` and returned `Could not create device`
before any libva message. ELF and source inspection confirmed the missing
`drm`/`vaapi-drm` build features described above. The corrected clean build on
2026-08-23 linked libmpv to `libdrm.so.2`, `libdisplay-info.so.3`, and
`libva-drm.so.2`; its feature list contains `drm`, `vaapi`, `vaapi-drm`, and
`vaapi-wayland`. Pinned hwdata was present for the libdisplay-info build and
absent from the final runtime tree. The exported application commit is
`1ca32dbf0f0ef57a3b11545b1ff9cf2ec5ab837a6ff1d2328e52a556d759d5c5`.
The separate 46,090,048-byte local bundle
`io.github.rusmikev.CineHDR-gpu-next.flatpak` has SHA-256
`92bc7e3f1efa4794d2de3743945b4cde4f5ac0f900ced372aa9ae7fd234b9576`.
The corrected-package run then passed on the RX 9060 XT with the exact
installed application commit. libva opened the Flatpak Radeon driver and mpv
selected `vaapi-copy`; active `opengl-next`, all 11 actions, the 16-bit
Rec.2100 PQ target, and zero decoder/VO/GL/FBO failures were recorded in
`validation-reports/gpu-next-quick-20260823-090720.txt`. Raw DRM-client VRAM
ended 242.1 MiB above its first warmed sample after a temporary peak. This
one-minute value is retained for Gate 2 settling comparisons and is not leak
evidence. The strict GPU Next part of the local package hardware checkpoint is
closed; the result is not vendor coverage, color validation, or Dolby Vision
certification. There is still no technical reason to repeat a 30-minute soak.

## Security Policy and Pinned Dependency Lifecycle

Pinning multimedia libraries (`FFmpeg`, `libplacebo`, `libdovi`) is essential for reproducible color pipeline validation, but requires proactive security maintenance:

1. **Vulnerability Tracking:**
   - CineHDR CI and maintainers monitor upstream FFmpeg security releases (`8.1.x` branch) and CVE advisories.
   - Any high/critical security vulnerability in FFmpeg decoder or demuxer components triggers an immediate patch bump in the Flatpak manifest with an updated SHA-256 digest and test pass.
2. **Scheduled Upstream Audits:**
   - Pinned dependencies are reviewed on every major CineHDR release cycle to assess stability and upstream compatibility.
   - When libmpv upstream PR #16818 (`MPV_RENDER_PARAM_BACKEND`) lands in a stable upstream release, CineHDR will transition from the pinned fork back to the upstream libmpv stack.

## Rollback

For a renderer failure, select `legacy` and restart CineHDR. For a package-level
failure, rebuild or reinstall the released mpv `0.41.0` Flatpak dependency path
and its screenshot patch. No settings or user-data migration is required.
Never swap libmpv or renderer contexts at runtime.
