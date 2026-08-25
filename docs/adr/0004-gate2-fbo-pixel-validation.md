# ADR-0004: Gate 2 embedded-FBO pixel validation

- **Status:** Accepted for Gate 2 implementation
- **Date:** 2026-08-23
- **Scope:** Local correctness comparison of legacy and GPU Next
- **Builds on:** [ADR-0001](0001-gpu-next-render-api.md) and
  [ADR-0003](0003-pinned-gpu-next-flatpak-stack.md)
- **Execution governance:**
  [ADR-0006](0006-bounded-validation-governance.md)

## Context

Gate 1 and the corrected local Flatpak candidate prove that both the embedded
GPU Next render path and real VAAPI-copy playback work on the target machine.
They do not prove pixel/color correctness. Gate 2 requires measurable black,
reference-white, neutral-axis, primary, gradient, clipping, and controlled
legacy-versus-GPU-Next evidence.

The repository already contains an early `pixel_validator_egl.c`, pattern
generator, and Python test scaffold. That scaffold is legacy-only, accepts
missing output as a skipped test, depends on absent pre-generated clips, does
not prove which libmpv was loaded, and does not compare identical renderer
inputs. It cannot be used as acceptance evidence in its current form.

GTK/Wayland screenshots are also insufficient as the first measurement layer:
they include GDK color-state and compositor/display transforms, making a pixel
difference impossible to attribute to the renderer alone.

## Options considered

1. Use visual inspection or screenshots only. Rejected because the result is
   subjective and mixes renderer, compositor, and display behavior.
2. Use mpv's screenshot command. Rejected as the primary oracle because it
   does not necessarily read the exact caller-owned FBO that CineHDR publishes.
3. Render deterministic lossless fixtures through both embedded Render API
   types into explicit CineHDR-format FBOs and read them with `glReadPixels`.
   Selected as the controlled first layer.
4. Start with VAAPI and the real Wayland surface. Deferred to a second layer;
   it is necessary end-to-end evidence but adds decoder, interop, compositor,
   and display variables before the renderer baseline exists.

## Decision

Gate 2 starts with a development-only offscreen EGL harness that loads the
same exact pinned custom libmpv for both `opengl` and `opengl-next`. Each pair
uses the same lossless generated frame, timestamp, software decode, target
primaries/transfer/peak, dimensions, and option set. The only intended variable
is the Render API type.

Fixtures are generated locally from explicit numeric patch definitions and
encoded losslessly with FFV1 in full-range planar RGB. HDR fixtures are tagged
BT.2020/PQ; SDR fixtures are tagged BT.709/sRGB. The generator records its
inputs, ffmpeg version, stream metadata, and SHA-256. Generated media and raw
frames remain disposable build/test artifacts and are not committed.

The harness renders HDR to `GL_RGBA16F` with depth 16 and SDR to `GL_RGBA8`
with depth 8, matching CineHDR's FBO contract. It records the requested and
active API, loaded mpv/FFmpeg/libplacebo versions, input/output color metadata,
actual FBO format, GL implementation, decode mode, patch values, and GL/FBO
errors in machine-readable JSON. Missing dependencies, missing frames,
unexpected versions/APIs, invalid metadata, incomplete FBOs, or empty readback
are failures, never silent skips.

The first slice uses software decoding and disables stochastic variables where
the two backends expose equivalent controls. It strictly rejects missing or
non-finite samples and missing provenance, while recording in-range values,
black distinction, non-decreasing neutral luminance, neutral-axis error, and
primary-channel dominance as diagnostic observations. It also reports
absolute reference-white and legacy/GPU-Next deltas without declaring pixel
identity a requirement. A numerical color threshold may be promoted to a
release gate only after its mathematical basis and fixture decode error are
recorded; thresholds must not be tuned merely to make the first observed
renderer result pass.

Subsequent Gate 2 layers reuse the same fixtures and measurement schema for:

- real Radeon software-decode and VAAPI-copy comparisons;
- dithering modes and SDR gradients;
- GTK/Wayland color-state and monitor-peak end-to-end checks;
- subtitle/screenshot workflows;
- performance and load/unload/resize settling.

Legacy remains the default and fallback. Renderer selection remains fixed at
process/context creation; the A/B harness creates separate mpv and render
contexts and never hot-swaps either one.

### VAAPI-copy extension contract

