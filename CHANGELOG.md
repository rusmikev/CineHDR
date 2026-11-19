# Changelog

Release notes for CineHDR. Entries are taken from the AppStream metadata in
`data/io.github.rusmikev.CineHDR.metainfo.xml.in`, which remains the source
for what Flatpak and software centers display. Version `X.Y.Z.N` means upstream
Cine `X.Y.Z` plus CineHDR revision `N`.

## 1.8.8.1 (2026-11-19)

- Merged upstream Cine `main` (`e8c84e1`: preserving `mpv.conf` startup options, improved session-restore window sizing, subtitle font selection, popover height limits, and updated translations) while keeping the standard Flatpak on `libmpv v0.41.0` with the screenshot fix and legacy renderer default.
- Updated the standard Flatpak to GNOME Runtime 51. Rechecked SDR playback and HDR10 Wayland submission on GNOME with AMD graphics.
- Validated HDR10 output and transitions between HDR and SDR displays on GNOME with Intel graphics.
- Validated 1080p SDR playback on GNOME and Plasma Wayland with NVIDIA PRIME, CUDA interop, and NVDEC on a GeForce RTX 3050.
- Validated HDR-to-SDR tone mapping on Plasma with AMD graphics when GTK lacks the sRGB transfer function.
- Moved validation tooling outside the standard Flatpak. Separated the experimental GPU Next build from the standard package.
- Updated the README with installation instructions, platform test results, and known limitations.

## 1.8.8.0 (2026-11-09)

- Merged upstream Cine v1.8.8 (fixed MPRIS registration ownership, reset screen inhibit cookie on window close, added Swedish and Persian translations)
- GNOME 51 and Mutter 51 platform verification with native Wayland color management v2

## 1.8.7.0 (2026-11-02)

- Merged upstream Cine v1.8.7 (improved startup, dynamic window sizing based on screen size, fixed mouse click handling)
- Validated HDR10 and HLG output on GNOME with AMD graphics, including live HDR mode transitions
- Fixed shutdown cleanup during playback after display changes

## 1.8.5.2.0 (2026-08-23)

- Merged upstream v1.8.5 and updated yt-dlp to 2026.08.19
- Integrated experimental libmpv gpu-next backend selection and validation harness
- Fixed GTK4 paused window resize redraws and buffer sizing lifecycle
- Enhanced GL synchronization and error reporting

## 1.8.4.1.1 (2026-08-17)

- Merged upstream v1.8.4 updates and improvements
- Hardware-accelerated video flip via GLSL shaders (flip-h, flip-v) with hwdec support
- Fixed Flatpak Wayland color-management protocol cleanup
- Improved pause synchronization and MPRIS input handling
- Full Wayland HDR Passthrough and color management engine

## 1.8.2.1.0 (2026-08-08)

- Merged upstream v1.8.2 updates and new features
- Added A-B Loop button and tristate loop mode (off / file / playlist)
- Persisted loop state across application restarts
- Improved Drag-and-Drop file indicator
- Optimized async mpv property setters
- Maintained full Wayland HDR Passthrough engine

## 1.8.1.1.0 (2026-08-01)

- Merged upstream v1.8.1 updates and optimizations
- Asynchronous thumbnail preview seek based on mpv seeking state
- Disabled thumbnail previews for audio-only streams
- Corrected MPRIS Seeked D-Bus signal timing
- Maintained full Wayland HDR Passthrough engine

## 1.8.0.1.0 (2026-07-28)

- Merged upstream v1.8.0 updates and improvements
- Hardware-accelerated thumbnail previews using ThumbPreviewGLArea
- Added Graphics Offload preference
- Timeline UX enhancements and scrubbing pause feature
- Maintained full Wayland HDR Passthrough and diagnostics engine

## 1.7.4.1.0 (2026-07-22)

- First major release of CineHDR
- Full Wayland HDR support (Rec.2100 PQ / 16-bit float)
- HDR Diagnostics overlay
- Dynamic auto target-peak detection based on monitor luminance
- Fallback logic for SDR screens and missing Wayland protocols
- Refactored OpenGL rendering pipeline to eliminate tearing and race conditions
