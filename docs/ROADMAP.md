# CineHDR Roadmap

Short, ordered list of the next engineering steps. The detailed rationale for
items 1–3 lives in `docs/HDR.md` (invariants E–G); this file only tracks what
comes next.

1. **GTK Wayland surface-submission compatibility.** A strict Gate 2 trace on
   GTK 4.22.4/KWin 6.7.4 proved that the color-manager global and manager
   capabilities are not sufficient: GTK destroyed the manager without
   creating or setting a color-managed surface, including under its documented
   experimental `color-mgmt` opt-in. Re-evaluate only with a changed GTK/KWin
   implementation. Until an actual `get_surface` -> ready image description ->
   `set_image_description` -> same-surface commit chain passes, diagnostics and
   release notes must not call the registry preflight native-HDR evidence.
2. **Live monitor-state updates.** Replace the 2 s TTL poll in
   `wayland_output_hdr` with a persistent `image_description_changed`
   listener: keep the private queue alive and pump it from a custom `GSource`
   using `wl_display_prepare_read_queue()` / `wl_display_read_events()` /
   `wl_display_dispatch_queue_pending()` on the display fd. Invalidate caches
   and re-apply HDR settings from the event instead of polling.
3. **Auto `target-peak` from the output.** *Implemented; A/B validation
   still open.* With the peak preset on `auto` and the output's
   `max_lum` below `sig-peak × 203 × 0.9`, mpv now receives the monitor
   value and tone-maps inside PQ (docs/HDR.md, invariant C). Remaining
   release gate: A/B against `peak=auto` on a mid-range (≈400–600 nit)
   HDR display — gradients 100→4000 nits plus real scenes — and a
   decision on trusting the compositor's own tone mapping on KWin ≥ 6.3.
4. **`target-colorspace-hint` cleanup.** *Done.* The property is a no-op
   under the libmpv render API (mpv does not own the swapchain); it is no
   longer set, restored, displayed or asserted anywhere, and a regression
   test locks it out of the mpv property writes.
5. **xx_color_manager_v4 output query.** The monitor-state probe currently
   binds only the ratified `wp_color_manager_v1`. GTK 4.16/4.17 systems
   (Plasma 6.2 era) negotiate `xx_color_manager_v4`, whose event tables
   differ; either add the second interface table set or document the gap
   permanently once GNOME 48+/Plasma 6.3+ is the floor.
6. **CI on a real compositor.** The Wayland protocol paths are untestable
   under xvfb. Evaluate a headless CM-capable compositor in CI
   (`kwin_wayland --virtual` in the Flatpak SDK image) to exercise
   `wayland_cm_probe` and `wayland_output_hdr` end-to-end.
7. **Upstreaming split.** The parts worth offering to upstream Cine
   independently of HDR: the FBO-pool renderer with `GLTextureBuilder`
   (fixes the `GSK_RENDERER=gl` pin), the Niri auto-pin, and the
   diagnostics dialog skeleton.