After accepting the target-Radeon software-decode row, the next bounded layer
adds an explicit `no|vaapi-copy` decode dimension to the development harness.
Software decode remains the default so the accepted baseline command and report
contract do not change. A VAAPI-copy run uses the same generated fixture,
timestamp, output target, FBO, renderer separation, and strict provenance as the
software row; only the requested decode mode changes.

The requested mode and `hwdec-current` are recorded for every fresh process and
must both be exactly `vaapi-copy`. Decoder fallback to `no`, `auto`, or another
backend is a failed evidence run, never an accepted software result under a
hardware label. Both renderer captures must report the same decode mode and GL
device before they are compared. The harness does not request a DRM device,
choose a driver, or add a production fallback; real-device execution remains an
explicit user-run command with the target `DRI_PRIME` selector.

The lossless RGB FFV1 fixture is tried first because it preserves the accepted
software row's exact input bytes and color metadata. If the target VAAPI decoder
cannot decode that codec/profile/pixel-format combination, the strict failure is
capability evidence, not a reason to fall back silently. A later YUV hardware-
decodable fixture would require a separately recorded generation/quantization
error model before its samples could contribute color tolerances.

The first strict attempt produced exactly that capability boundary on the RX
9060 XT: `vaapi-copy` was requested for the RGB FFV1 fixture, but mpv reported
`hwdec-current=no`. The failed report is
`validation-reports/gate2-pixel-rx9060xt-vaapi-copy-20260823.json` (SHA-256
`57734f7a0d576ed5779f882fe291ac0c9fc91d9bde3e053eebb83056d21e5e55`).
It is retained as a valid rejection of that fixture/decode combination. It does
not invalidate the already accepted renderer or package evidence and must not
be repeated without changing the technical input.

### Hardware-decodable YUV fixture contract

The next fixture profile is HEVC Main 10, full-range 10-bit 4:2:0 YUV, using
BT.2020 non-constant-luminance matrix signalling for HDR/PQ and BT.709 matrix
signalling for SDR/sRGB. Patch boundaries remain aligned to 2x2 chroma blocks,
and the same encoded file, frame, targets, and FBOs are used by both renderer
processes. The existing RGB FFV1 profile remains the default and its cache,
schema evidence, and accepted reports are not reinterpreted.

Generation has two explicit error boundaries. First, the deterministic encoded
RGB patch frame is converted once to a recorded raw `yuv420p10le` reference;
the conversion recipe, matrix/range, source and YUV hashes, chroma layout, and
per-patch reconstruction error are provenance. Second, libx265 encodes that
raw reference in lossless mode with fixed single-thread parameters. A software
decode of the resulting HEVC file must be byte-identical to the encoder's raw
YUV input before the fixture is accepted. The exact FFmpeg and x265 identities,
HEVC profile, stream metadata, decoded-frame count, media SHA, and decoded-YUV
SHA are recorded. Missing or unequal evidence is a hard fixture failure.

The playback artifact is a directly encoded MP4/ISO-BMFF file with an `hvc1`
track, 24,000-unit video timescale, and explicit 24 fps timestamps. Two
otherwise identical Matroska trials produced random Segment/Track UID and cue
bytes despite bitexact controls. An elementary HEVC trial was byte-identical but
provided no demuxer packet timestamps, so paused libmpv never published its
first frame even with an explicit fps override. In contrast, two direct MP4
encodes were byte-identical, retained every required VUI/profile field, reported
exactly 24 frames at 24 fps and one-second duration, and decoded byte-identically
to the encoder YUV reference. MP4 is therefore selected instead of weakening
media-SHA reproducibility or adding a raw-stream timing mode to the probe.

The probe treats planar `yuv420p10` and semi-planar `p010` as semantic 10-bit
4:2:0 layouts for metadata validation while recording the actual layout. The
two renderer processes in one row must still report identical input metadata,
GL device, decode mode, target, and FBO. An accepted VAAPI row additionally
requires `hwdec-current=vaapi-copy` in both processes. The RGB-to-YUV error
record remains diagnostic and cannot be promoted to a color tolerance merely
because the first hardware run passes.

