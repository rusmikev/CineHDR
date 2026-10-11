# ADR-0007: Republish the render target after a paused HDR output change

- **Status:** Accepted for implementation after independent Astra high review;
  runtime correctness remains unverified
- **Date:** 2026-10-10
- **Scope:** CineHDR's libmpv Render API FBO and GDK texture on a paused SDR/HDR switch
- **Builds on:** [ADR-0001](0001-gpu-next-render-api.md), [ADR-0004](0004-gate2-fbo-pixel-validation.md), and [ADR-0006](0006-bounded-validation-governance.md)

## Context

The single authorized GNOME Run 6 on Candidate v7 passed initial SDR and
SDR→HDR publication, then failed HDR→SDR. At the failed sample, the controller
reported an SDR source and inactive HDR output, but the FBO target and actual
`GdkGLTexture` still had 16-bit Rec.2100 PQ state. A frame had been published
after the playlist switch, so merely waiting for *a* new frame is insufficient.
The raw report and trace retain exact provenance in `docs/GPU_NEXT_DEVELOPMENT.md`.

The publication fixture is paused. The libmpv update callback can schedule a
render before the `video-params` observer has applied the new output decision.
`HdrController.apply_hdr_settings()` then notifies the video widget, whose
`_on_hdr_controller_change()` only queues GTK compositing of the existing
texture. It does not force another FBO render. This is a source-supported
ordering hypothesis, **not yet a confirmed live root cause**. The mpv
`end-file` error label appeared adjacent to window teardown after the report
was written; its causal role is unverified.

## Proposed decision

Keep a committed output state separate from the desired state observed in
`video-params` or selected by mode, peak, or monitor changes. Each desired
change advances an epoch and makes new publication pending. On the GTK main
context, apply all required mpv target properties, then obtain nonblocking
readback for the same epoch. Commit one immutable tuple of target transfer,
primaries, peak, FBO format, and GDK color state only if all setters succeed,
all readbacks match, and no newer epoch or shutdown supersedes the work.

The pinned libmpv `mpv_set_property()` returns the core property-set status
synchronously (`player/client.c:1311-1342`); `mpv_get_property_async()` exists
for nonblocking readback (`player/client.c:1507`). Setter success acknowledges
core configuration, **not rendered pixels**. Partial setter failure, missing or
mismatched readback, or bounded readback timeout leaves the pending epoch
invalid. A previous texture may remain visible only as last-known output; it
must not be claimed as publication of the new state. Report the gap in Video
Output Diagnostics and avoid an automatic retry loop. If restoring prior mpv
properties cannot be verified, suppress further publication until a valid
state is committed or the renderer fails explicitly.

All render entry points, including mpv update, paused redraw, resize, startup,
and context restoration, use the committed tuple for **both** FBO choice and
GDK tag. No property query runs in the render callback. Recheck the epoch
before `GdkGLTextureBuilder.build()` and discard a render superseded during
that call. A new commit requests one coalesced
`_schedule_render(force_redraw=True)` so paused playback can republish even
with zero mpv update flags. Equivalent notifications do not create a loop;
shutdown cancels pending work and prevents publication. No saved setting,
fixture, timeout, or validation threshold changes are implied by this design.

## Alternatives and risks

- `queue_draw()` alone only recomposites the previously published texture.
- Increasing the validation wait or unpausing the fixture would not guarantee
  that a color-decision change causes a new render.
- Forcing a render before mpv target-property application could tag pixels with
  a color state that does not match their content. Committed-state gating and
  the pixel oracle below must reject this.
- A controller callback may also fire for monitor or peak changes. Coalescing
  and no-op detection must prevent unnecessary redraws and shutdown callbacks.
- Readback acknowledgement can lag behind a decoded frame. While pending,
  show only the last-known texture and disclose that the new state is not yet
  published; do not mislabel it as the current state.

## Measurable validation

Before hardware, add a controlled-order regression: publish an HDR paused
frame, deliver the next libmpv update before SDR `video-params`, then deliver
the controller change. Verify that a subsequent forced render advances frame
generation and publishes `GL_RGBA8`/sRGB with acknowledged SDR mpv target
properties. Exercise the reverse direction, zero mpv update flags, partial
setter failure, missing/mismatched readback, a superseding epoch, repeated
notifications, and shutdown. The regression must fail on the current code.

Extend ADR-0004's development-only caller-owned FBO readback oracle for the
paused transition. Read the **same FBO slot that will be published** after
`mpv_ctx.render()` and before building its GDK texture. Use the pinned,
byte-identical HEVC SDR/HDR patch fixtures, fixed geometry, frame content, and
deterministic render options. Retain the initial SDR FBO capture as the
same-session reference; capture the HDR state and the return-to-SDR FBO.
Require named patch samples of the returned SDR FBO to match the initial SDR
reference within a threshold fixed **before** the run from format precision
and measured deterministic repeat variability, and require the HDR capture to
be distinguishable from SDR at those patches. Record FBO slot identity, epoch,
frame generation, target properties, fixture hashes, geometry, and raw samples.
As a negative control, substitute prior HDR pixels into an SDR-format FBO
while keeping SDR properties and sRGB tagging correct; the pixel comparison
must fail. If this capture or threshold cannot be established, leave pixel
correctness `UNVERIFIED`; correct metadata alone cannot close it.

After independent review and explicit user approval under ADR-0006, one new
pinned **legacy-only** GNOME observation may compare all four publication
states under a 120-second outer timeout. Retain the raw report, mpv target
property evidence, Wayland trace, exact app/runtime/source identities, exit
codes, artifact hashes, and cleanup result. Gate 2W remains `OPEN` and the
four-state row `HOLD` until its own evidence criteria are met.

## Rollback

Remove the controller-triggered forced render and any associated property
guard; retain the stable renderer and the Run 6 evidence. Restore the prior
verified Flatpak bundle after confirmed absence of the test instance.
