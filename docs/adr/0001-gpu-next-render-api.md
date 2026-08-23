# ADR-0001: GPU Next through a pinned libmpv Render API backend

The prototype backend decision remains authoritative. Its user-facing,
process-immutable selection and saved-preference fallback are defined by
[ADR-0002](0002-user-facing-gpu-next-selection.md).

- **Status:** Accepted for prototype
- **Date:** 2026-08-19
- **Amended:** 2026-08-22 after external, Dolby Vision, and lifecycle reviews
- **Scope:** Local `gpu-next-local` development line

## Context

CineHDR currently embeds mpv through the libmpv OpenGL Render API. mpv renders
into a CineHDR-owned OpenGL framebuffer; CineHDR then publishes that texture to
GTK with the correct GDK color state and lets the Wayland compositor present it.

The current boundary is valuable and must be preserved:

```text
mpv decode/process
  -> CineHDR GL framebuffer
  -> GL synchronization
  -> Gdk.GLTexture
  -> Rec.2100 PQ or sRGB color state
  -> GTK/Wayland compositor
```

Upstream libmpv does not yet expose a released GPU Next render backend. The
tracking issue remains open and mpv's own comparison page still lists the
Render API as unsupported:

- https://github.com/mpv-player/mpv/issues/10810
- https://github.com/mpv-player/mpv/wiki/GPU-Next-vs-GPU

## Decision

Build the first prototype using a pinned custom libmpv based on lhc70000's
experimental `opengl-next` API type:

https://github.com/lhc70000/mpv/commit/97179bce7ed980c53647d6344916f632fe689e9e

This design accepts the same OpenGL initialization and caller-owned FBO
parameters as the existing `opengl` Render API. It therefore lets us change the
renderer behind `mpv_render_context_render()` while initially keeping the GTK,
OpenGL-resource, synchronization, and Wayland HDR presentation code unchanged.

This is a time-boxed feasibility decision, not an endorsement of the patch as
production-ready. The implementation is large, unmerged, and its OpenGL path
was described by its author as experimental.

## Backend modes

The internal selector has three modes:

- `legacy`: request API type `opengl`;
- `gpu-next`: request API type `opengl-next` and fail visibly if unavailable;
- `auto`: try `opengl-next` during context creation, then try `opengl` if the
  first attempt reports an unsupported or initialization failure.

Development defaults to `legacy`. This ADR originally kept the selector
internal; ADR-0002 later exposes the safe `legacy`/`gpu-next` subset as a
restart-required user setting while retaining `auto` for development only.

Fallback is permitted only while creating the render context. A failure after
rendering begins must request a clean restart in legacy mode; CineHDR must not
hot-swap a renderer with uncertain GL or hardware-decoder state.

### GL context recreation

Controlled hardware validation showed that freeing the old Render API context
under its owning `Gtk.GLArea` context and creating a new `opengl-next` context
is not sufficient by itself. The pinned mpv implementation calls
`kill_video_async()` from `mpv_render_context_free()`; that path tears down the
VO and deselects the current video track. A new context can therefore be active
while paused playback has no video chain or frame.

Before freeing a live context, CineHDR records the current video selection and
whether it was automatic. After the new context and its update callback exist,
CineHDR forces a `vid=no` transition and restores `vid=auto` or the exact prior
track ID. This reinitializes the video chain against the new Render API context
without replacing the mpv core, changing the file, or changing pause state.
The transition runs only after an actual context teardown; first startup is
unchanged. Failure to restore the track latches the existing lifecycle-failure
state and requires application restart.

The accepted evidence remains two real `Gtk.GLArea` cycles with a newly
rendered frame, preserved HDR state/observers and render target, followed by
working playback and seeks. If track restoration proves unreliable for any
supported workflow, remove it and use the already-defined stop/restart policy;
do not retain or reuse a Render API object across different GL contexts.

### SDR/HDR transition validation

The controlled transition matrix loads exactly two preflighted files into one
isolated process: BT.709 SDR first and PQ or HLG HDR second. It runs
SDR → HDR → SDR → HDR without recreating the Render API context. Every
switch must publish a new frame and change all coupled state together:

- SDR: source detector false, HDR output false, `GL_RGBA8`/8 and sRGB;
- HDR: source detector true, HDR output true, `GL_RGBA16F`/16 and Rec.2100 PQ;
- GPU Next remains active, the render-context generation is unchanged, stale
  Dolby Vision metadata is absent on SDR, and playback/seeks still work.