The accepted generator revision is
`hevc-main10-yuv420p10-mp4-v2`. Two independent clean generations produced
byte-identical MP4 files: HDR SHA-256
`bc437162b00b6565ca486ecc0a67704b6f099ebccf03cf671b2a0f87e94e021d`
and SDR SHA-256
`41393a0ee9dd887a1f11d4b21946fef3d1f49e72127719eefd1ac40aa9d5e360`.
The encoder identity was x265 `4.1+1-1d117be`, GCC 16, 64-bit, 10-bit. All 24
decoded frames in each file were byte-identical to one another and to the
recorded encoder-YUV stream. The first-frame hashes are
`deb7ce4c78be34c8fef39518001133d0ce33e74ad9e3b774a83bcc80d25771ff`
for HDR and
`37791dba589bda2464be6ea60e5c45c702ab17ad0b40ca5718983a40b5b05f46`
for SDR. The recorded RGB-to-YUV-to-RGB diagnostic model has maximum code
errors 484 HDR and 662 SDR; these are fixture-conversion observations, not
renderer tolerances.

### Render delivery and shader setup contract

The RGB profile remains paused at timestamp zero with `loop-file=no`. The HEVC
profile uses continuous delivery with `loop-file=inf` only because provenance
strictly proves that every decoded frame is byte-identical. Thus shader
compilation cannot select different picture content. Every capture records the
actual time position, pause state, loop state, and update-callback observation.
The GL thread receives only the atomic render update callback during capture,
uses Render API calls rather than normal libmpv commands/properties, reports
each completed render swap, and requires a complete red/green/blue primary
structure before readback. This is synchronization/readiness evidence, not a
numeric color threshold.

A fresh llvmpipe shader cache exposed a legacy-only harness cold-start effect:
the first `opengl` process compiled for 30 seconds but returned a uniform black
FBO, while the following fresh processes on that cache completed normally.
Looping the identical media did not remove the effect. The runner therefore
performs and records one separate HDR shader-setup process for each API before
the four measured processes. A warm-up exit is never pixel evidence and never
weakens a measured failure; process launch remains mandatory, and every later
HDR/SDR renderer capture is still a fresh, strictly validated process. This
explicit setup makes the FBO comparison reproducible without claiming cold
startup validation. First-frame and lifecycle behavior remain separate Gate 1
evidence.

### First HEVC software evidence

The fresh-cache llvmpipe run is recorded in
`validation-reports/gate2-pixel-hevc-main10-mp4-llvmpipe-software-20260823-132808.json`
(report SHA-256
`e038fd8fe07c0feaf7dd08ff4633775e5587048f4e869d966017272f69984e2e`).
It preserved the failed 30.2-second legacy shader-setup attempt, then completed
both fresh APIs for HDR and SDR on Mesa 26.1.7 with the exact pinned mpv,
FFmpeg, and libplacebo identities. All captures used software decode, the
expected 10-bit 4:2:0 input metadata and output targets, the intended FBOs,
and reported no mpv, GL, or FBO errors. Structural diagnostics passed. The
maximum sampled legacy/GPU-Next deltas were `0.068114519` for HDR and
`0.219607830` for SDR.

This accepts only the deterministic HEVC fixture and its offscreen
software-decode probe baseline. The deltas remain diagnostic. It does not
accept VAAPI-copy, real-GPU output, GTK/Wayland/display behavior, color
accuracy, or Dolby Vision, and it does not close Gate 2.

### Target-Radeon VAAPI-copy evidence

The changed-input hardware row completed on the target RX 9060 XT in
`validation-reports/gate2-pixel-rx9060xt-hevc-main10-vaapi-copy-20260823.json`
(report SHA-256
`21af4a712d5169e11632bf213403a728e9de7cab5523796b26a4210701d709ff`).
Both renderer warm-ups and all four measured fresh processes completed on Mesa
26.1.7. Both APIs reported requested and active `vaapi-copy`, the same Radeon
device, `p010`, the exact pinned runtime stack, the required HDR/SDR targets,
and no mpv, GL, or FBO errors. Every structural diagnostic passed. The maximum
sampled legacy/GPU-Next deltas were `0.071410418` for HDR and `0.219607830`
for SDR.

This accepts the real-Radeon VAAPI-copy embedded-FBO row. The RGB FFV1
capability rejection remains valid, and no repeat of either hardware row is
needed without a changed input or implementation. The measured values remain
diagnostic rather than fitted tolerances. This row does not prove the later
GTK/GDK publication, Wayland compositor/display result, subtitles,
screenshots, settling, color accuracy, or Dolby Vision correctness, so Gate 2
remains open.

