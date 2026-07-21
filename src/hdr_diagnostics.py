# hdr_diagnostics.py
#
# Copyright 2026 Diego Povliuk / rusmikev
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gi
from gettext import gettext as _

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk

from .hdr_detection import (
    check_hdr_support,
    get_compositor_cm_support,
    get_hdr_unsupported_reason,
)


def get_mpv_prop(mpv, name, default=None):
    if mpv is None:
        return default
    try:
        if hasattr(mpv, "_get_property"):
            res = mpv._get_property(name)
            if res is not None:
                return res
    except Exception:
        pass
    try:
        attr_name = name.replace("-", "_")
        if hasattr(mpv, attr_name):
            res = getattr(mpv, attr_name)
            if res is not None:
                return res
    except Exception:
        pass
    try:
        if hasattr(mpv, "get_property"):
            res = mpv.get_property(name)
            if res is not None:
                return res
    except Exception:
        pass
    try:
        res = mpv[name]
        if res is not None:
            return res
    except Exception:
        pass
    return default


@Gtk.Template(resource_path="/io/github/rusmikev/CineHDR/hdr_diagnostics.ui")
class HdrDiagnosticsDialog(Adw.Dialog):
    __gtype_name__ = "HdrDiagnosticsDialog"

    status_row: Adw.ActionRow = Gtk.Template.Child()
    display_hdr_row: Adw.ActionRow = Gtk.Template.Child()
    compositor_cm_row: Adw.ActionRow = Gtk.Template.Child()
    monitor_hdr_row: Adw.ActionRow = Gtk.Template.Child()
    offload_row: Adw.ActionRow = Gtk.Template.Child()
    unsupported_reason_row: Adw.ActionRow = Gtk.Template.Child()
    color_state_row: Adw.ActionRow = Gtk.Template.Child()
    texture_format_row: Adw.ActionRow = Gtk.Template.Child()

    codec_row: Adw.ActionRow = Gtk.Template.Child()
    resolution_row: Adw.ActionRow = Gtk.Template.Child()
    hwdec_row: Adw.ActionRow = Gtk.Template.Child()
    dovi_profile_row: Adw.ActionRow = Gtk.Template.Child()
    primaries_row: Adw.ActionRow = Gtk.Template.Child()
    trc_row: Adw.ActionRow = Gtk.Template.Child()
    peak_luma_row: Adw.ActionRow = Gtk.Template.Child()
    target_row: Adw.ActionRow = Gtk.Template.Child()

    def __init__(self, window, **kwargs):
        super().__init__(**kwargs)
        self._win = window
        self._timer_id = None
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
        try:
            self.connect("closed", self._on_closed)
        except Exception:
            pass

    def _on_realize(self, *args):
        self.update_diagnostics()
        if self._timer_id is None:
            self._timer_id = GLib.timeout_add(500, self._on_timeout)

    def _on_unrealize(self, *args):
        self._stop_timer()

    def _on_closed(self, *args):
        self._stop_timer()

    def _stop_timer(self):
        if self._timer_id is not None:
            try:
                GLib.source_remove(self._timer_id)
            except Exception:
                pass
            self._timer_id = None

    def _on_timeout(self):
        if not self.get_realized() or not self.get_visible():
            self._timer_id = None
            return GLib.SOURCE_REMOVE
        self.update_diagnostics()
        return GLib.SOURCE_CONTINUE

    @Gtk.Template.Callback()
    def _on_refresh(self, *args):
        self.update_diagnostics()

    def update_diagnostics(self):
        gl_area = getattr(self._win, "gl_area", None)
        controller = getattr(gl_area, "hdr_controller", None) if gl_area else None
        mpv = getattr(self._win, "mpv", None)

        # 1. Output & Color State
        is_active = getattr(controller, "is_hdr_active", False) if controller else False
        is_content = getattr(controller, "is_hdr_content", False) if controller else False
        mode = getattr(controller, "hdr_mode", "auto") if controller else "auto"
        supported = check_hdr_support()

        if is_active and supported:
            self.status_row.set_subtitle(_("Active (Direct 16-bit HDR Pass-through)"))
        elif mode == "force-sdr":
            self.status_row.set_subtitle(_("Disabled (Force SDR mode)"))
        elif is_content:
            # Covers both "system unsupported" and "monitor HDR is off":
            # in either case the content is HDR and mpv tone-maps it to SDR.
            self.status_row.set_subtitle(_("Active (SDR Tone Mapping enabled)"))
        else:
            self.status_row.set_subtitle(_("Disabled (SDR Content)"))

        if supported:
            # GTK accepting the Rec.2100 color state does not guarantee the
            # monitor is actually in HDR mode — GTK/the compositor convert to
            # SDR otherwise, which is simpler than mpv tone mapping.
            self.display_hdr_row.set_subtitle(
                _("Yes (Wayland + GTK 4.16+) — final output depends on the compositor/monitor HDR mode")
            )
            self.unsupported_reason_row.set_visible(False)
        else:
            self.display_hdr_row.set_subtitle(_("No (Fallback to SDR / 8-bit)"))
            self.unsupported_reason_row.set_visible(True)
            self.unsupported_reason_row.set_subtitle(get_hdr_unsupported_reason())

        # Direct answer from the compositor registry: without a color
        # management global GTK cannot pass Rec.2100 PQ through, no matter
        # what the rows above say.
        cm = get_compositor_cm_support()
        if cm is True:
            self.compositor_cm_row.set_subtitle(
                _("Yes (wp_color_manager_v1 advertised)")
            )
        elif cm is False:
            self.compositor_cm_row.set_subtitle(
                _("No — compositor lacks wp_color_manager_v1, HDR pass-through impossible")
            )
        else:
            self.compositor_cm_row.set_subtitle(_("Unknown (probe unavailable)"))

        # Actual monitor state read from the output's image description —
        # this is what finally decides pass-through vs mpv tone mapping in
        # auto mode.
        states = None
        try:
            from . import wayland_output_hdr
            states = wayland_output_hdr.get_output_hdr_states()
        except Exception:
            states = None
        hint = getattr(controller, "output_hint", None) if controller else None
        if states is None:
            self.monitor_hdr_row.set_subtitle(_("Unknown (probe unavailable)"))
        elif hint and hint in states:
            info = states[hint]
            if info.hdr:
                peak = f", peak ~{int(info.max_lum)} nits" if info.max_lum else ""
                self.monitor_hdr_row.set_subtitle(
                    _("HDR active on {c} ({tf}{peak})").format(c=hint, tf=info.tf_name, peak=peak)
                )
            else:
                self.monitor_hdr_row.set_subtitle(
                    _("SDR on {c} — enable HDR in display settings for pass-through").format(c=hint)
                )
        else:
            hdr_outputs = [c for c, i in states.items() if i.hdr]
            if hdr_outputs:
                self.monitor_hdr_row.set_subtitle(
                    _("HDR active on: {list}").format(list=", ".join(sorted(hdr_outputs)))
                )
            else:
                self.monitor_hdr_row.set_subtitle(
                    _("SDR on all outputs — enable HDR in display settings for pass-through")
                )

        offload = getattr(self._win, "offload", None)
        try:
            enabled = offload.get_enabled() if offload else None
        except Exception:
            enabled = None
        if enabled is None:
            self.offload_row.set_subtitle(_("Unknown"))
        elif enabled == Gtk.GraphicsOffloadEnabled.DISABLED:
            self.offload_row.set_subtitle(
                _("Disabled (NVIDIA workaround) — HDR goes through GTK compositing")
            )
        else:
            self.offload_row.set_subtitle(_("Enabled (subsurface / direct scanout possible)"))

        if hasattr(Gdk, "ColorState") and hasattr(Gdk.ColorState, "get_rec2100_pq"):
            if is_active and supported:
                self.color_state_row.set_subtitle("Rec.2100 PQ (16-bit)")
            else:
                self.color_state_row.set_subtitle("sRGB (8-bit / SDR)")
        else:
            self.color_state_row.set_subtitle("N/A (GTK < 4.16)")

        if is_active and supported:
            self.texture_format_row.set_subtitle("GL_RGBA16F (16-bit Float, 64 bpp)")
        else:
            self.texture_format_row.set_subtitle("GL_RGBA8 (8-bit Int, 32 bpp)")

        # 2. Video Signal (libmpv)
        if not mpv:
            return

        params = get_mpv_prop(mpv, "video-params") or get_mpv_prop(mpv, "video-out-params") or {}
        if not isinstance(params, dict):
            params = {}

        try:
            codec = get_mpv_prop(mpv, "video-format") or get_mpv_prop(mpv, "video-codec")
            self.codec_row.set_subtitle(str(codec) if codec else _("No video loaded"))
        except Exception:
            self.codec_row.set_subtitle(_("Unknown"))

        try:
            w = params.get("w") or get_mpv_prop(mpv, "video-params/w") or get_mpv_prop(mpv, "width")
            h = params.get("h") or get_mpv_prop(mpv, "video-params/h") or get_mpv_prop(mpv, "height")
            pix = params.get("pixelformat") or get_mpv_prop(mpv, "video-params/pixelformat")
            if w and h:
                res_str = f"{w}x{h}"
                if pix:
                    res_str += f" ({pix})"
                self.resolution_row.set_subtitle(res_str)
            else:
                self.resolution_row.set_subtitle(_("Unknown"))
        except Exception:
            self.resolution_row.set_subtitle(_("Unknown"))

        try:
            hw = get_mpv_prop(mpv, "hwdec-current") or get_mpv_prop(mpv, "hwdec")
            if hw and str(hw).lower() not in ("no", "none", ""):
                self.hwdec_row.set_subtitle(f"{hw} ({_('GPU Acceleration active')})")
            else:
                self.hwdec_row.set_subtitle(_("Software / CPU Decoding"))
        except Exception:
            self.hwdec_row.set_subtitle(_("Unknown"))

        try:
            from .hdr_detection import get_dovi_info
            dovi = get_dovi_info(params, mpv)
            if dovi:
                profile = dovi.get("profile")
                level = dovi.get("level")
                if profile is None:
                    # colormatrix=dolbyvision is set for profile 5 and 8 alike,
                    # so it proves presence but not which profile.
                    desc = _("Detected — profile unknown (RPU not processed)")
                elif dovi.get("unsupported"):
                    desc = _("Profile {p} — IPT base, not renderable here; forced to SDR").format(p=profile)
                elif profile in (7, 8):
                    desc = _("Profile {p} — HDR10 base layer (RPU not processed)").format(p=profile)
                else:
                    desc = _("Profile {p} (RPU not processed)").format(p=profile)
                if level:
                    desc = f"{desc} · Level {level}"
                self.dovi_profile_row.set_subtitle(desc)
                self.dovi_profile_row.set_visible(True)
            elif is_content:
                self.dovi_profile_row.set_subtitle(_("No (Standard HDR10 / HLG)"))
                self.dovi_profile_row.set_visible(True)
            else:
                self.dovi_profile_row.set_visible(False)
        except Exception:
            self.dovi_profile_row.set_visible(False)

        try:
            prim = params.get("primaries") or get_mpv_prop(mpv, "video-params/primaries") or _("Unknown")
            self.primaries_row.set_subtitle(str(prim))

            gamma = params.get("gamma") or get_mpv_prop(mpv, "video-params/gamma") or _("Unknown")
            self.trc_row.set_subtitle(str(gamma))

            sig_peak = params.get("sig-peak") or get_mpv_prop(mpv, "video-params/sig-peak") or 0.0
            if sig_peak and float(sig_peak) > 0 and float(sig_peak) != 1.0:
                nits = int(float(sig_peak) * 203)
                self.peak_luma_row.set_subtitle(f"{float(sig_peak):.2f} (~{nits} nits)")
            else:
                self.peak_luma_row.set_subtitle(_("Standard / SDR (1.00)"))
        except Exception:
            self.primaries_row.set_subtitle(_("Unknown"))
            self.trc_row.set_subtitle(_("Unknown"))
            self.peak_luma_row.set_subtitle(_("Unknown"))

        try:
            t_trc = get_mpv_prop(mpv, "target-trc", "auto")
            t_prim = get_mpv_prop(mpv, "target-prim", "auto")
            t_peak = get_mpv_prop(mpv, "target-peak", "auto")
            peak_src = getattr(controller, "effective_peak_source", "auto") if controller else "auto"
            self.target_row.set_subtitle(f"TRC: {t_trc} | Prim: {t_prim} | Peak: {t_peak} ({peak_src})")
        except Exception:
            self.target_row.set_subtitle(_("Unknown"))