`force-hdr` intentionally publishes SDR content through the HDR surface, so it
cannot demonstrate the SDR half of this invariant. Transition validation
therefore sets the in-memory controller mode to `auto` only in its non-unique,
short-lived process. It does not write GSettings or alter the saved product
preference. This is measurement isolation, not a change to normal color policy.

Thumbnail preview remains on the legacy backend during the first experiment.

## Required invariants

1. HDR output contains BT.2020 coordinates encoded as PQ in `GL_RGBA16F` and is
   tagged `Gdk.ColorState.get_rec2100_pq()`.
2. SDR output remains sRGB in `GL_RGBA8` and is tagged sRGB.
3. HLG remains an mpv HLG-to-PQ conversion, not native HLG presentation.
4. CineHDR continues to own the final FBOs and textures.
5. The existing three-slot pool, destroy notification, fallback-slot lifetime,
   fence, and flush rules remain unchanged until measurements justify a new
   architecture.
6. Context creation and render calls use the current `Gtk.GLArea` OpenGL
   context as required by the libmpv Render API.
7. Wayland capability checks and monitor-HDR gates remain CineHDR
   responsibilities because libmpv does not own the GTK surface or swapchain.
8. The existing `target-prim=bt.2020`, `target-trc=pq`, peak policy, and absence
   of `target-colorspace-hint` remain unchanged initially.
9. Backend capability is independent of content and display detection. Legacy
   rendering must never claim Dolby Vision RPU support.
10. Stable legacy playback remains usable throughout development.

Every render call explicitly provides the target FBO `internal_format` and
output `depth`: `GL_RGBA16F`/16 for HDR and `GL_RGBA8`/8 for SDR.

Their meaning differs between the two backends. The experimental
`opengl-next` implementation uses `internal_format` when libplacebo wraps the
caller-owned FBO; this is authoritative for the target representation and its
automatic sample-depth/dithering decision. That implementation currently
ignores `MPV_RENDER_PARAM_DEPTH` and resolves dithering through `dither-depth`
(`auto` matches the target surface). The legacy OpenGL backend does consume
`MPV_RENDER_PARAM_DEPTH`, so passing the matching 8/16 value prevents it from
assuming an 8-bit target for an HDR `GL_RGBA16F` FBO. We retain both parameters
for a shared render path, but `depth=16` is not evidence that GPU Next itself
rendered at 16-bit precision. The relevant API contract is documented in:

https://github.com/mpv-player/mpv/blob/master/include/mpv/render_gl.h

## Alternatives considered

### PR #16818 and `MPV_RENDER_PARAM_BACKEND`

https://github.com/mpv-player/mpv/pull/16818

This route has some downstream testing, but it requires extending the pinned
`python-mpv` binding with a new render parameter. Upstream reviewers also
requested architectural changes and the pull request remains a Draft. Keep it
only as a reference or fallback research path if `opengl-next` fails Gate 1.

### Standalone `vo=gpu-next`

Reject for integration. An mpv-owned window would bypass CineHDR's GDK texture,
integrated GTK layout, texture lifetime, and present Wayland HDR decision path.
It may be used as a visual reference player only.

### Direct libplacebo or a new player engine

Reject. This would expand the work from replacing a renderer to rebuilding
frame delivery, A/V synchronization, seeking, subtitles, track handling,
hardware decoding, metadata, screenshots, and lifecycle management.

## Dependency isolation and pinning

- Do not replace the system mpv installation.
- Build the custom libmpv in an isolated local prefix or Flatpak build.
- Pin the full mpv commit, its base, libplacebo and FFmpeg versions, Meson
  options, compiler/tool versions, and `python-mpv` version.
- Do not use floating branches or unrecorded distribution resolution.
- Log the loaded libmpv path/version, libplacebo version, requested backend,
  active backend, and fallback reason at startup.
- Compare `legacy` and `gpu-next` using the same patched libmpv binary before
  comparing against the current stable package. This separates renderer
  differences from dependency-version differences.
- Do not rebase the experimental patch until the exact pinned revision passes
  the first feasibility gates.
- Before a public build, archive an auditable patch series relative to the
  recorded upstream base, including base/result SHA and checksum. Do not vendor
  the complete mpv source tree into CineHDR.

## Dolby Vision policy

GPU Next/libplacebo RPU processing is the primary expected feature gain, but
CineHDR still outputs rendered Rec.2100 PQ rather than a native Dolby Vision
bitstream.