### Live GTK/GDK publication on Wayland

The next bounded layer validates the hand-off immediately after
`Gdk.GLTextureBuilder.build()` in a real Wayland CineHDR window. It uses the
same deterministic HEVC HDR/SDR fixtures and runs legacy and GPU Next in two
separate processes on the same pinned stack; renderer hot-swap remains
forbidden. A process-local `auto` HDR mode is allowed only in this isolated
validator so the matrix can exercise SDR -> HDR -> SDR -> HDR without changing
the saved preference.

Acceptance reads the actual built `Gdk.GLTexture`, not only CineHDR's intended
state string. Every state records and checks the texture type, dimensions,
`Gdk.MemoryFormat`, and `Gdk.ColorState` equality. SDR requires an actual
`R8G8B8A8`/sRGB texture paired with `GL_RGBA8`/8; HDR requires an actual
`R16G16B16A16_FLOAT`/Rec.2100 PQ texture paired with `GL_RGBA16F`/16. Each
switch must publish a newer texture generation. The process also records and
requires a real GDK Wayland display/surface, the expected startup-selected
renderer, active `vaapi-copy`, the pinned runtime identity, and zero
renderer/GL/FBO failures.

This layer is intentionally not a screenshot oracle. GDK exposes the texture
color state but no public getter for the compositor's current surface image
description. Therefore a successful publication report proves the live
GTK/GDK object and Wayland client path, not compositor transformation, display
photometry, or protocol-level color-state acceptance. Those claims require a
separate protocol-trace or measurement layer and must not be inferred here.

The opt-in publication validator and its two-process local launcher are
implemented. The launcher independently validates the accepted HEVC MP4
metadata and media hashes, runs legacy before GPU Next, and requires one fresh
strict PASS report from each backend.

The corrected real Wayland/Radeon run passed in
`validation-reports/wayland-publication-20260823-143411-268387`. The legacy
report SHA-256 is
`393231dc96111acc7eb44e45ed809a876568e26d0dc963625d797365be07fddf`;
the GPU Next report SHA-256 is
`7256ca8eb0a0aeb4725773fb30cfe6d23385dbcdf95550009b737d70ed8863f8`.
Both processes loaded the pinned runtime, retained their expected startup API,
used active `vaapi-copy`, exposed Wayland display/surface GTypes, and passed
the exact SDR -> HDR -> SDR -> HDR actual-texture matrix with newer generations
3 -> 5 -> 7 -> 9. SDR was `R8G8B8A8`/sRGB paired with `GL_RGBA8`/8; HDR was
`R16G16B16A16_FLOAT`/Rec.2100 PQ paired with `GL_RGBA16F`/16. The texture
dimensions matched the scaled widget in every state and no validation failure
was recorded.

The earlier legacy-only attempt was correctly rejected because automatic mpv
selection produced direct `vaapi`, not the required comparable `vaapi-copy`
path. Publication mode now requests `vaapi-copy` before media loading. This
accepts the live GTK/GDK object-publication row only; the compositor/display
boundary and the rest of Gate 2 remain open.

### Wayland color-management surface submission

The next bounded layer reuses the accepted publication matrix with opt-in
`WAYLAND_DEBUG=client` tracing. A pure parser validates the protocol stream;
it does not marshal new requests or change CineHDR's surface ownership. Legacy
and GPU Next remain separate startup-selected processes, and the normal
publication launcher remains usable without tracing.

For each backend, acceptance requires the compositor to advertise and GTK to
bind the ratified `wp_color_manager_v1`; the manager capability burst must
include perceptual intent and complete with `done`. GTK must obtain a
`wp_color_management_surface_v1` for the CineHDR `wl_surface`, use a
`wp_image_description_v1` that received version-appropriate `ready` or
`ready2`, call
`set_image_description` with perceptual intent, and subsequently commit that
same `wl_surface`. Any image-description `failed` event, Wayland display or
protocol error, missing evidence, or failed publication report is a strict
failure. The trace parser records object identities and ordering rather than
matching brittle numeric object IDs.

