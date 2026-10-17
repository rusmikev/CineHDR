# Model usage log

This log is evidence for tuning `docs/MODEL_POLICY.md`. It records milestone
outcomes, not token accounting promised by any subscription plan.

| Date | Work | Route | Retries / escalation | Verification and outcome |
| --- | --- | --- | --- | --- |
| 2026-08-19 | GPU Next integration decision | Architect: `gpt-5.6-sol`, high | One decision pass | ADR-0001 accepted with backend, fallback, color, rollback, and gate invariants. |
| 2026-08-19 | Backend selector and first scaffold | Engineer: `gpt-5.6-terra`, medium | One review correction: move `depth` from the FBO structure to the top-level Render API parameter | Focused tests, compile check, and full Meson suite passed. |
| 2026-08-19 | First-frame integration debugging | Primary session within ADR-0001 | python-mpv integer-parameter incompatibility escalated for milestone review | Patched and legacy SDR first frames passed; patched HDR first frame used `GL_RGBA16F`/16; system libmpv `auto` fallback passed. |
| 2026-08-19 | Milestone architecture review | Architect: `gpt-5.6-sol`, high | First review requested one P1 fix; second review approved it | FBO restoration moved to `finally`; failed renderer is latched and stopped without hot-swap; regression test and HDR smoke test passed. |
| 2026-08-20 | External source-level review follow-up | Architect: `gpt-5.6-sol`, high | External reviewer identified GPU Next `depth` no-op and GL lifecycle risk; one architecture pass accepted both | ADR amended: `internal_format` is authoritative for GPU Next, Gate 2 uses controlled differences, DV requires version-qualified capability; GL lifecycle fix required before Gate 1 closes. |
| 2026-08-20 | User-facing renderer selection | Architect: `gpt-5.6-sol`, high; implementation: Engineer tier | Architecture fixed the process-start boundary, environment priority, saved-preference fallback, and conservative capability claim before implementation | Persistent enum, output-menu setting, fallback UX, diagnostics, focused tests, and Meson verification added; no runtime renderer swap. |
| 2026-08-20 | User-facing selection milestone review | Architect: `gpt-5.6-sol`, high | Review found one product-accessibility gap and three diagnostic wording/state gaps | Added the always-accessible Preferences mirror, explicit renderer failure states, truthful environment-override status, and backend-specific conservative Dolby Vision reporting; hot-swap remains prohibited. |
| 2026-08-20 | Dolby Vision path and architecture limit review | Architect: `gpt-5.6-sol`, high | One source-level decision pass after a real Profile 8 report contradicted the conservative diagnostics | Confirmed that the pinned `opengl-next` path carries mapped RPU data to libplacebo; separated path evidence from pixel validation, retained the Profile 5 gate, rejected native-DV/FEL claims, and identified HDR-to-HDR tone mapping as distinct from direct passthrough. |
| 2026-08-22 | Repeatable real-GPU validation runner | Primary session implementing accepted ADR-0001 Gate 1 criteria | No architecture escalation; tooling is opt-in and does not alter renderer lifecycle or color policy | Added quick/30-minute soak interaction plans, conservative PASS/WARN/FAIL evaluation, GL/FBO/drop/memory/VRAM evidence, path-safe reports, focused tests, and Meson registration. |
| 2026-08-22 | Controlled GL lifecycle validator | Primary session implementing the lifecycle criterion already accepted in ADR-0001 | No architecture escalation; production ownership and failure policy remain unchanged | Added an opt-in two-cycle unrealize/realize plan, successful-context generation evidence, post-cycle playback/seeks, and preservation checks for GPU Next, HDR observers/state, and the render target. |
| 2026-08-22 | First lifecycle failure analysis | Architect-sensitive review from real-hardware evidence and pinned mpv source | Reopened the lifecycle detail after generation 1→2 produced no paused frame; rejected a validator-only seek workaround | Confirmed `mpv_render_context_free()` tears down the VO and deselects video; ADR now requires capture and explicit restoration of the prior video selection after a new context, with restart on failure. |
| 2026-08-22 | SDR/HDR transition matrix | Architect-sensitive color-invariant validation within ADR-0001 | Saved `force-hdr` would make an SDR surface check meaningless; selected an in-memory `auto` override confined to the isolated validator | Defined a preflighted SDR → HDR → SDR → HDR matrix covering source detection, actual published format/color state, stale DV metadata, unchanged renderer context, playback, and seeks. |
| 2026-08-22 | First transition measurement review | Primary session reviewing real-hardware evidence | The color matrix passed, but generic soak thresholds compared cold 1080p SDR with warm 4K HDR and counted file-reload VO timing as steady playback | Retained raw counters, changed leak evaluation to first-vs-second warmed HDR state, and made VO drops spanning deliberate reloads informational; strict decoder/GL/FBO/action checks remain. |
| 2026-08-22 | Accepted SDR/HDR transition run | Primary session applying the amended measurement criteria | One repeat after correcting the comparison basis | PASS on RX 9060 XT/Mesa 26.1.7: SDR → HDR → SDR → HDR, unchanged context, correct format/color-state pairs, stale DV cleared on SDR, VAAPI active, zero decoder/GL/FBO failures, and no repeated-HDR RSS/VRAM growth. |
| 2026-08-22 | SDR soak scope correction | Primary session reviewing the real report against the Gate 1 invariant | The generic soak correctly preserved saved `force-hdr`, so BT.709 content used the 16-bit HDR surface and could not close the 8-bit SDR criterion | Kept that run as valid force-HDR stability evidence and added strict `sdr-soak`: BT.709 preflight, process-local auto mode, and mandatory RGBA8/sRGB state before the duration segment. |
| 2026-08-22 | First strict SDR-soak startup | Primary session reviewing a fail-before-measurement report | Playlist count was checked before mpv's asynchronous load command had populated the playlist; no duration test ran | Moved exact playlist cardinality into the existing bounded readiness wait and applied the process-local auto mode immediately; renderer behavior was not implicated. |
| 2026-08-22 | Second strict SDR-soak startup | Primary session tracing the persistent playlist-cardinality timeout | The isolated process still restored the user's saved playlist before the supplied SDR file was appended | Validation windows now skip saved-playlist restore and reject session-save actions while retaining the strict one-file invariant; normal CineHDR session behavior is unchanged. |
| 2026-08-22 | Third strict SDR-soak startup | Primary session using the added playlist-cardinality telemetry | The validator observed seven entries after saved-session isolation; CineHDR's normal mpv `autocreate-playlist=filter` had discovered neighboring media | Validation setup now disables automatic directory playlist creation before loading explicit paths; production playlist behavior remains unchanged. |
| 2026-08-22 | Gate 1 milestone closeout | Architect: `gpt-5.6-sol`, high | One evidence review; retained both the report WARN and the unresolved RSS observation | Conditional PASS on RX 9060 XT/Mesa 26.1.7: strict SDR, HDR/DV duration, GL lifecycle, and SDR/HDR transition criteria completed. Five VO drops (0.011%) remain WARN; +50.4 MiB RSS is not treated as proof of either a leak or leak-freedom. The result is explicitly not certification. |
| 2026-08-22 | Pinned Flatpak dependency strategy | Architect: `gpt-5.6-sol`, high | One dependency review expanded the headline stack to include the tested libdovi feature boundary | ADR-0003 accepted one custom libmpv containing both startup-selected APIs, exact mpv/FFmpeg/libplacebo/libdovi pins, legacy default and creation-only fallback, no runtime hot-swap, and unchanged conservative Dolby Vision policy. Bounded manifest implementation and non-GPU verification follow at Engineer tier. |
| 2026-08-22 | Pinned Flatpak manifest implementation | Engineer: `gpt-5.6-terra`, medium; primary review | Primary review corrected the first generated Cargo source list to create a real vendored tree, checksum metadata, and offline Cargo configuration | The candidate manifest now pins mpv, FFmpeg, libdovi, libplacebo, and every Cargo archive; build assertions cover the intended FFmpeg release/ABI and libplacebo Dolby Vision features. Static manifest tests and the full Meson suite verify the dependency contract without GPU access. |
| 2026-08-22 | Clean pinned Flatpak candidate and local bundle | Dependency boundary: Architect `gpt-5.6-sol`, high; bounded build work: Engineer `gpt-5.6-terra`, medium; primary verification | Flathub's newest Rust extension object returned 404, so the previous complete 25.08 commit was recorded; the checksum-identical Debian uchardet mirror replaced a timed-out primary; the root Cargo crate needed an explicit archive type | A clean build completed FFmpeg 8.1.2 → libdovi 3.3.2 offline → libplacebo 7.360.1 with dovi/libdovi → custom mpv `g97179bce7` → CineHDR. Build-time ELF assertions and loader inspection proved the pinned `/app` linkage. A 46,011,816-byte ignored local bundle was exported at SHA-256 `d6854f680af95b204d8e97705d56e2aba7954bff551d18ed0a1de94bef4375a3`; focused tests and both host and Builder-environment Meson suites passed 7/7. Nothing was installed, GPU-launched, or published. Short hardware smoke checks remain open. |
| 2026-08-22 | Local Flatpak hardware-smoke hand-off | Architect boundary: primary `gpt-5.6-sol`, high; runner implementation: Engineer `gpt-5.6-terra`, medium; primary review | One review correction changed the install hint from uninstall-first `--reinstall` to non-destructive `--or-update`; no renderer or validation-policy escalation | Added a quick-only installed-candidate runner with path-scoped filesystem overrides, strict startup GPU Next, log/report freshness checks, and no automatic install or device permission changes. Legacy and GPU Next checks are separate processes, preserving the no-hot-swap invariant. Static runner tests pass; real GPU execution remains a user-run checkpoint. |
| 2026-08-22 | No-stage-cache Flatpak rebuild comparison | Architect interpretation: primary `gpt-5.6-sol`, high; local build execution within accepted ADR-0003 | One independent rebuild; no renderer tests repeated | The complete stack rebuilt with Builder stage-cache disabled. OSTree reused all 214 content objects and wrote zero content bytes, proving identical packaged tree content under the recorded SDK/pins. Commit metadata still differed (`708dbbab…` vs `cc7ae181…`), producing different bundle sizes and SHA-256 values, so the claim remains dependency/content reproducibility rather than bit-for-bit artifact reproducibility. |
| 2026-08-23 | First installed Flatpak GPU Next quick checks | Architect evidence review: primary `gpt-5.6-sol`, high; bounded runner correction: Engineer `gpt-5.6-terra`, medium | The interaction plan passed twice, but the packaging checkpoint stays open because hardware decoding was inactive; the first logging correction exposed that Python root logging did not subscribe to libmpv messages, so the installed-artifact runner now injects python-mpv's validation-only verbose `log_handler` | Both `WARN` runs on RX 9060 XT/Flatpak Mesa 26.1.6 kept the exact pinned stack and `opengl-next` active, rendered DV Profile 8 to `GL_RGBA16F`/Rec.2100 PQ, passed all 11 actions, and recorded zero decoder/GL/FBO failures. VO drops were five then four; `hwdec=no` repeated. One final short run is justified solely to capture VAAPI negotiation, not as another soak. Normal CineHDR and the installed bundle are unchanged. |
| 2026-08-23 | Flatpak VAAPI device-creation root cause and dependency correction | Dependency/root-cause architecture: primary `gpt-5.6-sol`, high; bounded manifest/tests: Engineer `gpt-5.6-terra`, medium; primary build and evidence review | Three verbose quick runs established failure before libva initialization. Source/ELF review found that the candidate enabled FFmpeg VAAPI and mpv `vaapi-wayland` but omitted mpv `drm`/`vaapi-drm`; the first corrected build exposed a missing build-only PNP database, so the Architect boundary pinned hwdata rather than adding a runtime driver override | Added exact libdisplay-info 0.3.0 and build-only hwdata 0.410 pins, fail-fast mpv DRM/VAAPI options, and ELF assertions for libdrm/libdisplay-info/libva-drm. The clean build passed, final features include `drm`, `vaapi`, `vaapi-drm`, and `vaapi-wayland`, hwdata was removed from runtime, and a separate 46,090,048-byte bundle was exported at SHA-256 `92bc7e3f…`. One short hardware run is required to prove active `vaapi-copy`; no soak repeat is justified. |
| 2026-08-23 | Corrected Flatpak GPU Next hardware acceptance | Architect evidence review: primary `gpt-5.6-sol`, high; user-run real GPU command | One corrected-package quick run; no stability retry and no soak | PASS on the exact installed commit: libva initialized the Flatpak Radeon driver, mpv selected `vaapi-copy`, `opengl-next` and the 16-bit Rec.2100 PQ target stayed active, all 11 actions passed, and decoder/VO/GL/FBO failures were zero. The +242.1 MiB raw warmed-sample VRAM delta is retained for Gate 2 settling work, not called a leak. CUDA probe failures from `hwdec=auto` on a non-NVIDIA system and repeated multi-RPU sample warnings do not broaden or invalidate the conservative DV policy. |
| 2026-08-23 | Gate 2 pixel-validation architecture | Architect: primary `gpt-5.6-sol`, high; bounded harness implementation routed to Engineer `gpt-5.6-terra`, medium | One decision pass separated renderer-FBO evidence from decoder/compositor/display evidence | ADR-0004 selects deterministic lossless HDR/SDR fixtures, separate embedded legacy/GPU Next contexts on one pinned stack, exact CineHDR FBO formats, strict provenance, and no silent skips. Software-decode offscreen readback is the first layer; real Radeon VAAPI-copy, GTK/Wayland, dithering, workflow, performance, and settling checks remain explicit later layers. |
| 2026-08-23 | Gate 2 first instrumentation slice and software baseline | Bounded scaffold: Engineer `gpt-5.6-terra`, medium; architecture/evidence review and corrective integration: primary `gpt-5.6-sol`, high | Review corrected RGB matrix signalling, `flip_y=False` row mapping, exact runtime-stack identity, FFV1 frame metadata/bitexact muxing, the mpv `colorlevels` property, initial-empty-frame rejection, and caller-FBO rebinding | Deterministic HDR/SDR FFV1 fixtures, separate `opengl`/`opengl-next` EGL probes, strict JSON provenance/comparability, and environment-independent tests are implemented. Two clean generations were byte-identical. The first llvmpipe report completed all four captures on mpv `g97179bce7`/FFmpeg `8.1.2`/libplacebo `7.360.1`; diagnostic maximum sampled deltas are 0.036132812 HDR and 0.215686262 SDR. Gate 2 remains open: no threshold was fitted to this observation and no Radeon/Wayland/VAAPI claim was made. |
| 2026-08-23 | Gate 2 target-Radeon software-decode checkpoint | Architect evidence review: primary `gpt-5.6-sol`, high; user-run explicit dGPU command | The first hardware command selected the integrated Raphael GPU; one short repeat was justified with the documented PCI `DRI_PRIME` selector, without repeating a soak | Complete comparable HDR/SDR evidence on RX 9060 XT (`radeonsi`, `gfx1200`) / Mesa 26.1.7: both fresh renderer contexts loaded the exact pinned stack and intended FBOs with no mpv/GL/FBO errors, and all structural color diagnostics held. Maximum sampled A/B deltas matched llvmpipe at 0.036132812 HDR and 0.215686262 SDR. The real-Radeon software row is accepted, but the values remain diagnostic; Gate 2, VAAPI-copy, Wayland/display, and color certification remain open. |
| 2026-08-23 | Gate 2 strict VAAPI-copy harness extension | Architecture/review: primary `gpt-5.6-sol`, high; bounded implementation: Engineer `gpt-5.6-terra`, medium | Architect review added failed-run decode evidence after the first implementation discarded the actual fallback value | Added an opt-in `no|vaapi-copy` decode dimension with software default unchanged. Every renderer remains a fresh process; accepted hardware evidence requires requested and active `vaapi-copy` on both APIs and one GL device, while fallback is a strict self-diagnosing FAIL. The exact RGB FFV1 fixture is tried before introducing a lossy/YUV variable. Unit/static/compile checks pass; real VAAPI execution remains a user-run checkpoint. |
| 2026-08-23 | Gate 2 hardware-decodable fixture and probe synchronization | Architecture/container/synchronization: primary `gpt-5.6-sol`, high; bounded generator, runner, probe, and tests: Engineer `gpt-5.6-terra`, medium | RGB FFV1 correctly rejected VAAPI-copy. Matroska was rejected for random UID bytes; elementary HEVC was rejected for absent timestamps; direct deterministic MP4 was selected. Cold llvmpipe then exposed a legacy shader-setup timeout, so warm-up was separated and recorded instead of weakening measured-frame checks. | Added deterministic full-range HEVC Main 10/yuv420p10 MP4 fixtures with exact decoded-YUV and all-frame identity, explicit RGB/YUV error provenance, fresh process/API isolation, update-callback/render-swap synchronization, and strict primary-structure readiness. Two clean encodes were byte-identical. The fresh-cache software report completed all four measured captures after its explicitly non-evidence warm-up; no threshold, VAAPI, real-GPU, Wayland/display, or certification claim was added. |
| 2026-08-23 | Gate 2 target-Radeon VAAPI-copy acceptance and next hand-off layer | Evidence and GTK/GDK/Wayland boundary: primary `gpt-5.6-sol`, high; real GPU command: user | One changed-input hardware run; no retry. The result was accepted without fitting the measured deltas to a threshold. | Both APIs completed HDR/SDR on RX 9060 XT/Mesa 26.1.7 with active `vaapi-copy`, `p010`, exact pinned stack/FBO targets, structural diagnostics, and zero mpv/GL/FBO errors. The embedded-FBO VAAPI row is closed, not Gate 2. The next ADR slice validates actual built GDK texture formats/color states in separate legacy/GPU-Next Wayland processes; compositor/display claims remain excluded. |
| 2026-08-23 | Gate 2 live GTK/GDK publication harness | Publication boundary and acceptance: primary `gpt-5.6-sol`, high; bounded implementation: Engineer `gpt-5.6-terra`, medium | Architect review required pre-load pause/keep-open isolation, exact ordered state evidence, actual scaled-widget texture dimensions, strict pinned-runtime identity, and reportable setup failure before hand-off. | Added an opt-in Wayland publication mode and a single local launcher that verifies exact deterministic HEVC provenance, then runs legacy and GPU Next in separate processes. PASS requires all four actual GDK texture states, newer generations, Wayland GTypes, active `vaapi-copy`, the expected API, and no renderer/GL/FBO failures. The full Meson suite passes; no sandbox GPU run was made, so the real Radeon publication checkpoint remains open. |
| 2026-08-23 | First live publication attempt and decode-path correction | Architect evidence review and correction: primary `gpt-5.6-sol`, high; real GPU command: user | The launcher stopped after the legacy process as designed: automatic hardware-decoder selection chose direct `vaapi`, while the accepted comparison requires `vaapi-copy` in both processes. | The failed SDR state still proved an actual correctly sized `GdkGLTexture` with `R8G8B8A8`/sRGB, Wayland display/surface types, legacy `opengl`, and the exact pinned runtime. The criterion was not weakened. Publication mode now requests `vaapi-copy` before either file is loaded; one short two-process repeat is justified by this changed input configuration. |
| 2026-08-23 | Accepted live GTK/GDK Wayland publication row | Architect evidence review: primary `gpt-5.6-sol`, high; real GPU command: user | One repeat after the explicit pre-load `vaapi-copy` correction; no additional run requested. Post-report file-error warnings were timestamped to the validator's deliberate four-second shutdown and did not occur inside the accepted state sequence. | Separate legacy and GPU Next processes passed the exact SDR -> HDR -> SDR -> HDR matrix on the RX 9060 XT with generations 3 -> 5 -> 7 -> 9, actual matching GDK texture formats/color states/dimensions, Wayland GTypes, active `vaapi-copy`, expected APIs, and the pinned runtime. This closes the GTK/GDK object-publication row only; compositor image-description acceptance, display output, color certification, and Gate 2 remain open. |
| 2026-08-23 | Gate 2 Wayland surface-protocol architecture | Architect: primary `gpt-5.6-sol`, high | One decision pass after accepted GDK publication; local GTK/KWin/protocol inspection selected passive client tracing instead of adding another protocol owner. Review of the installed v3 XML added `ready2` alongside deprecated `ready`. | ADR-0004 now defines an opt-in `WAYLAND_DEBUG=client` parser requiring the ratified color-manager global/bind, perceptual intent, a version-appropriate ready image description, surface submission, same-surface commit, and no protocol rejection. It deliberately does not require SDR/PQ surface alternation because GTK may transform both texture states into a compositor-preferred encoding, and it makes no display/color claim. Bounded harness implementation follows at Engineer tier. |
| 2026-08-23 | Gate 2 Wayland surface-protocol harness | Bounded implementation: Engineer `gpt-5.6-terra`, medium; grammar/API review: primary `gpt-5.6-sol`, high | Architect review corrected synthetic `@` object syntax to canonical libwayland `#`, corrected `supported_render_intent` to XML-defined `supported_intent`, added the identity argument to v1 `ready`, and required explicit backend linkage. | Added optional `--protocol-trace` to the unchanged two-process publication launcher and a pure strict parser with ready/ready2, dynamic object association, same-surface commit, failure rejection, hash-linked JSON, and ten synthetic tests. Full Meson 9/9 passes. No real Wayland trace was run in the sandbox; one short user-run trace remains the acceptance checkpoint. |
| 2026-08-23 | First real Wayland surface trace and GTK opt-in boundary | Evidence/GTK-Wayland architecture: primary `gpt-5.6-sol`, high; bounded runner/tests: Engineer `gpt-5.6-terra`, medium; primary correction | The legacy GDK publication passed, but the strict parser correctly found no color-managed surface submission. Local GTK help showed that the installed experimental backend requires explicit `GDK_DEBUG=color-mgmt`; primary review also corrected the first Engineer list separator from unsupported comma to GTK-documented colon. | Retained the failed trace and unchanged strict criterion. Trace mode alone now adds `GDK_DEBUG=color-mgmt` alongside passive client tracing; ordinary CineHDR and zero-argument publication runs remain unchanged. One short changed-input repeat is justified. Even a PASS will describe validation-only surface submission, not product default behavior, compositor transformation, display output, color accuracy, or certification. |
| 2026-08-23 | GTK/KWin surface-submission capability closeout | Architect/evidence review: primary `gpt-5.6-sol`, high; structured failure reporting: Engineer `gpt-5.6-terra`, medium | The changed-input legacy run proved the GTK opt-in was active: GTK bound the KWin v2 global at interface v1 and received perceptual intent plus `done`, then destroyed the manager without `get_surface` or `set_image_description`. The failure precedes any renderer-specific protocol chain, so starting GPU Next would not satisfy it. | Closed this protocol row as unavailable, not PASS, on GTK 4.22.4/KWin 6.7.4. Added hash-linked CLI FAIL JSON while preserving exit 1 and strict raising APIs. Updated HDR docs/roadmap so a registry global is only a precondition, never native-HDR/compositor-acceptance evidence. No further repeat is justified without changed toolkit/compositor implementation; product fallback behavior remains unchanged pending a separate product decision. |
| 2026-08-23 | Gate 2 bounded resource-settling architecture | Architect: primary `gpt-5.6-sol`, high | The existing 31-minute soaks cannot distinguish retained pools/back-buffer from per-cycle resource growth, and repeating them would violate the evidence policy. Selected deterministic repeated allocation endpoints instead of fitting a byte threshold to one observation. | ADR-0004 defines separate legacy/GPU-Next processes, exact paused HEVC/VAAPI/pinned-stack inputs, one unmeasured warm-up, three identical resize and HDR/SDR cycles, process-local RSS/DRM evidence, and unload cooldown. Strictly increasing same-state RSS or VRAM endpoints yield WARN; PASS remains bounded structural evidence, never leak-freedom. Long-media demuxer behavior stays explicitly out of scope. |
| 2026-08-23 | Gate 2 bounded resource-settling harness | Bounded implementation: Engineer `gpt-5.6-terra`, medium; architecture and integration review: primary `gpt-5.6-sol`, high | Primary review required pre-unload `vaapi-copy` and pinned-runtime evidence, allowed compositor-negotiated dimensions only when all three actual endpoints match, and kept post-unload decoder state out of the cooldown criterion. | Added a no-argument local launcher and opt-in validator mode with one warm-up, three comparable cycles, strict action/context/GDK/GL/FBO/resource evidence, and hash-pinned fixtures. Separate legacy/GPU-Next processes preserve startup-only renderer selection. Strictly rising same-state RSS or process VRAM yields WARN without a byte threshold. Unit/static and full Meson suites pass; no sandbox GPU run was made. |
| 2026-08-23 | First resource-settling run and paused-resize synchronization | Architecture/evidence correction: primary `gpt-5.6-sol`, high; real GPU command: user | Legacy passed warm-up but failed the first resize because the logical window changed while the paused GDK texture and frame generation stayed old. This preceded all resource endpoints and is not renderer or leak evidence. Source/API review selected an explicit previous-frame redraw on target-size change rather than weakening texture-dimension checks. | `MpvVideoWidget` now coalesces GTK width/height notifications into its existing Render API path after a texture exists; libmpv update or forced target redraw can render, and shutdown clears both pending states. The evidence revision is v2, preserving the v1 FAIL separately. Focused/static/full tests precede one justified bounded repeat; no soak repeat. |
| 2026-08-23 | Second resource-settling integration failure and GTK allocation correction | Architecture/evidence correction: primary `gpt-5.6-sol`, high; real GPU command: user | The v2 report proved the new revision loaded but GTK 4.22.4 did not emit `notify::width/height` for widget allocation, so the same legacy resize failed before endpoints. The renderer, decoder, runtime and GL/FBO state remained correct. | Replaced the ineffective property hook with Gtk.Widget's `do_size_allocate` vfunc, explicitly allocating the sole Gtk.GLArea child and tracking logical size and scale factor. Tests now execute child allocation and the forced-redraw decision rather than only inspect source. Evidence revision v3 preserves both prior FAILs; one bounded changed-integration repeat is justified, no soak. |
| 2026-08-25 | Bounded validation governance and evidence-status correction | Architect: primary `gpt-5.6-sol`, high | Review of repeated Gate 2 integration failures found that failure-signature escalation alone did not cap total target-hardware executions; supplemental GitHub changes also rolled weak or absent row evidence into Gate 2/3 PASS claims | ADR-0006 limits an attempt family to an initial hardware run plus one evidence-driven correction, then requires `HOLD`, Architect review and explicit user approval. Gate 2W remains independent. External Antigravity/Gemini execution is restricted to an immutable Operator hand-off. Unsupported Gate 2/3 sign-off was withdrawn while accepted Gate 1/FBO/VAAPI/GDK rows were preserved. No GPU or duration test was run. |
| 2026-08-25 | Intel/NVIDIA smoke oracle v2 | Architecture and evidence contract: primary `gpt-5.6-sol`, high; bounded implementation in the primary session because proactive delegation was unavailable | Replaced the historical aggregate 16-process predicate with independently selectable vendor/fixture rows and no automatic retry. An initial empty render, missing fixture, wrong media path, stale runtime, API fallback, stub, early exit, or file warning now prevents PASS. | Added path-safe loaded-media-frame evidence, pinned fixture/runtime/API/GPU/HDR checks, clean preflight, sanitized JSON plus raw-log hashes, and one-pinned-Flatpak execution for both Intel and NVIDIA. Local unit/source checks only; real laptop status remains `NOT_RUN` until an exact source commit and rebuilt Flatpak commit exist. |
| 2026-09-23 | Flatpak GPU Next validation attempt family | Operator: target hardware (Gemini/User) | Run 1 (09:59:12 UTC): quick playback WARN on integrated iGPU (hwdec=no). Run 2 (10:10:05 UTC): GL probe on target dGPU. Run 3 (11:37:47 UTC, user authorized): quick playback WARN on target discrete GPU with opengl-next and vaapi-copy. | WARN (exit 0). Target discrete GPU render, active opengl-next, and active vaapi-copy confirmed. Physical decode GPU remains Unknown. VO drop observed growth after baseline: 0 (initial sample counter: 21). Fullscreen and multiple RPU warnings unresolved; Gate 2W and Dolby Vision validation remain open. |
| 2026-09-24 | Target GNOME Wayland color-management trace capture (Run 2) and Gate 2W audit | Operator: target hardware (Gemini/User) | Run 1: initial passive trace under GNOME (wayland-trace-20260924-133500); Run 2: single-shot trace capture script (wayland-trace-MgsoJA) after parser fix. Two-run budget exhausted. | Offline trace parser PASS (status: PASS, ready -> set_image_description -> commit on surface #61). Active API and Session status for this capture remain Unknown (no contemporaneous Copy Report). 1514 attaches logged as client protocol calls, not confirmed presented frames. Combined renderer+submission row placed on HOLD ("нет свидетельства из той же сессии"). Gate 2W remains OPEN. Previously accepted independent rows preserved. No further hardware runs permitted without Sol proposal, Astra review, and user authorization. |
| 2026-09-26 | Target GNOME Wayland color-management trace capture (Run 3, Candidate v4) and Gate 2W narrow row acceptance | Operator: target hardware (Gemini/User); Engineer: Gemini | Run 1 (2026-09-24): initial passive trace under GNOME (`wayland-trace-20260924-133500`). Run 2 (2026-09-24): single-shot trace (`wayland-trace-MgsoJA`) with parser PASS but missing contemporaneous Copy Report -> HOLD. Run 3 (2026-09-26, user-authorized exceptional ADR-0006 run): single-shot trace (`wayland-trace-Z4GghM`, script SHA-256 `1139ef43...`) on Candidate v4 (app commit `8ed86c0b...`, target discrete GPU). | PASS (exit 0). Synchronous autonomous diagnostics export (`first-media-frame-confirmed`) confirmed active `opengl-next`, `session_status=active`, `render_target=GL_RGBA16F` (depth 16), `Gdk.ColorState=Rec.2100 PQ`, and `vaapi-copy` in the same session as Wayland color-management protocol submission and commit on surface #61 (`io.github.rusmikev.CineHDR`). Narrow correlated row accepted as PASS. Cleanup status raw `FAILED_STILL_RUNNING` separated from subsequent observation (`CONFIRMED_ABSENT` at 07:50:18 UTC). Gate 2W remains OPEN overall: this correlated GPU Next first-frame/ready/set/commit observation does not by itself establish the complete SDR→HDR→SDR→HDR publication matrix or the separate legacy backend protocol row. Direct scanout, display photometry, and per-frame linkage are explicit non-claims; KDE was not started. The previously accepted GDK publication matrix retains its separate status on its original stack. |
| 2026-10-03 | Gate 2W Run 4 offline qualification: SDR fixture reproduction, Flatpak library provenance proposal, and rerun brief | Engineer: Gemini | Restored SDR fixture SHA-256 `41393a0e...` with FFmpeg 8.1.2 Lavf62.12.102 (preserving `2ec07e...`); dry-run preflight confirmed VERIFIED for both fixtures. Identified Flatpak `/app/lib/` provenance blocker against `publication_runtime_errors`; added negative regression tests reproducing rejection and foreign library substitution; evaluated under MODEL_POLICY.md and prepared ADR amendment proposal without editing product code; added ADR-0006 Gate 2W Run 4 rerun brief. | All 432 unit tests pass cleanly. No hardware run executed; Gate 2W remains OPEN pending independent review of the proposal and user authorization. |
| 2026-10-04 | Gate 2W oracle hardening & Astra high review blocker resolution | Engineer: Gemini | Resolved 4 reproduced blockers from Astra high review: eliminated cross-connection ID space confusion by unconditionally rejecting descriptor ID recreation on the primary composite row (`len(all_creations) > 1`), avoiding fragile heuristics while preserving single-creation Attempt 3; rejected `unset_image_description` between `set_image_description` and `commit`; protected `cleanup_status.json` from `EXIT` handler overwrite; ensured fail-closed handling in `run_wayland_publication_validation.sh` on missing `info` via `flatpak ps`. Added negative regression tests including foreign `wl_callback` ID deletion across multiple registries. | All 481 unit tests pass cleanly (Attempt 3 SHA-256 `2e9d5de1...` verified PASS). `git diff --check` clean. Ready for independent Astra high re-review without build or hardware run; Gate 2W remains OPEN, Run 4 remains HOLD. |
| 2026-10-04 | Gate 2W Run 4 oracle and launcher independent review | Reviewer: `gpt-6-astra`, high (review only) | Independent review of Revision 2 packet and full diff against HEAD `9010e5a`; verified resolution of all 4 blockers without new connection heuristics | VERDICT: ACCEPTED / PASS. Confirmed that unconditional rejection of descriptor ID reuse on the primary composite row (`len(all_creations) > 1`) eliminates the reproduced false PASS from a foreign delete_id during descriptor ID reuse; Attempt 3 narrow row remains valid (`#51` created once, `ready(7)` -> `set` -> `commit`); `unset_image_description` invalidation, `EXIT` trap cleanup preservation, and fail-closed missing-info handling verified. Clears the independent oracle-review prerequisite; Candidate v5 snapshot and local Flatpak build remain separate planned steps. Gate 2W remains OPEN, Run 4 remains on HOLD pending explicit hardware authorization. |

| 2026-10-04 | Gate 2W Run 5 exception and execution brief | Primary Codex engineering; independent `gpt-6-astra`, high review only | First review required exact GPU/runtime/host identity, an enclosing deadline, failure-safe artifact retention and corrected claim boundary; focused re-review accepted all four | Candidate v6 bundle and fixtures verified by SHA-256; 149 focused unit tests and governance checks passed, shell syntax and `git diff --check` clean. Reviewer accepted one bounded Run 5 proposal; installation and hardware execution still await separate explicit user approval. Gate 2W stays OPEN / Run 4 row HOLD. |
| 2026-10-11 | Gate 2W Run 7 Candidate v14 final adjudication | Engineer: Gemini; independent Codex publication review | One bounded GNOME launcher session ran separate legacy and gpu-next containers; both passed four publication states, protocol submission and FBO phases, exited 0, and cleaned up. | Verified GNOME domain CLOSED; cross-compositor Gate 2W CONDITIONAL; Gate 2 and Gate 3 OPEN. Current sanitized decision is `validation-reports/gate2w-run7-adjudication-reconciled-mesa-20261011.json` (SHA-256 `70f8e5f1b658f174439826f378ec93254e17f279e917c35577c4b844f0758a94`). Physical HDR, scanout and per-frame presentation are non-claims; Mesa version unmeasured. |
| 2026-10-04 | Gate 2W Run 5 live validation | Primary Codex Operator following independently accepted bounded brief | One authorized execution only; no retry. Initial legacy SDR passed, HDR switch failed after a file-error event; gpu-next not run | Raw logs and sanitized JSON hashed; test instance cleanup CLEAN and absent. Candidate v5 restored and verified. Gate 2W OPEN, multi-phase row HOLD; HDR file-error root cause unverified. |


## Initial observation

The routing saved the Architect tier from routine scaffold work while still
catching a GPU-state cleanup defect at the milestone boundary. One Engineer
correction and one Architect-requested fix were sufficient; no task required
`max` reasoning or a complete reimplementation.

### 2026-09-23 Native checkout routing reconciliation

- Mirrored the active GPT-6 routing, one-failed-dispatch stop, two-local-attempt
  hand-off, and independent Astra review contract from the main CineHDR policy.
  Historical 5.6 routes above remain records of their original work.
- Preserved the existing Gemini technical guide and added current-policy
  precedence. Corrected only its retained-texture lifetime, unsynchronized
  timeout publication, blanket hardware-matrix, and HDR-proof claims against
  ADR-0001/0006 and current `video_widget.py`. Gemini Flash remains an immutable
  external Operator unless separately given a bounded Engineer brief.
- Focused governance tests passed 10/10 in this native checkout; scoped
  `git diff --check` passed. No player, GPU/Wayland, full suite, dependency
  installation, or remote operation ran here. Live output remains unverified.

### 2026-09-23 Flatpak GPU Next validation attempt family

- Executed Run 1 (Quick playback) at 2026-09-23 09:59:12 UTC: Flatpak candidate loaded `opengl-next` and custom libmpv, producing a WARN report on integrated iGPU with software decoding (`hwdec=no`). Proved packaging and GPU Next startup without stub errors, but failed target discrete GPU and hardware decode targets.
- Executed Run 2 (GL probe) at 2026-09-23 10:10:05 UTC: GdkGLContext probe confirmed target discrete GPU rendering context under Wayland.
- Real-hardware execution budget of two attempts per ADR-0006 was reached; row was placed on `HOLD`.
- **User authorization for exceptional Run 3**: The user explicitly authorized a single third target-hardware execution within this attempt family after offline review and regression testing of corrected `run_flatpak_gpu_validation.sh` (SHA-256 `a7fd5c2a04c487ad01b6a81b29df192ebf8fb7887118513d5112145d15686d9c`).
- **Refined execution brief**:
  - Boundary under test: Pinned Flatpak candidate `4edda5ddca09dd99f8897e3c51738a4f508bc7318029c891e0c4e78229e5d736` with target discrete `DRI_PRIME` routing and pre-load `hwdec="vaapi-copy"` interception on UHD HDR10/Dolby Vision test stream (SHA-256 `fd27b83024d9ecf86d27a9a1698ed28d3eb87ddbfe8692f7751b9db96da909c7`).
  - Falsifiable hypothesis: Simultaneously observe `Active renderer: opengl-next` with `Renderer status: active`, `OpenGL renderer` indicating target discrete GPU, and active hardware decoding `Hardware decoding: vaapi-copy` without fallback or unhandled crash.
  - Disclaimer: Discrete GL renderer proof does not prove VAAPI decoding GPU; physical decoder device remains Unknown without direct kernel/driver VAAPI device telemetry.
  - Exact command template: `timeout --signal=TERM --kill-after=10s 180s bash ./run_flatpak_gpu_validation.sh "<sample-path>" quick`
  - Stop conditions: After this third run, stop immediately. No fourth run, no probes, no self-repair.
- **Execution and observed result of exceptional Run 3 (2026-09-23 11:37:47 – 11:39:06 UTC)**:
  - Command template: `timeout --signal=TERM --kill-after=10s 180s bash ./run_flatpak_gpu_validation.sh "<sample-path>" quick`
  - Exit code: `0`
  - Overall result: `WARN` (exit 0)
  - Report artifact: local report artifact `validation-reports/gpu-next-quick-20260923-163902.txt` (SHA-256: `7c2deb8441ecd416353911f714f7ed48d7637e39866cfa2462e44016055c0559`)
  - Raw log artifact: local raw log artifact `validation-reports/gpu-next-quick-20260923-163750-524705.log` (SHA-256: `9ec99115999c6cd6d4b28886de21aec1f86bff605a1c67292216f5d72501e6fb`)
  - Observed runtime evidence:
    - Active renderer: `opengl-next`, status: `active`
    - OpenGL renderer: target discrete GPU driver string matching target dGPU architecture
    - Hardware decoding: `vaapi-copy` (`vd: Using hardware decoding (vaapi-copy).`)
    - Target render format: `GL_RGBA16F / 16-bit`, published color state: `rec2100-pq`
    - Drops / errors: FBO pool drops: 0, FBO allocation failures: 0, GL errors: 0, decoder drops: 0 (observed growth: 0), VO drops: 0 observed growth after baseline (initial sample counter: 21 at +0.0m baseline; not zero drops across the entire process lifetime)
  - Unresolved observations:
    - Fullscreen confirmation warning (`WARN: enter fullscreen — fullscreen state was not confirmed by GTK`) is retained as an unresolved observation.
    - Multiple RPUs logged during playback (`ffmpeg/video: hevc: Multiple Dolby Vision RPUs found in one AU. Skipping previous.`) are retained as unresolved observations; they are not declared harmless or conforming.
  - Boundaries remaining open:
    - Physical decoding GPU for VAAPI remains `Unknown` (neither mpv nor libva reports the underlying DRM device node in telemetry).
    - Gate 2W (Wayland color-management protocol submission and surface lifecycle) remains `OPEN`.
    - Dolby Vision validation (colorimetric accuracy, Profile 5 reshaping, enhancement layer / multi-RPU processing) remains `OPEN` (pending).
  - Status: Observed result is WARN (exit 0) for discrete GPU Next startup and vaapi-copy in this candidate execution. However, formal row acceptance remains unconfirmed (provenance unverified) because exact source Git provenance was not established for Flatpak candidate 4edda5dd. Historical observations and log artifacts remain recorded and are not declared false. Independent previously accepted rows remain unaffected. The attempt family execution budget of three runs is complete; no fourth run is permitted without a new independent architecture review and explicit user authorization. (Full original command details, environment paths, and execution logs are retained exclusively in local non-public validation backup).

### 2026-09-24 Wayland trace parser correction (offline)

- Sol high proposal received independent Astra high conditional review; Sol medium implemented the bounded parser correction on base Git `4831fefb4f7fe8df74462286b233b39eb579cae3` under ADR-0004.
- Focused Wayland trace tests passed 28/28; validation governance tests passed 11/11. Pure `validate_trace` replay of retained `wayland-client.raw.log` (SHA-256 `6a7f36cffd97c5a0561b6f955fab68d9153b5a5d29eb620345abfa3f5858746d`) returned parser PASS; ignored local evidence is `validation-reports/wayland-parser-offline-replay.json` with parser SHA-256 `09775ed521e0810d071950982f1dcbe4eae01b5fd52dc340df85d9d1512c9748`.
- No playback, GPU/Wayland hardware execution, or remote operation ran. Gate 2W remains OPEN; offline parser replay does not accept a hardware evidence row.

### 2026-09-24 Target GNOME Wayland trace capture and Gate 2W audit

- **Environment and Provenance**:
  - Source Git checkout HEAD: `e41fbeb1dad3b1b463c40030efba587f2341bfb7` (branch `codex/gpu-next-diagnostics`).
  - Installed custom Flatpak candidate: app commit `8c6c861c4832346c611f95d8d1edbd3982c5c69a59dcdf114b144eca9982fc74` (built from source commit `4831fefb4f7fe8df74462286b233b39eb579cae3`), runtime commit `b1935f7a673108616d4fd84564f15cefc9f637481472a8d4d2f0945375d41f4b`.
- **Target Hardware Execution (Run 2)**:
  - Executed validated single-shot capture script `validation-reports/capture_wayland_trace.sh` (SHA-256 `7d7b152dc403445a9e064aaa938653ee17bb89a30ee12223b6ce860101332f67`).
  - Traced Flatpak instance ID `248765847` within observation window `2026-09-24T13:29:47.522831Z` to `2026-09-24T13:30:47.526506Z` (60s timeout, exit code 124).
  - Offline trace parser (`tests/parse_wayland_color_trace.py`, SHA-256 `09775ed521e0810d071950982f1dcbe4eae01b5fd52dc340df85d9d1512c9748`) on raw log `wayland-client.raw.log` (SHA-256 `ba775d0a4ba212168ec6e07dea1614a6ce0d61913a2aa251812fa497c90b3578`) evaluated status `PASS` (`wayland-trace-validation.json`, SHA-256 `bff2c74d58f1f7acd2fb4c75062368db8f27dbe314c5d8d542d040002d3f4255`):
    - Manager `wp_color_manager_v1` advertised at v2 and bound at v1 (id 32).
    - Image description created and received `ready` event (id 51, index 643).
    - Toplevel surface (`wl_surface#61`, `io.github.rusmikev.CineHDR`) received `set_image_description` (index 849) followed by `commit` (index 865).
- **Renderer Evidence and Telemetry Gap**:
  - The capture session raw client log contains zero stdout/stderr logs certifying renderer initialization or active API (only 6 non-Wayland warnings).
  - No contemporaneous Copy Report from Video Output Diagnostics was sampled within the capture window `[13:29:47, 13:30:47]`.
  - Therefore, `Active API` and `Session status` for this specific trace capture remain strictly `Unknown`.
  - The separate earlier session Copy Report (at `12:54:34 UTC`, instance `2622755033`) proved `opengl-next` only for its own process instance; under ADR-0006, evidence from a distinct session cannot be combined or correlated to claim concurrent proof.
- **Surface Attach and Presentation Distinction**:
  - The trace recorded 1514 non-null `attach(wl_buffer#...)` calls on `wl_surface#61`.
  - Per ADR-0006 / GEMINI.md, this count represents client-side protocol calls, not verified video frames scanned out or presented on physical display.
- **Process Absence and Teardown**:
  - Instance `248765847` was confirmed absent in `flatpak ps` output at `13:33:47 UTC` (`instance_absence_check.txt` -> `CONFIRMED_ABSENT`).
  - `KILL_TIMEOUT` in `cleanup_status.txt` was a script misclassification of the non-zero exit from `flatpak kill` (which reported `ошибка: 248765847 не запущен`).
  - The exact termination mechanism remains unknown.
- **Gate 2W and Attempt Budget Governance**:
  - Gate 2W remains `OPEN`.
  - The specific validation row requiring simultaneous proof of active `opengl-next` renderer and Wayland color-management surface submission is placed on `HOLD` with recorded reason: «нет свидетельства из той же сессии» (no evidence from the same session).
  - Previously accepted independent evidence rows (FBO format/depth, VAAPI-copy, GDK texture publication) remain intact.
  - ADR-0006 hardware attempt family limit for GNOME Wayland trace capture is exhausted (2/2 runs completed: Run 1 initial trace, Run 2 corrected parser capture). A third execution is strictly prohibited without a proposal from Sol (high reasoning), independent review from Astra (high reasoning), and explicit authorization from the user.
- **Retained Local Artifacts (Private)**:
  - Local directory: `validation-reports/wayland-trace-MgsoJA/`
  - `instance.txt`: SHA-256 `a3778c357879eeefd4caf4c4cfe488abbef88022977f761ed60075f21330db1f`
  - `instance_info`: SHA-256 `816d5268ab762b137fb5bf8a7f1cacdbe55ef84dbdda54df953c120bc23ba435`
  - `sampler_status.txt`: SHA-256 `16a5d7ca2e1613a570f97007a146205c9a1e522e1459d30cbc65b87343a2ff5b`
  - `start_time.txt`: SHA-256 `1faec2f1814afce364555f2925918b2e854a4379c8e77f8d57cfdf6584beccb6`
  - `end_time.txt`: SHA-256 `e888c0ad85bc488853b9540c9f880c574a90a18e8ea2c2b0e4621c30f94db856`
  - `exit_code.txt`: SHA-256 `ca2ebdf97d7469496b1f4b78958f9dc8447efdcb623953fee7b6996b762f6fff`
  - `cleanup_status.txt`: SHA-256 `fb53801bad2f74ac95374b4c3663d8100f7d24030ee0ebfa3e7510bd83aa4c05`
  - `instance_absence_check.txt`: SHA-256 `3471b85a1a3051eac4c9e4337536811779a6c200ba9fd8710a92cc4d85e436e5`
  - `wayland-trace-validation.json`: SHA-256 `bff2c74d58f1f7acd2fb4c75062368db8f27dbe314c5d8d542d040002d3f4255`
  - `session_facts.json`: SHA-256 `cbfd57433c9fdaa93d1b6e62d95b990e5988868fe4c6bc891ef751f07a581908`
  - `wayland-client.raw.log`: SHA-256 `ba775d0a4ba212168ec6e07dea1614a6ce0d61913a2aa251812fa497c90b3578`
  - Capture script: `validation-reports/capture_wayland_trace.sh` (SHA-256 `7d7b152dc403445a9e064aaa938653ee17bb89a30ee12223b6ce860101332f67`)
  - Raw logs and private media paths remain strictly uncommitted.


### 2026-09-25 User-requested model routing and budget revision

- Scope: shared policy, Codex/Gemini entry points, ADR-0006 routing amendment,
  current Gate 2W guidance and static governance checks. Performed in the
  current Codex session without spawning additional agents; the exact serving
  model identifier was not exposed to this session. This is a policy revision,
  not an independent acceptance review of the application changes.
- Gemini is the default bounded Engineer; GPT-6 Luna serves prescribed low-risk
  work, Sol difficult engineering/proposals/reviews and Astra selective difficult
  or high-risk work. Removed the compulsory Sol-to-Astra sequence and the
  provider-wide restriction of Gemini to Operator or Astra to Reviewer.
- Independent review remains mandatory at sensitive boundaries. It must be a
  separate qualified session/agent with source and evidence access. A hardware
  Operator still cannot repair its own execution or accept a row. Actual model
  availability and the user's budget preference govern routing.
- Official OpenAI model and Codex usage documentation informed the route
  defaults; API pricing is not treated as a subscription quota. No fixed quota
  or unverified Gemini version is prescribed.
- GNOME remains first; KDE follows verification of the core GNOME path. Gate 2W
  remains OPEN and its exhausted GNOME capture row remains HOLD. Existing local
  and hardware attempt counts are preserved. Pending W4 and diagnostics export
  patches are not accepted or changed by this revision.
- Validation: focused static governance tests and policy diff checks; no build,
  playback, hardware execution, commit, push or pull request. Detailed test
  result is recorded with the completed task rather than implying runtime proof.

### 2026-09-26 Target GNOME Wayland trace capture Attempt 3 and Gate 2W narrow row acceptance

- **Environment and Provenance**:
  - Source checkout base: Git commit `9010e5a4a473aa90ae35ef5bb22b27dacf373d6e`.
  - Installed system Flatpak candidate v4:
    - App commit: `8ed86c0b54694b75d1aaa9be75956d1d3a9d075901d90695e4e0d3d144970813`.
    - Runtime commit: `b1935f7a673108616d4fd84564f15cefc9f637481472a8d4d2f0945375d41f4b` (`org.gnome.Platform/x86_64/50`).
    - Bundle package: `validation-reports/candidate-v4/io.github.rusmikev.CineHDR-candidate-v4.flatpak`
      (SHA-256 `54f71856ea224f85834dfb15d717d76ff4136288c7493d0d4cab0e12fcf439f2`).
  - Target discrete GPU: `AMD Radeon (radeonsi, gfx1200, ACO, DRM 3.64)`.
  - Mesa / GL version: `OpenGL ES 3.2 Mesa 26.2.2 (git-3281a69a8b)`.
- **Target Hardware Execution (Attempt 3, Exceptional User-Authorized Brief)**:
  - History of 3 attempts preserved:
    - Run 1 (2026-09-24, `wayland-trace-20260924-133500`): initial passive trace under GNOME.
    - Run 2 (2026-09-24, `wayland-trace-MgsoJA`): single-shot trace capture after parser fix; offline parser PASS, but active API and session status were Unknown (no contemporaneous Copy Report), placing combined row on HOLD ("нет свидетельства из той же сессии"). Two-run budget exhausted.
    - Run 3 (2026-09-26, `wayland-trace-Z4GghM`): single-shot execution under approved ADR-0006 brief using corrected script `validation-reports/capture_wayland_trace_attempt3_corrected.sh` (SHA-256 `1139ef43bf88aa5dffad0e522e01fc4f9e2048b899fad2c465fbbb556adac3e6`).
  - Monitored Flatpak instance ID `2470237098` across session window `2026-09-26T07:47:04.757041Z` to `2026-09-26T07:47:16.897379Z`.
  - Script exit code: `0`; process exit code: `130` (graceful SIGINT termination after facts acquisition).
  - Stop reason: `FACTS_VERIFIED`.
  - Autonomous diagnostics export: `first-media-frame-confirmed` triggered at `2026-09-26T07:47:15.715399+00:00`
    (`video_output_diagnostics.json`, SHA-256 `164e8bca3f1004f17fdbb2079338c37d57669abea7d70427c4d0f9e14af6f9b7`):
    - Active API: `opengl-next`, session status: `active`.
    - Render target format: `GL_RGBA16F`, depth: 16.
    - GDK Color State: `Rec.2100 PQ (published texture)`.
    - Active decoder: `vaapi-copy`.
    - Evidence status: `CONFIRMED`, gaps: `[]`.
    - Verified report text hash: `32c46f79b490d76f00b8011d41b8ea6976e2e6a66b5740ba6ef2c98758472ffd`.
  - Offline Wayland trace parser evaluated `wayland-client.raw.log` (SHA-256 `2e9d5de1a514986d1cbcb88d0b6d6312725e44e065e7fae7b77f40c3b77b107c`)
    and reported status `PASS` (`wayland-trace-validation.json`, SHA-256 `fa5d05e219929be2637131a8a7b076f358ce9f996454bce35eebd1af66cef821`):
    - Image description `51` ready event at index 643.
    - `wl_surface#61` color management surface `64` received `set_image_description` at index 953 followed by `commit` at index 968.
    - Toplevel application ownership verified: `io.github.rusmikev.CineHDR`.
    - Surface buffer attaches: 5 total (4 post-description).
- **Process Absence and Cleanup Observation**:
  - Raw `cleanup_status.txt` (SHA-256 `d7a91e51b28326614450b962eefeeb5c108ebc4ceea94e02dae9286b5e96c0c6`) recorded `FAILED_STILL_RUNNING` due to polling within 0.2s of `flatpak kill` while container was tearing down.
  - Independent subsequent check confirmed instance `2470237098` was completely absent in `flatpak ps` at `2026-09-26T07:50:18.675659+00:00`.
  - The exact cause of the cleanup delay remains unproven without dedicated profiling.
- **Evidence Adjudication and Gate Status**:
  - The narrow row requiring simultaneous proof of active `opengl-next` renderer and Wayland color-management protocol submission in the same session is accepted as `PASS` (`session_facts.json`, SHA-256 `ca73ebec0e1268bc3a64a719cbd9a818f4c8c276616a05efce77c4f75ecea725`).
  - Gate 2W remains `OPEN`.
  - Non-claims explicitly maintained per ADR-0006 (boundaries of client protocol evidence, not added blockers):
    - Physical display output (photometry / photon measurement) is not certified by this client trace.
    - Compositor transformation / direct scanout / zero-copy presentation is not proven.
    - Per-frame client-to-compositor presentation linkage is not proven.
    - Pixel / color accuracy under Wayland compositor remains a separate verification track.
  - Remaining open requirements under the accepted matrix:
    - GNOME: the accepted `opengl-next` trace covers one backend. ADR-0004 still
      needs a separate legacy `opengl` process with passing GDK publication report
      and same-session manager `perceptual`/`done`, ready image description,
      perceptual `set_image_description`, then CineHDR surface commit. The surface
      encoding may be compositor-preferred across source texture states.
    - KDE/KWin remains `UNAVAILABLE` pending upstream support; its row has no
      accepted compositor submission evidence.
    - Resize, fullscreen, teardown, and forced SDR/PQ surface alternation are
      not conditions of this Gate 2W row. Gate 1/2 lifecycle and publication rows
      retain their separate status. Absence of local reports does not establish
      a zero attempt count.
- **Retained Local Artifacts (Private)**:
  - Local directory: `validation-reports/wayland-trace-Z4GghM/`
  - `session_facts.json`: SHA-256 `ca73ebec0e1268bc3a64a719cbd9a818f4c8c276616a05efce77c4f75ecea725`
  - `wayland-trace-validation.json`: SHA-256 `fa5d05e219929be2637131a8a7b076f358ce9f996454bce35eebd1af66cef821`
  - `video_output_diagnostics.json`: SHA-256 `164e8bca3f1004f17fdbb2079338c37d57669abea7d70427c4d0f9e14af6f9b7`
  - `video_output_diagnostics.txt`: SHA-256 `32c46f79b490d76f00b8011d41b8ea6976e2e6a66b5740ba6ef2c98758472ffd`
  - `wayland-client.raw.log`: SHA-256 `2e9d5de1a514986d1cbcb88d0b6d6312725e44e065e7fae7b77f40c3b77b107c`
  - `instance.txt`: SHA-256 `ee9044c5c0084dbdb9c6b613acce2ad6e3b88aefbf5afda81bc73756dbd3f5dd`
  - `instance_info`: SHA-256 `e84509710af804b367da4bd8f1e8f62b5f0f076fcfce35d98ac3ae4ccc46b349`
  - `exit_code.txt`: SHA-256 `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa`
  - `process_exit_code.txt`: SHA-256 `f5bde7eb9f6c71611dc5726e8aca3eb4eba3e386da49e0a4ed5c295a90a73a0d`
  - `stop_reason.txt`: SHA-256 `33c555218a04b0414dffa51441fd217889f235272d0280a8b36c1a4b1b293d4d`
  - `cleanup_status.txt`: SHA-256 `d7a91e51b28326614450b962eefeeb5c108ebc4ceea94e02dae9286b5e96c0c6`
  - `SHA256SUMS`: SHA-256 `f2439c5823dcd6e20c9b655a7212457ecd61f9d2db56ef51c9fc56db694be111`
  - Raw logs and private media paths remain strictly uncommitted.

### 2026-09-26 W4 software evidence and hardware hand-off boundary

- Engineer: `gpt-6-sol`, medium, explicitly assigned bounded documentation and
  harness-path correction; no GPU/Wayland run. Retained W4 software report passed
  on llvmpipe with `hwdec-current=no`; its hardware cell was `NOT_RUN` in that
  software-only report.
- The screenshot validator tolerates one decoded 8-bit value outside the subtitle
  ROI; the retained report observed zero difference there. Its 50-pixel subtitle
  count and two-code-value PNG patch limits are structural heuristics, not
  ADR-0005 FBO precision bounds. PNG `cHRM` identifies primaries, not PQ/HLG
  transfer. The W4 script retains repo-relative defaults and explicit environment
  or command-line path overrides without a user-specific fallback.
- The next hardware row required an explicit raw active-decoder check because
  the runner does not reject `hwdec=auto` falling back to `decode.active=no`.
  This was a preflight limitation, not a change to the accepted W4 thresholds.

### 2026-09-26 W4 standalone hardware screenshot row

- Roles: `gpt-6-luna` low Operator executed the immutable, user-authorized
  single-run brief; `gpt-6-sol` medium Engineer prepared this bounded document
  update; the parent independently reviewed the raw evidence and accepted only
  this row. The earlier W4 hardware-attempt total remains Unknown; this is one
  recorded authorized execution, not a reset of ADR-0006 history.
- Target AMD GPU/host stack, 09:56:40–09:56:41 UTC, exit 0. Exact HEVC Main10 HDR
  fixture provenance and loaded dependencies passed independent checks. Raw
  `decode.requested=auto`, `decode.active=vaapi-copy`, actual `opengl-next`,
  target GL renderer, BT.2020/PQ input and target, `GL_RGBA16F`/16, and the
  320×180 16-bit RGB screenshot passed the existing PNG/patch oracle. CUDA
  loader errors during auto probing remain in stderr; mpv subsequently reported
  active VAAPI-copy decoding. Source evidence is retained in
  `validation-reports/gate2-w4-hardware-20260926-095640/` with SHA256SUMS.
- Verdict: narrow `PASS` for the standalone W4 `hdr_hwdec_auto` screenshot row.
  The current runner's aggregate PASS does not enforce active hardware decoding;
  the independent raw check did. The physical decode GPU remains Unknown. This
  is not CineHDR GTK playback or Candidate v4 execution, display output,
  color accuracy, direct scanout, or aggregate Gate 2/2W acceptance. The
  software-only W4 report's `NOT_RUN`
  remains its historical result.

### 2026-09-27 CineHDR UI screenshot save observation

User-operated `s` in one installed-v4 CineHDR session; no agent changed the app
or settings. A new 3840×2160 8-bit RGB JPEG appeared in the previously empty
screenshot directory at 02:22:44.816449Z (SHA-256
`c5cceb24186f75bb853f2d892daf21c358b47c40bdea452803159edfc2f38954`).
Pillow verify/load and visual inspection confirmed a nonempty video frame:
UI save workflow `PASS`. A fresh 02:28:57.795574Z Copy Report showed configured
and requested GPU Next, active `opengl-next`, session `active`, `vaapi-copy`,
target AMD GPU, `GL_RGBA16F`/16, and Rec.2100 PQ GDK color state. The same
CineHDR instance and app/runtime commits were confirmed at 02:30:23Z, linking
the report to the 02:22:30Z screenshot session, without per-frame linkage. The
02:16:56Z report remains historical evidence from a prior process. `gpt-6-sol`
medium prepared this documentation; the parent independently reviewed the UI
and process evidence. No HDR/color accuracy, subtitle, physical HDR output, or
wider Gate 2/2W claim follows. Retained evidence:
`validation-reports/ui-screenshot-20260927-022244/` with SHA256SUMS.

### 2026-09-27 W4 active-decoder oracle correction and retained GNOME evidence

User requested continued Codex work with Gemini temporarily excluded.
`gpt-6-sol` medium prepared the bounded runner/test patch; the parent independently
reviewed it and required one correction to avoid a VAAPI-only restriction.
A caller-level regression on the original runner reproduced six false PASS
cases; the corrected W4 class passed 12 tests and the integrated pixel-pipeline
suite passed all 32. The hardware cell now checks requested `auto` separately
from a concrete actual hardware backend, with both retained in evidence.
Offline replay accepted the existing real `vaapi-copy` JSON; an explicitly
synthetic `decode.active=no` mutation was rejected. All accepted hardware
reports remain unchanged; no new hardware run occurred.

The parent also reconciled the retained GNOME legacy trace with its user Copy
Report: the protocol chain passes and the `opengl` sample lies inside the
capture interval, but no full same-process publication matrix exists in that
capture. Gate 2W remains OPEN; its next useful check combines that matrix with
protocol capture under the existing attempt limits. Artifacts are retained in
`validation-reports/w4-hwdec-oracle-20260927/` and
`validation-reports/wayland-legacy-offline-reconciliation-20260927.json`.

### 2026-10-03 Gate 2W Run 4 offline qualification and provenance analysis

Engineer: Gemini. Offline qualification for Gate 2W Run 4 resolved two blockers without GPU or Wayland execution:
1. Reconciled SDR fixture provenance: confirmed FFmpeg 8.1.3 generator on host with Lavf62.12.102 muxer tag restored to match the accepted fixture `41393a0ee9dd887a1f11d4b21946fef3d1f49e72127719eefd1ac40aa9d5e360` byte-for-byte; updated JSON provenance and preserved unmodified FFmpeg 8.1.3 artifact `cinehdr-gate2-hevc-main10-yuv420p10-lossless-sdr-ffmpeg813-2ec07e.mp4`. Preflight check confirms both fixtures `VERIFIED`.
2. Reconciled Flatpak Candidate v4 runtime loading: inside container, libmpv is loaded from `/app/lib/libmpv.so.2.5.0` (matching built candidate SHA-256 `8c40fbc4...`), whereas `src/gpu_validation.py` currently requires `/mpv-gpu-next-prefix/lib64/`. Added negative regression test reproducing the rejection of `/app/lib/` and verifying that foreign library substitution (`/usr/lib`, `/tmp`, version drift) is strictly rejected.
3. Formulated proposal for exact canonical path checking (`libmpv_path in {"/app/lib/libmpv.so.2", "/app/lib/libmpv.so.2.5.0"}`) gated by verified container context, eliminating loose suffix matching.
4. Assessed under `docs/MODEL_POLICY.md`: altering library provenance criteria in `src/gpu_validation.py` affects render evidence criteria and packaging boundaries, requiring an ADR amendment proposal and independent review before product modifications. Stopped before modifying `src/gpu_validation.py`. Noted that modifying product code will change git commit SHA, requiring Candidate v5 rebuild rather than reusing Candidate v4. Candidate v4 is not claimed to have passed the matrix.
5. Added the ADR-0006 Gate 2W Run 4 rerun brief to `docs/GPU_NEXT_DEVELOPMENT.md` targeting GNOME Shell 50.5 on Wayland with `org.gnome.Platform//50`, incorporating Attempt 1's contemporaneous Copy Report and Candidate v5 prerequisites.

### 2026-10-04 Gate 2W Run 4 offline remediation of Astra high review blockers

- **Role**: Engineer (Gemini-first under MODEL_POLICY).
- **Scope**: Offline resolution of 4 blockers reproduced during the independent Astra high review of Gate 2W Run 4:
  1. *Wayland ID space mixing across connections*:
     - Object lineage enforced in `tests/parse_wayland_color_trace.py`: verified that `wl_surface` and its `xdg_wm_base` belong to the same connection/registry as `wp_color_manager_v1`, and that `wp_image_description_v1` creations stem strictly from `manager_id`, its creators, or `color_surface_id`. Reject descriptions created by alien managers on other connections.
     - Ambiguous `delete_id` rejection: when client-allocated descriptor ID reuse is evaluated, verified that `wl_display.delete_id` belongs to the connection's display, and rejected reuse if any other object with the same ID was destroyed within the reuse window.
  2. *`unset_image_description` between set and commit*:
     - In `validate_composite_window_trace` (as well as `validate_multiphase_trace` and `validate_trace`), invalidated and rejected submission if `wp_color_management_surface_v1.unset_image_description` occurred between `set_image_description` and `wl_surface.commit`.
  3. *Overwriting final `cleanup_status.json` by EXIT trap handler*:
     - In `run_wayland_publication_validation.sh`: `cleanup_handler` now protects pre-existing `cleanup_status.json` (such as `trigger_signal: NORMAL`), preventing the EXIT trap handler from clobbering clean completion telemetry.
  4. *Recognizing instance as terminated when info is missing without checking `flatpak ps`*:
     - In `cleanup_instance`, if `$XDG_RUNTIME_DIR/.flatpak/<inst>/info` is absent, strictly query `flatpak ps --columns=instance`. Fail-closed (return 1) if the instance remains listed or if `flatpak ps` query fails. Return 0 only when absence is verified via `flatpak ps`.
- **Validation**:
  - Maintained accepted narrow result of Attempt 3 (`wayland-trace-Z4GghM`, SHA-256 `2e9d5de1...`, `PASS`).
  - Claim boundaries preserved: `container_session_correlated=true`, `same_process_wire_correlated=false`, per-frame presentation and compositor scanout rows remain `OPEN`.
  - Added 4 negative regression tests and positive counterpart tests in `tests/test_wayland_color_trace.py` (54 tests passed) and `tests/test_wayland_publication_scenario.py` (51 tests passed).
  - All static and governance tests passed (`test_validation_governance.py`, `test_attempt3_regressions.py`).
  - `git diff --check` passed cleanly.
  - No Flatpak build or install, no real GPU/Wayland Run 4 execution, no push or PR.

### 2026-10-17 First-frame republication simplification and multi-backend validation

- **Role**: Gemini (Engineer under Gemini-first profile; architecture accepted by independent Astra high).
- **Scope & Implementation**:
  - Integrated the ADR-0007 simplification: removed `_start_nonblocking_readback`,
    property-reply handlers and timeout from `src/hdr_controller.py`.
    `apply_hdr_settings()` sets target options before committing output state;
    the pinned native renderers refresh option caches at render entry.
  - Preserved `_pending_epoch` and the widget's committed-epoch checks and
    `_committed_render_epoch`, plus first published sample retention in
    `src/gpu_validation.py`. Configuration guards do not certify pixel contents.
  - Corrected first-frame unavailable evidence semantics: missing GDK texture getters, unpopulated
    epoch, or unknown media path are retained in the first published sample and evaluated as `UNVERIFIED`
    without false `provenance_errors` across recovery; confirmed format/tag mismatches strictly yield `FAIL`.
  - Added targeted negative regression test `test_first_published_unavailable_texture_evidence_survives_recovery_as_unverified`
    (baseline failure captured in `first-frame-unavailable-evidence-failure.log`).
- **Validation**:
  - Full relevant test suites (tests.test_hdr, tests.test_gpu_validation, tests.test_diagnostics_export)
    passed: 287 tests run, 0 failures; `git diff --check` passed cleanly.
  - Frozen candidate snapshot preflight (`--probe-only`) passed in host and container.
  - Bounded 60s GNOME Wayland publication supervisor runs on identical candidate:
    - `gpu-next` (`dev-publication-gpu-next-20261017T095237Z`): Active API `opengl-next`, publication matrix
      and FBO pixel oracle PASS, 3 captures, 0 LSB repeat-SDR delta, exit 0, cleanup ABSENT.
    - `legacy` (`dev-publication-legacy-20261017T100358Z`): Active API `opengl`, publication matrix and FBO
      pixel oracle PASS, 3 captures, 0 LSB repeat-SDR delta, exit 0, cleanup ABSENT.
    - User instance `3895243466` preserved intact in both runs.
  - Checkpoint updated (`first-frame-checkpoint.json`, status `COMPLETE_VERIFIED_LOCAL_OVERLAY`) and all
    13 SHA-256 sums verified.
- **Claim Boundaries**: Local client texture publication and FBO pixel fidelity are verified on both
  backends. Physical display HDR output and packaged application build are not certified by this check.
- **Packaged candidate inspection (Codex)**:
  - Build source commit: `feb0d405297f031d4a49db6f7ddf5cd5e7c612d1`;
    exported app commit: `2e97e6a5e694c1de54cfad246240d93222aea5a16f9ee1910cc2074cc0d9a10e`.
  - Bundle SHA-256 `b3505cd372a34a7625077d3ffdc672dc7939710d6a06408bbe006df0f9aee506`;
    build-log SHA-256 `239eddcc68d4f39bfc6acef4f2ca06c4db9d9de53135de85fa66a0797c8175ff`.
  - All 39 source files and 25 installed Python/shader files match the verified
    development snapshot; the dependency manifest is unchanged. Build log
    records 13/13 passing suites.
  - Correction to the Engineer summary: cached downloads did not mean all
    dependency binaries were reused. The log records compilation of FFmpeg,
    libplacebo and libmpv. Packaged libmpv and libplacebo hashes differ from the
    previously installed stack; libavcodec matches. This alone does not prove
    a functional regression, but overlay results do not verify these binaries.
  - Corrected documentation-only names and setter/render timing description;
    production code and existing bundle are unchanged. Packaged runtime
    validation remains pending; no remote action performed by this inspection.
