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
* Replaced standard `GtkGLArea` with a custom high-precision float rendering pipeline (`GL_RGBA16F`).
* Integrated Wayland HDR color state signaling (`rec2100-pq` / `srgb` textures) via GTK4 ColorState APIs to pass HDR signal to compatible compositors.
* Added a dedicated **HDR Settings** control icon on the playback panel (visible when playing HDR content) for real-time SDR/HDR switching and peak brightness adjustment.
* Provided system integration launcher (**CineHDR**).

**Known limitations / Известные ограничения:**
* CineHDR requires GTK's modern renderer (`ngl`/`vulkan`). Upstream Cine pins `GSK_RENDERER=gl` to work around frame drops on the Niri DE and video blackouts on some NVIDIA setups; that workaround is incompatible with HDR. On Niri the pin is now applied automatically (`NIRI_SOCKET` detected, nothing lost — Niri has no color management anyway). Elsewhere, launching with `GSK_RENDERER=gl` still works — CineHDR detects it and falls back to SDR rendering.
* CineHDR now asks the compositor directly whether it speaks a color-management protocol (`wp_color_manager_v1`) and refuses HDR pass-through when it does not — on such systems (older wlroots, Weston, Niri) you automatically get mpv's proper tone mapping instead of a washed-out picture. One gap remains: a capable compositor with monitor HDR switched *off* still converts PQ to SDR itself, which is simpler than mpv's tone mapping — if HDR content looks washed out or clipped there, select **Force SDR** in the HDR menu. Check **HDR Diagnostics** in the same menu for the live pipeline state, including the new "Compositor Color Management" row.

---

### Description

CineHDR combines a clean interface with a high-performance engine to deliver a seamless viewing experience with HDR support.

### Features

- **HDR Support** — Pass HDR10/HLG color state signal to compatible Wayland compositors
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