The submitted surface description is not required to alternate between sRGB
and PQ. GTK may legally transform both input texture states into one
compositor-preferred surface encoding; the accepted GDK publication reports
remain the authority for the input texture format and color state. Therefore
a protocol PASS proves a ready image description was submitted and committed
without a protocol rejection. It does not prove the compositor's transform,
direct scanout, display mode, luminance, pixels, or color accuracy. Those
claims remain outside this layer.

The trace mode is development-only and contains only the generated disposable
fixtures. Unit tests use synthetic traces for missing globals, unsupported
intent, description failure, wrong object ordering, missing commit, and valid
multi-manager traffic. Rollback removes the optional trace/parser path while
retaining the already accepted GTK/GDK object-publication evidence.

The optional launcher mode and pure parser are implemented. The parser handles
canonical libwayland `interface#id` records, dynamic bind IDs, both
`ready(identity)` and `ready2(identity_hi, identity_lo)`, and TID-prefixed
records. It emits hash-linked JSON evidence without copying raw trace contents
or absolute media paths. Ten synthetic parser cases, launcher static checks,
and the full nine-test Meson suite pass.

The first real trace in
`validation-reports/wayland-publication-20260823-150806-285447` stopped after
the legacy process with the strict protocol criterion unsatisfied. Its normal
publication report still passed and is byte-identical to the accepted legacy
report (SHA-256
`393231dc96111acc7eb44e45ed809a876568e26d0dc963625d797365be07fddf`).
The raw trace SHA-256 is
`3dfc07f4de8e8dc64037037694aff1d10243f78c9107b28554ca106ae751faf1`.
KWin advertised `wp_color_manager_v1`; output probes bound managers, received
perceptual-intent capability bursts, `done`, and ready output image
descriptions. However, the trace contained no `get_surface` or
`set_image_description` request. This is a valid rejection of surface
submission, not a renderer or GDK texture-publication failure.

Local GTK 4.22 help identifies Wayland color management as the explicit
`GDK_DEBUG=color-mgmt` opt-in. The trace launcher therefore enables that flag
only in each `--protocol-trace` CineHDR child, alongside `WAYLAND_DEBUG=client`.
It remains absent from the normal launcher and product environment.

The one justified repeat is retained in
`validation-reports/wayland-publication-20260823-153023-295773`. The legacy
GDK publication again passed, with report SHA-256
`e1316efc83d31dd255dfb11f264ba367164077a11717412fe55e4f9aefd0d319`.
The trace SHA-256 is
`81b09aa49f53be209b0496146b6c24034f0ae65ff24b53a323c0ad3edb015896`.
This time GTK's default queue did bind the advertised version-2 global at
interface version 1 and received perceptual intent plus `done`, proving that
the opt-in took effect. It then destroyed the manager before creating a color
object for the CineHDR `wl_surface`; the trace again contains no `get_surface`
or `set_image_description`. The hash-linked strict FAIL JSON is
`gate2-wayland-color-management-legacy-20260823-153023.json` (SHA-256
`dd697e6dc876bd5b831bf30c791bbebca2eef3ecc42c120261b7a0f018f4382b`).

This closes the attempted protocol row as unavailable on the tested GTK
4.22.4/KWin 6.7.4 stack, not as PASS. A manager global is necessary but not
sufficient evidence that GTK submits a color-managed surface. No GPU Next
process was needed after the toolkit-level legacy failure, and no further
repeat is justified without a changed GTK/KWin implementation. The optional
trace mode remains as a reproducible capability check. It must not be used to
claim compositor transformation, display output, native HDR, color accuracy,
or certification.

### Bounded resource-settling matrix

The next independent Gate 2 layer addresses the unresolved allocation shape,
not long-duration stability. Gate 1's accepted SDR soak observed +50.4 MiB RSS,
and the first corrected Flatpak quick run observed a large raw warmed-sample
VRAM delta. Neither is proof of a leak or leak-freedom. Repeating a 30-minute
soak without changing the measurement basis would not resolve that ambiguity.

The settling validator therefore reuses the exact deterministic HEVC fixtures,
active `vaapi-copy`, pinned runtime, process-local `auto` HDR mode, and separate
startup-selected legacy/GPU-Next processes. Each process keeps the byte-
identical fixtures paused and alive, performs one unmeasured allocation warm-up,
then runs three identical cycles. A cycle resizes the real Wayland window to
1280x720 and 960x540 logical sizes, switches HDR to SDR and back to HDR, and
waits at the same small-window HDR endpoint. The final action unloads media and
records a ten-second cooldown. This exercises FBO reallocation, GDK texture
replacement, VAAPI decoder replacement, and unload settling without adding
playback-duration or demuxer-back-buffer variables.

