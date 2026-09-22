<img style="vertical-align: middle;" src="data/icons/hicolor/scalable/apps/io.github.rusmikev.CineHDR.svg" width="112" height="112" align="left">

### CineHDR

Play your videos with HDR support

<br>

[![Download Flatpak](https://img.shields.io/badge/Download-Flatpak%20(Actions)-283C54?style=for-the-badge&logo=flatpak&logoColor=white)](https://github.com/rusmikev/CineHDR/actions)
[![CI](https://github.com/rusmikev/CineHDR/actions/workflows/build-flatpak.yml/badge.svg)](https://github.com/rusmikev/CineHDR/actions/workflows/build-flatpak.yml)

---

### 📢 CineHDR: AI-Enhanced HDR Fork

> [!IMPORTANT]
> **Disclaimer:** This repository is an independent fork of the original [Cine](https://github.com/diegopvlk/Cine) video player. 
> The HDR implementation and UI modifications were co-developed with **Google Gemini (Advanced Agentic Coding AI)**. 
> This software is provided **"as is"**, without warranty of any kind, express or implied. Use at your own risk.
>
> **Дисклеймер:** Этот репозиторий является независимым форком оригинального видеоплеера [Cine](https://github.com/diegopvlk/Cine).
> Поддержка HDR и изменения в интерфейсе разработаны совместно с **Google Gemini (Advanced Agentic Coding AI)**.
> Программное обеспечение предоставляется **"как есть" (as is)**, без каких-либо явных или подразумеваемых гарантий.

**Changes in this fork / Изменения в этом форке:**
* Replaced standard `GtkGLArea` with a high-precision float rendering pipeline (`GL_RGBA16F` / `Rec.2100 PQ`).
* Integrated GTK4 `GdkColorState` tagging (`rec2100-pq` / `srgb` textures) and `GtkGraphicsOffload` pipeline.
* Built-in dynamic Wayland protocol probing (`wp_color_manager_v1` / `xx_color_manager_v4`) to automatically verify compositor and monitor HDR state before enabling PQ signaling.
* Added a dedicated **HDR Settings** control icon on the playback panel (visible when playing HDR content) for real-time SDR/HDR switching and peak brightness adjustment.
* Added experimental support for `libmpv`'s `gpu-next` rendering backend (`libplacebo` / Dolby Vision RPU pipeline).
* Provided system integration launcher (**CineHDR**).

**Known limitations / Известные ограничения:**
* **Wayland Color Management Pipeline**: CineHDR prepares a true 16-bit `Rec.2100 PQ` surface and communicates target color states to GTK4. End-to-end native HDR output to the screen depends on the Wayland color-management protocol implementation in GTK and the compositor:
  * **GTK 4 Color Management Requirement**: In GTK 4 (4.18–4.24.0), Wayland color management is experimental and disabled by default. It requires the environment variable `GDK_DEBUG=color-mgmt`, `CINEHDR_EXPERIMENTAL_COLOR_MGMT=1` (for Flatpak: `flatpak override --user --env=CINEHDR_EXPERIMENTAL_COLOR_MGMT=1 io.github.rusmikev.CineHDR`), or the CLI flag `--experimental-color-mgmt`. Without this opt-in, GTK disables `wp_color_manager_v1`, causing surfaces to be mapped to sRGB. When disabled, CineHDR automatically falls back to high-quality SDR tone mapping via `mpv`.
  * **KDE Plasma (KWin)**: KWin advertises HDR capabilities (PQ, compound power 2.4) but intentionally does not advertise the `sRGB` transfer function (TF 9). GTK 4 strictly requires `sRGB` to establish its baseline color space. CineHDR models GTK's policy (`gtk_cm_policy`) and automatically detects this condition, falling back cleanly to `mpv`'s tone mapping to avoid washed-out colors.
  * **GNOME (Mutter)**: Mutter supports client color management for `wp_color_manager_v1` including `sRGB`. Native HDR output requires both enabling experimental HDR in Mutter (`gsettings set org.gnome.mutter experimental-features "['experimental-hdr']"`, toggling HDR in GNOME Display Settings) and running with `GDK_DEBUG=color-mgmt`.
* **Renderer Selection**: CineHDR uses GTK's modern renderers (`ngl`/`vulkan`). On the Niri compositor (`NIRI_SOCKET` detected), `GSK_RENDERER=gl` is pinned automatically to preserve upstream frame-drop workarounds; CineHDR detects this legacy renderer and falls back to safe SDR tone mapping.
* Check **HDR Diagnostics** in the playback menu for the live pipeline state, active renderer backend, compositor capabilities, and specific fallback reasons.

---

### Description

CineHDR combines a clean interface with a high-performance engine to deliver a seamless viewing experience with HDR support.

### Features

- **HDR & Color Management** — HDR content is rendered into a high-precision Rec.2100 PQ target with dynamic monitor-aware tone mapping and tri-state capability validation.
- **Renderer Selection** — Support for both stable legacy OpenGL and experimental `gpu-next` (`libplacebo`) backends with automatic safe creation fallback.
- **Simple Design** — A refined, distraction-free interface
- **MPV-Based** — Leverages the robust power of MPV for great playback and format support
- **Audio and Subtitles** — Control track selection and synchronization for both
- **Video Controls** — Easily adjust brightness, contrast, zoom, aspect ratio, etc.

### Screenshot

<p align="center"><img src="screenshots/video.png" alt="Video Playing"/></p>

<div>
  <details>
    <summary>More Screenshots (Expand):</summary><br>
      <p align="center"><img height="943" src="screenshots/preferences.png" alt="Preferences"/></p>
      <p align="center"><img src="screenshots/options.png" alt="Video Options"/></p>
      <p align="center"><img src="screenshots/window.png" alt="Main Window"/></p>
  </details>
</div>

### Donate (Upstream Project)

If you want to support the original creator of Cine (Diego Povliuk), you can use:

- [PayPal](https://www.paypal.com/donate?hosted_button_id=DVL7H35GA66X6)
- [Ko-fi](https://ko-fi.com/diegopvlk)
- Pix: diego.pvlk@gmail.com

### Translations (Upstream Project)

You can help translate upstream Cine using [Weblate](https://hosted.weblate.org/projects/cine/app/).
Strings specific to this fork (the HDR menu and diagnostics) are not on Weblate — translation contributions for them are welcome as pull requests against `po/`.

[![Translation status](https://hosted.weblate.org/widget/cine/app/multi-auto.svg)](https://hosted.weblate.org/engage/cine/)


### Code of Conduct

This project follows the [GNOME Code of Conduct](https://conduct.gnome.org).

### Installation / Установка

For general users, the easiest way to install **CineHDR** is using Flatpak:

#### Option A: Pre-built Flatpak (Fastest) / Готовая сборка Flatpak
1. Go to the [Actions](https://github.com/rusmikev/CineHDR/actions) tab of this repository.
2. Click on the latest workflow run (e.g. "Implement HDR playback support...").
3. Scroll down to the **Artifacts** section at the bottom and download `CineHDR-Flatpak-x86_64` (for standard PCs) or `CineHDR-Flatpak-aarch64` (for ARM devices).
4. Unzip the downloaded file to obtain `CineHDR.flatpak`.
5. Install it by running the following command in your terminal:
   ```bash
   flatpak install --user CineHDR.flatpak
   ```

#### Option B: Build Flatpak from source / Сборка Flatpak из исходников
If you want to compile the Flatpak bundle yourself:
1. Ensure `flatpak` and `flatpak-builder` are installed on your system.
2. Add the Flathub repository (required for runtime dependencies):
   ```bash
   flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
   ```
3. Build and install the application:
   ```bash
   flatpak-builder --user --install --force-clean build-dir build-aux/flatpak/io.github.rusmikev.CineHDR.json
   ```

#### Option C: Native compilation / Локальная сборка
1. Install development dependencies (`meson`, `ninja`, `python3-mpv`, and dependencies for GTK4/Adwaita).
2. Clone the repo, open it in GNOME Builder and press run, or compile it manually using:
   ```bash
   meson setup build
   meson compile -C build
   ```