- Keep the Profile 5 refusal for `legacy`.
- Treat Profile 5 as candidate-supported, not supported, for `gpu-next`.
- Do not remove the gate until tests prove that RPU reshaping is active and
  colors are correct.
- Test Profiles 5, 7, and 8 separately; do not imply complete enhancement-layer
  support from successful RPU mapping.
- Verify text and image/PGS subtitle color in HDR and Dolby Vision.
- Any future Profile 5 activation must use an explicit, version-qualified
  capability such as `supports_dovi_reshaping`; it must not infer support from
  the backend name alone. Unknown backend/dependency versions default to
  unsupported. A hidden development override may be used for Gate 3 evidence.

### Evidence and product wording

Source review of the pinned `97179bce7` build established a narrower claim
that may be shown before Gate 3 without advertising validated Dolby Vision
support:

- FFmpeg's per-frame Dolby Vision metadata is mapped into
  `mp_image.params.repr.dovi` by mpv;
- the experimental `opengl-next` backend passes that representation unchanged
  to `pl_render_image_mix()`;
- libplacebo's color-decoding shader applies Dolby Vision reshaping when the
  representation is `PL_COLOR_SYSTEM_DOLBYVISION` and `repr.dovi` is present.

For the exact pinned mpv/libplacebo combination, an active `opengl-next`
session plus a runtime `colormatrix=dolbyvision` observation may therefore be
reported as **RPU metadata mapped to the GPU Next render path**. This is source-
and-runtime evidence of the active code path, not pixel evidence that the
result is color-accurate. Diagnostics must keep the validation state separate.
Unknown dependency combinations remain unvalidated and must not inherit the
claim merely from the `opengl-next` name.

The allowed product description is "Dolby Vision RPU reshaping to rendered
Rec.2100 PQ (experimental, validation pending)". Do not call this native/full
Dolby Vision or Dolby Vision passthrough: CineHDR outputs pixels through a GTK
HDR surface and does not send the original Dolby Vision metadata to the
display. Enhancement-layer/FEL reconstruction remains unsupported. Profile 5
remains blocked until Gate 3 passes.

Profile 8 alone is not proof of an HDR10-compatible base layer. Validation
fixtures must record `dv_bl_signal_compatibility_id`; diagnostics that cannot
read it must not label every Profile 8 stream as an HDR10 fallback.

HDR output is also not necessarily direct passthrough. When the configured
target peak is numerically below the source peak (for example 279 nits for a
roughly 1000-nit source), GPU Next performs intentional HDR-to-HDR tone mapping
inside the PQ output. Diagnostics must describe that mapping rather than call
the session direct passthrough.

## Phases and acceptance gates

### Gate 0: stable baseline

- The stable backend remains launchable.
- Existing test status and local hardware/software versions are recorded.
- No unexplained new failure is introduced.

### Gate 1: build and first frame

- The exact pinned custom libmpv builds reproducibly in isolation.
- `opengl-next` context creation succeeds.
- An SDR video renders inside the existing GTK widget for 30 minutes while
  seeking, resizing, pausing, and resuming, without a crash, persistent black
  frame, GL error, or steadily increasing VRAM use.
- Diagnostics prove that GPU Next, not fallback, is active.
- Two controlled `Gtk.GLArea` unrealize/realize cycles, or an equivalent GL
  context-recreation test, complete without calling libmpv Render API functions
  under a non-current or different GL context. The backend is recreated, HDR
  observers remain active, and later SDR/HDR transitions are still detected.
  If safe recreation is impossible, playback stops and requires restart.
- A single-process SDR → HDR → SDR → HDR sequence publishes the expected
  8-bit/sRGB and 16-bit/Rec.2100 PQ targets without recreating the renderer.
- Diagnostics record GL vendor, renderer, version, GLSL version, successful
  libplacebo OpenGL initialization, and a complete/renderable `GL_RGBA16F` FBO.

### Gate 2: HDR/SDR correctness and controlled renderer differences

- HDR10 and HLG use `GL_RGBA16F` plus Rec.2100 PQ; SDR uses `GL_RGBA8` plus
  sRGB.
- Existing pixel patterns preserve monotonicity, neutral axis, black, reference
  white, and primaries within validator tolerances.