At every measured HDR endpoint the report records actual GDK texture state,
renderer/context/frame generations, RSS, per-process DRM resident VRAM/GTT,
and GL/FBO/drop counters. Acceptance requires the expected fixed API, unchanged
context generation, active `vaapi-copy`, exact runtime/fixture provenance,
correct HDR/SDR texture/FBO pairs, all requested state/size transitions, three
complete comparable HDR endpoints, available RSS and per-process DRM VRAM, and
zero GL/FBO failures. Global VRAM remains context only because other desktop
clients can change it during the run.

No byte threshold is fitted to the first observation. Three strictly increasing
same-state endpoints for RSS or per-process DRM VRAM produce `WARN`; any plateau
or decrease breaks that sustained-growth signal. `PASS` means only that this
bounded repeated workload did not show that signal and passed the structural
checks. Neither outcome proves or disproves a leak, and retained resources are
not required to fall to zero after unload because the renderer, GTK, driver,
and allocators may retain reusable pools. Raw deltas and the cooldown sample
remain authoritative for later threshold decisions. The paused one-second
fixture also does not resolve Gate 1's long-media demuxer-back-buffer RSS
observation.

The validator is development-only. Rollback removes its mode and launcher while
retaining all prior Gate 1, embedded-FBO, GDK-publication, and protocol-failure
evidence. It cannot change the legacy default, enable runtime renderer hot-swap,
alter normal HDR policy, or broaden Dolby Vision claims.

The bounded implementation is `run_gate2_resource_settling.sh` plus the
opt-in `settling` mode in `src/gpu_validation.py`. The launcher accepts no
arguments, generates the accepted fixtures in an isolated temporary cache,
and runs legacy followed by GPU Next as separate processes. It does not choose
a DRM device, add Flatpak permissions, or modify saved settings. Endpoint
comparability uses the actual compositor-negotiated logical size: the requested
large/small transitions must occur, and the three measured small-window sizes
must match each other, but they need not equal 960x540 exactly. Missing or
incomplete reports fail closed. Environment-independent tests and the full
Meson suite pass; real Radeon/Wayland evidence remains pending.

The first real v1 attempt failed before the resource endpoints. Legacy reached
the correct pinned `opengl`/`vaapi-copy` HDR state with zero GL/FBO failures,
and Wayland applied the requested logical resize, but the paused renderer kept
the old GDK texture and render-frame generation. This is not allocation-growth
evidence. It exposed a missing target-surface synchronization path: an mpv
update callback reports new frame availability, while libmpv also permits the
client to render the previous frame again when the target surface configuration
changes.

The v2 correction initially listened for `notify::width/height`. The next real
attempt loaded v2 but reproduced the same fail-before-endpoint state, proving
that GTK 4.22.4 did not emit those property notifications for this allocation
path. It did not challenge the render-redraw decision or provide renderer/
resource evidence; it rejected the selected GTK integration hook.

The v3 correction overrides Gtk.Widget's `do_size_allocate` vfunc, explicitly
allocates its sole `Gtk.GLArea` child, and compares logical width, height and
scale factor.
It coalesces a forced redraw only after a texture exists, uses the existing
context and FBO publication path, and does not synthesize media or renderer
updates. The pure render-decision invariant is: render when libmpv reports an
update **or** when GTK changed the target configuration. Shutdown clears both
pending states. This also fixes normal paused-video resizing instead of
manufacturing validator-only evidence. Unit tests execute the decision helper
and the allocation callback, including child allocation and redraw request.
The settling contract revision becomes `bounded-resource-settling-v3`;
rollback removes the resize redraw while retaining both earlier FAIL reports.
One bounded repeat is permitted because the measured GTK callback changed; no
duration test is repeated.

## Consequences and risks

- Renderer regressions can be separated from decoder and compositor effects.
- Offscreen llvmpipe evidence is useful for deterministic pipeline behavior
  but is not real-GPU or display certification.
- RGB lossless fixtures avoid chroma-subsampling ambiguity, but later YUV
  fixtures remain necessary for real media coverage.
