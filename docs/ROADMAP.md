# CineHDR Roadmap

Short, ordered list of the next engineering steps. The detailed rationale for
items 1–2 lives in `docs/HDR.md` (invariants E–G); this file only tracks what
comes next.

1. **Live monitor-state updates.** Replace the 2 s TTL poll in
   `wayland_output_hdr` with a persistent `image_description_changed`
   listener: keep the private queue alive and pump it from a custom `GSource`
   using `wl_display_prepare_read_queue()` / `wl_display_read_events()` /
   `wl_display_dispatch_queue_pending()` on the display fd. Invalidate caches
   and re-apply HDR settings from the event instead of polling.
2. **Auto `target-peak` from the output.** When pass-through is active and the
   output's image description reports `max_lum` well below the stream's
   `sig-peak`, feed `target-peak = max_lum` to mpv so it tone-maps *inside*
   PQ to the monitor's real capability instead of relying on the compositor
   clip. Needs A/B validation on a mid-range (≈400–600 nit) HDR display.
3. **`target-colorspace-hint` cleanup.** The property is a no-op under the
   libmpv render API (mpv does not own the swapchain). Remove it from
   `HdrController`, the restore snapshot, the diagnostics line and the nine
   test assertions in one dedicated commit.
4. **xx_color_manager_v4 output query.** The monitor-state probe currently
   binds only the ratified `wp_color_manager_v1`. GTK 4.16/4.17 systems
   (Plasma 6.2 era) negotiate `xx_color_manager_v4`, whose event tables
   differ; either add the second interface table set or document the gap
   permanently once GNOME 48+/Plasma 6.3+ is the floor.
5. **CI on a real compositor.** The Wayland protocol paths are untestable
   under xvfb. Evaluate a headless CM-capable compositor in CI
   (`kwin_wayland --virtual` in the Flatpak SDK image) to exercise
   `wayland_cm_probe` and `wayland_output_hdr` end-to-end.
6. **Upstreaming split.** The parts worth offering to upstream Cine
   independently of HDR: the FBO-pool renderer with `GLTextureBuilder`
   (fixes the `GSK_RENDERER=gl` pin), the Niri auto-pin, and the
   diagnostics dialog skeleton.
