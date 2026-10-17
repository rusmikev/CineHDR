# ADR-0007: Republish the render target after a paused HDR output change

- **Status:** Accepted; bounded GNOME client publication validated in Run 7
  on Candidate v14. Physical display output remains unverified.
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

## Accepted decision (2026-10-17 amendment)

On the GTK main context, mark the configured-output epoch pending before
setting `target-trc`, `target-prim`, and `target-peak`. After all synchronous
setters succeed, commit one immutable target/FBO/GDK tuple only if that epoch
remains current and the controller has not stopped. A committed tuple records
accepted configuration, not measured pixels. A setter failure or shutdown
suppresses new publication and reports the reason while retaining the previous
texture and its original label. Property readback, its private mpv API, timers,
and mock-specific branch are removed; CineHDR owns target options during
playback.

The widget does not render a candidate while pending or suppressed. It selects
FBO format and GDK label from the same committed snapshot and drops a render
superseded before texture construction. Only assignment of a new texture
advances the published generation. A new committed epoch forces a coalesced
redraw, including paused playback with zero mpv update flags. There is no
arbitrary delay or wait for another decoded media frame.

## Alternatives, falsifier, and rollback

A property readback only checks configuration; it cannot establish FBO pixels.
Waiting for a new decoded frame could stall paused transitions. The accepted
source path allows a repeated decoded frame to be rendered with newly accepted
target options, but does not certify the result.

The falsifier is the first new texture published after the requested fixture is
selected and its expected output signal committed, when its captured FBO pixels,
epoch, format, or actual GDK texture tag disagree with the transition contract.
The opt-in oracle records that first publication and preserves mismatches even
if a later frame recovers. Missing capture or provenance remains `UNVERIFIED`;
client publication does not prove display scanout or photometry. A bounded live
run on this changed candidate is still pending. If falsified, revert the
coherent simplification and retain the negative-control and raw failure report.
Restored readback, if temporarily needed for rollback, is configuration
evidence only.

## Subsequent bounded validation (2026-10-11)

A single GNOME Wayland Candidate v14 launcher session passed the declared
four-state publication and three-phase FBO pixel checks for both legacy and
gpu-next. The sanitized decision and raw artifact hashes are recorded in
`validation-reports/gate2w-run7-adjudication-reconciled-mesa-20261011.json`.
This supersedes the pre-run `HOLD` for the GNOME client publication row only;
Gate 2 and Gate 3 remain `OPEN`, and compositor scanout, per-frame presentation
and display photometry remain unverified.