- Legacy and GPU Next may intentionally differ in tone/gamut mapping and
  dithering. The report must preserve those deltas instead of treating any
  non-identity as failure.
- `glReadPixels` observes the embedded FBO boundary, not the final photons.
  End-to-end Wayland/display validation remains required.
- A live `Gdk.GLTexture` color state proves the published client object, not
  the compositor's surface image-description state or display output.

## Measurable validation

- Fixture generation is deterministic from recorded numeric inputs and its
  decoded stream metadata matches the requested primaries, transfer, matrix,
  range, pixel format, and dimensions.
- Both renderers load the exact pinned libmpv stack and render the same fixture
  into the declared FBO without GL/FBO errors.
- Unit tests cover PQ math, patch layout, report parsing, invariant evaluation,
  failure-on-missing-evidence behavior, and renderer-pair comparability.
- The default Meson suite runs environment-independent unit/static checks. The
  EGL/pixel run is explicit because GPU/driver availability is not assumed.
- The first explicit report contains both renderer results, strict structural
  evidence, and the diagnostic invariant values needed to derive justified
  color thresholds. It does not by itself close Gate 2.
- HEVC reports record non-evidence shader setup separately from the four fresh
  measured processes; a successful measured report cannot be read as a
  cold-start or lifecycle result.
- The GTK/GDK publication matrix uses separate legacy/GPU-Next processes and
  exact texture-object state; missing texture getters or non-Wayland execution
  fail instead of being silently skipped.

## First implementation evidence

The first explicit software/surfaceless run completed on 2026-08-23 and is
recorded in
`validation-reports/gate2-pixel-software-20260823-095222.json`. The report
proves separate `opengl` and `opengl-next` processes for both HDR and SDR,
exact mpv `g97179bce7`, FFmpeg `8.1.2`, and libplacebo `7.360.1` runtime
identities, complete CineHDR-format FBOs, software decode, tagged 24-frame
FFV1 inputs, and finite non-empty readback without GL/FBO errors.
The bitexact recipe was independently generated in two clean cache
directories; both HDR files and both SDR files were byte-identical.

The run also established two harness details that are now part of the
measurement contract: FFV1 needs frame-level `setparams` for Matroska to retain
primaries/transfer metadata, and CineHDR's `flip_y=False` target maps source
row 0 to OpenGL readback row 0. The probe rebinds its caller-owned framebuffer
after rendering before `glReadPixels`; renderer-internal framebuffer state is
not accepted as evidence.

The measured renderer deltas remain diagnostic. Their maxima were
`0.036132812` for the selected HDR float samples and `0.215686262` for the
selected SDR 8-bit samples. No color threshold has been inferred from this
single llvmpipe observation, and Gate 2 remains open for mathematical fixture
error analysis and the later real-Radeon/end-to-end layers.

The same software-decode matrix completed on the target Radeon RX 9060 XT
(`radeonsi`, `gfx1200`, Mesa `26.1.7`) in
`validation-reports/gate2-pixel-rx9060xt-software-20260823.json` (report
SHA-256
`b0ad540280f0eeb5746753a9b64423b6dbe56e2df846c5bed52fcdcc2337c665`).
Both fresh contexts reported the target renderer, the exact pinned runtime
stack, complete `GL_RGBA16F`/16 HDR and `GL_RGBA8`/8 SDR FBOs, software decode,
and no mpv, GL, or FBO errors. Both APIs again produced finite in-range samples,
distinct black/near-black values, non-decreasing neutral samples, zero sampled
neutral-axis channel error, and primary-channel dominance. The maximum sampled
A/B deltas were unchanged at `0.036132812` HDR and `0.215686262` SDR.

This accepts the real-Radeon software-decode layer as a structural and
diagnostic checkpoint only. It does not turn the observed deltas into color
tolerances, close Gate 2, exercise VAAPI-copy or GTK/Wayland/display output, or
certify color accuracy. On this hybrid-GPU host the reproducible target selector
is `DRI_PRIME=pci-0000_03_00_0`; an unqualified run selected the integrated
Raphael GPU and is not target-hardware acceptance evidence.

## Rollback

The harness is development-only and does not change CineHDR playback. If its
fixture or measurement design proves invalid, remove it and retain ADR-0001's
Gate 2 as open. Do not weaken playback color invariants, enable runtime
hot-swap, change the legacy default, or broaden Dolby Vision claims to make a
test pass.