- Against patched legacy on the same dependency stack, there is no unexplained
  regression in black level, reference white, neutral axis, highlight
  clipping, transfer/primaries signalling, monitor gating, subtitles,
  screenshots, stability, or supported workflows. Expected differences in
  tone mapping, gamut mapping, dithering, film grain, and metadata processing
  are documented per sample and configuration; pixel identity is not required.
- The monitor-peak substitution is retested because GPU Next and legacy may
  intentionally produce different tone-mapping curves for the same target.
- SDR gradients compare `dither-depth=auto`, disabled dithering, and explicit
  8-bit output. Auto must not introduce clipping, non-monotonic gradients, or
  clearly worse banding than legacy; HDR auto must not silently target 8-bit.
- The local decoding matrix covers software decode, the actual `auto`/safe
  selection, VAAPI direct, and copy fallback where available. It records
  `hwdec-current`, drops, load, and reload/seek behaviour for SDR and HDR.
- Screenshot checks cover video-only and subtitle-inclusive output for SDR,
  HDR10 and HLG, including rotation/aspect, software/hardware decoding,
  dimensions, absence of black/green frames, and expected SDR tone mapping.
- On the same machine, a ten-minute run has no more output drops than
  `max(3 frames, 0.1% of presented frames)` above patched legacy, and the 95th
  percentile render time is no more than 10% worse.
- Twenty load/unload cycles and 100 resizes show no continuing VRAM growth after
  settling.
- Existing monitor/compositor auto/force behavior remains unchanged.

### Gate 2W: Wayland compositor surface submission (Separate track)

- End-to-end Wayland presentation depends on GTK's protocol implementation (`wp_color_management_v1`).
- On GTK 4.22.4, GTK binds the protocol but does not invoke `get_surface` or `set_image_description` for the application window.
- Gate 2 is scoped to the FBO/GDK texture publication boundary under CineHDR's control.
- Full compositor passthrough is tracked separately under **Gate 2W**, contingent on upstream GTK color management fixes (tracked in GNOME/gtk issue).
- Until Gate 2W is closed, user-facing documentation describes output as "Rec.2100 PQ target prepared & tagged; compositor passthrough pending upstream GTK support".

### Gate 3: Dolby Vision value

- Profile 5 has no green/purple IPT appearance and passes neutral/primary
  reference checks.
- Enabling and disabling Dolby Vision metadata produces the expected measurable
  difference on RPU-active scenes.
- Profile 8 base-layer playback is not worse than legacy; its RPU result is
  recorded separately.
- Text and PGS subtitle colors are verified in HDR/Dolby Vision.
- The same pinned samples and equivalent renderer options compare embedded
  `opengl-next`, standalone `mpv --vo=gpu-next --gpu-api=opengl`, embedded
  legacy, and `dolbyvision=no` as a negative control. Exact pixel identity with
  the standalone swapchain is not required, but material unexplained
  regressions are rejected.
- Profile 5 activation is controlled by the explicit version-qualified backend
  capability and remains disabled by default until all criteria pass.
- Only then may the GPU Next backend advertise Dolby Vision reshaping support.

### Gate 4: eligible to become default

- All correctness, controlled-difference, and value gates pass on the target
  machine.
- No stable feature regression remains.
- Clean startup fallback works with an unpatched/system libmpv.
- A public release additionally requires vendor coverage and a documented
  installation/update path.

## Risks

- **Patch abandonment or API churn:** contain it behind a narrow adapter and
  exact dependency pins.
- **Untested OpenGL implementation:** fail early at Gate 1; reopen this ADR
  rather than beginning a player rewrite.
- **Dependency drift mistaken for a renderer difference:** test both backends
  in the same patched binary.
- **Incorrect target format or dithering:** pass explicit internal format and
  depth.
- **Hardware-decoder regressions:** test software decoding and each active
  hardware decoder independently.
- **Synchronization regressions:** keep the current pool/fence contract; any
  change requires a new architecture review.
- **Video-track restoration regression after GL recreation:** require two
  measured context cycles and post-cycle seeks; on any restoration error latch
  lifecycle failure and require restart rather than reusing the old context.
- **Dolby Vision overclaim:** keep the capability false until measured.

## Rollback

Select `legacy`, or launch against the original system libmpv. The prototype
does not require user-data or settings migration, so rollback is immediate.

## Product decision deferred

No product input is required for Gates 0-1. After the HDR correctness and
controlled-difference results are known, the product owner will choose the
priority among Dolby Vision Profile 5 correctness, performance, and broad GPU
compatibility if they conflict.
