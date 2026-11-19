<img style="vertical-align: middle;" src="data/icons/hicolor/scalable/apps/io.github.rusmikev.CineHDR.svg" width="112" height="112" align="left">

### CineHDR

A GTK4 / libadwaita video player with native HDR output on Wayland.

<br>

[![Download Flatpak](https://img.shields.io/badge/Download-Flatpak%20(Releases)-283C54?style=for-the-badge&logo=flatpak&logoColor=white)](https://github.com/rusmikev/CineHDR/releases)
[![CI](https://github.com/rusmikev/CineHDR/actions/workflows/build-flatpak.yml/badge.svg)](https://github.com/rusmikev/CineHDR/actions/workflows/build-flatpak.yml)

<p align="center"><img src="screenshots/video.png" alt="CineHDR playing a video"/></p>

## What it is

CineHDR is a fork of [Cine](https://github.com/diegopvlk/Cine) by Diego Povliuk, a
clean mpv-based player for the GNOME desktop. Upstream Cine has no HDR output
path. This fork adds one, so that HDR10 and HLG content is delivered to an HDR-enabled display through the Wayland
color-management protocol.

**Status:** HDR10 and HLG output is verified on **GNOME 50/51 with AMD graphics**
(see [Platform status](#platform-status)). Other desktops and GPUs are not yet
verified. On unsupported setups the player falls back to normal SDR playback
with tone mapping, exactly like upstream.

## What this fork adds

- **HDR rendering pipeline.** mpv renders into a floating-point `RGBA16F`
  target tagged as Rec.2100 PQ; the frame reaches GTK as a `GdkGLTexture` with a
  matching `GdkColorState` (via `GdkGLTextureBuilder`), and GTK passes the
  color description to the compositor (`wp_color_manager_v1`).
- **Runtime HDR detection.** A Wayland protocol probe reads what the compositor
  and the monitor actually advertise, so HDR signaling is only enabled when the
  output can take it. Unknown capabilities stay "Unknown", never guessed.
- **HDR menu and diagnostics.** A playback-panel control for SDR/HDR switching
  and peak brightness, plus an **HDR Diagnostics** dialog (and *Copy Report*)
  showing the live pipeline: renderer backend, texture color state, compositor
  capabilities, active `target-*` mpv settings, and the reason for any fallback.
- **Validation tooling.** Automated checks that capture the Wayland protocol
  trace and sample the rendered framebuffer, so that "HDR works" is backed by
  recorded evidence instead of a claim. See [docs/TESTING.md](docs/TESTING.md).
- **Experimental `gpu-next` backend** (libplacebo, Dolby Vision RPU path),
  off by default and outside the 1.0 scope.

Everything else (interface, playlists, audio/subtitle controls, video
adjustments, MPRIS, translations) comes from upstream Cine.

## Platform status

Each result is recorded against the evidence levels defined in
[docs/TESTING.md](docs/TESTING.md): **L2** playback works; **L3p** is a Wayland
protocol trace (HDR description and commit for native HDR, or their absence
and an SDR buffer commit for KDE fallback); **L4** is a visual comparison
against mpv on a test pattern.

| Environment | Stack | SDR playback (L2) | HDR-to-SDR fallback (L3/L3p) | mpv reference (L3p) | HDR10 protocol (L3p) | HDR10 visual (L4) |
|---|---|---|---|---|---|---|
| **GNOME / AMD** | GNOME 50, Mutter, AMD (Mesa) | **PASS** (includes a 4K60 smoke check on Flatpak runtime 51) | N/A | **PASS** | **PASS** (also rechecked on Flatpak runtime 51) | **PASS** |
| GNOME / Intel | GNOME ≥ 48, Intel (Mesa) | **PASS** (1080p SDR playback and clean exit; mpv reported frame drops in fullscreen 4K60 on the tested HDMI display, with no visible issues reported) | N/A | **PASS** | **PASS** | **PASS** |
| GNOME / NVIDIA | GNOME ≥ 48, NVIDIA (PRIME) | **PASS** (1080p with CUDA interop; default interop reports a startup GL error; mpv reported frame drops in fullscreen 4K60 with CUDA on the tested HDMI display, with no visible issues reported) | N/A | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` |
| KDE / AMD | Plasma ≥ 6.7, KWin, AMD (Mesa) | **PASS** (playback, pause, seek, resize/fullscreen, exit during playback) | **PASS** (GTK lacks TF sRGB → SDR tone mapping, no HDR description) | N/A | N/A | N/A |
| KDE / NVIDIA | Plasma ≥ 6.7, KWin, NVIDIA (PRIME) | `NOT_RUN` | `NOT_RUN` | N/A | N/A | N/A |

`NOT_RUN` means not tested, not failing. Dates, builds and per-scenario detail
are in [docs/TESTING.md](docs/TESTING.md). A Dolby Vision Profile 8 clip plays as
its HDR10-compatible base layer (no RPU processing). These results do not
establish photometric accuracy, frame-by-frame presentation, or behavior on
other hardware.

### Known limitations

- **GNOME ≥ 48 is required for native HDR.** Enable HDR per monitor in
  *Settings → Displays*.
- **KDE Plasma:** native HDR is deferred past 1.0. KWin no longer advertises the
  `sRGB` transfer function that GTK 4.22+ requires, so GTK disables color
  management; CineHDR then falls back to SDR tone mapping. The planned fix is an
  upstream GTK change.
- **Publishing an HDR texture is not proof of HDR on screen.** That needs the
  protocol trace (L3p) and a visual check (L4), which is why the table above
  separates them.

## Installation

### Pre-built Flatpak

1. Open the [Releases](https://github.com/rusmikev/CineHDR/releases) page.
2. Download `CineHDR-x86_64.flatpak` from the latest release.
3. Install it:
   ```bash
   flatpak install --user CineHDR-x86_64.flatpak
   flatpak run io.github.rusmikev.CineHDR
   ```

### Build the Flatpak from source

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
git clone --recurse-submodules https://github.com/rusmikev/CineHDR.git
cd CineHDR
flatpak-builder --user --install --force-clean build-dir \
  build-aux/flatpak/io.github.rusmikev.CineHDR.json
flatpak run io.github.rusmikev.CineHDR
```

### Native build (development)

Requires `meson`, `ninja`, `python3-mpv`, GTK4, libadwaita and blueprint-compiler.

```bash
meson setup build
meson compile -C build
meson test -C build --print-errorlogs
python3 run_dev.py /path/to/video.mkv
```

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | HDR pipeline: mpv → FBO → GdkGLTexture → GSK → Wayland, and the invariants of each stage |
| [docs/TESTING.md](docs/TESTING.md) | Test matrix, evidence levels (L1–L4), evidence bundle format |
| [docs/PLAN.md](docs/PLAN.md) | Release plan, decisions D1–D13, task status |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [tools/validation/README.md](tools/validation/README.md) | Validation tools: what each one proves and how to run it |
| [AGENTS.md](AGENTS.md) | Engineering rules for AI coding agents working on this repository |
| [CHANGELOG.md](CHANGELOG.md) | Changes by release |

## How this project is built

CineHDR is developed by its owner with AI coding assistants: first Google
Gemini, then OpenAI ChatGPT, and now Anthropic Claude. Most of the HDR
code was written by these assistants under written rules ([AGENTS.md](AGENTS.md))
and an evidence protocol ([docs/TESTING.md](docs/TESTING.md)). The owner sets
the goals and runs every hardware check; an assistant never claims a result it
did not see. The software is provided **as is**, without warranty of any kind.

## Credits and license

- [Cine](https://github.com/diegopvlk/Cine) by Diego Povliuk and contributors is
  the foundation of this project. To support the original author:
  [Ko-fi](https://ko-fi.com/diegopvlk) · [PayPal](https://www.paypal.com/donate?hosted_button_id=DVL7H35GA66X6).
- Built on [mpv](https://mpv.io), GTK4 and libadwaita.
- Translations of upstream strings are managed on
  [Weblate](https://hosted.weblate.org/projects/cine/app/). Strings specific to
  this fork (HDR menu and diagnostics) are not on Weblate; pull requests
  against `po/` are welcome.
- Licensed under the [GNU GPL v3](LICENSE). This project follows the
  [GNOME Code of Conduct](https://conduct.gnome.org).

---

## Configuration reference

<details>
<summary>GTK color management (<code>GDK_DEBUG=color-mgmt</code>)</summary>

In GTK 4.18–4.24, Wayland color management is experimental. Without
`GDK_DEBUG=color-mgmt`, GTK disables `wp_color_manager_v1` and surfaces are
treated as sRGB, so playback falls back to SDR tone mapping in mpv.

- **Flatpak (default):** the launcher enables `GDK_DEBUG=color-mgmt` before GTK
  initializes and preserves any other `GDK_DEBUG` flags. To opt out, set
  `CINEHDR_NO_COLOR_MGMT=1`, for example
  `flatpak override --user --env=CINEHDR_NO_COLOR_MGMT=1 io.github.rusmikev.CineHDR`.
  An explicit `GDK_DEBUG=all:color-mgmt` is also respected as disabling it.
- **Native / `run_dev.py`:** opt-in, so system defaults are not modified. Use
  `CINEHDR_EXPERIMENTAL_COLOR_MGMT=1`, `--experimental-color-mgmt`, or export
  `GDK_DEBUG=color-mgmt`.

</details>

<details>
<summary>Render backend (<code>render-backend</code>) and GTK renderer (<code>GSK_RENDERER</code>)</summary>

CineHDR distinguishes mpv's internal video backend from GTK's scene-graph
renderer.

- **`render-backend`** (Preferences → Video, or the HDR & Video Output menu):
  `legacy` (the `opengl` libmpv API) is the stable default; `gpu-next` is
  experimental. Changing it takes effect on the next launch; live switching is
  not supported. If `gpu-next` context creation fails at startup, CineHDR falls
  back to `legacy` and keeps the saved preference.
- **`CINEHDR_RENDER_BACKEND`** (developer override, takes precedence over
  GSettings): `legacy`; `auto` (automatic fallback to `opengl` on creation
  failure); `gpu-next` (strict, fails visibly).
- **`GSK_RENDERER`:** if unset, CineHDR selects `gl` on GTK ≥ 4.18 (`ngl` on
  older GTK, `gl` under Niri). An explicit user value is always kept.

</details>

<details>
<summary>NVIDIA PRIME on GNOME Wayland (SDR)</summary>

On the tested RTX 3050 laptop, the legacy renderer played 1080p SDR with
`nvdec` when mpv loaded only the CUDA interop. The default interop search
reported a startup OpenGL error on that setup.

For this workaround, add `gpu-hwdec-interop=cuda` to CineHDR's Flatpak
`mpv.conf` under `~/.var/app/io.github.rusmikev.CineHDR/config/cinehdr/`.
This selects the interop used by the renderer. The playback decoder can
still be `nvdec`. Remove the line before using a different GPU or to restore
the default interop selection.

On the tested setup, the following launch selected the NVIDIA renderer.
Use the EGL vendor manifest path that exists inside your Flatpak.

```bash
flatpak run --env=__NV_PRIME_RENDER_OFFLOAD=1 \
  --env=__GLX_VENDOR_LIBRARY_NAME=nvidia \
  --env=__EGL_VENDOR_LIBRARY_FILENAMES=/usr/lib/x86_64-linux-gnu/GL/glvnd/egl_vendor.d/10_nvidia.json \
  --env=CINEHDR_RENDER_BACKEND=legacy io.github.rusmikev.CineHDR
```

Check **HDR Diagnostics** for the actual OpenGL renderer and active decoder.
See [the test matrix](docs/TESTING.md) for the validated scope.

</details>

<details>
<summary>Reading the HDR Diagnostics report</summary>

Open **HDR Diagnostics** from the playback menu for the live pipeline state,
active renderer backend, compositor capabilities, and specific fallback
reasons. *Copy Report* produces the same text for bug reports; it contains no
personal paths or media names.

</details>
