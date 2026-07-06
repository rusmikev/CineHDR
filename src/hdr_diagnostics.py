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

from .hdr_detection import check_hdr_support


def get_mpv_prop(mpv, name, default=None):
    try:
        if hasattr(mpv, "_get_property"):
            res = mpv._get_property(name)
        elif hasattr(mpv, "get_property"):
            res = mpv.get_property(name)
        else:
            res = mpv[name]
        return res if res is not None else default
    except Exception:
        return default


@Gtk.Template(resource_path="/io/github/rusmikev/CineHDR/hdr_diagnostics.ui")
class HdrDiagnosticsDialog(Adw.Dialog):
    __gtype_name__ = "HdrDiagnosticsDialog"

    status_row: Adw.ActionRow = Gtk.Template.Child()
    display_hdr_row: Adw.ActionRow = Gtk.Template.Child()
    color_state_row: Adw.ActionRow = Gtk.Template.Child()
    texture_format_row: Adw.ActionRow = Gtk.Template.Child()

    codec_row: Adw.ActionRow = Gtk.Template.Child()
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
        mode = getattr(controller, "hdr_mode", "auto") if controller else "auto"

        if is_active:
            self.status_row.set_subtitle(_("Active (Tone Mapping enabled)"))
        elif mode == "force-sdr":
            self.status_row.set_subtitle(_("Disabled (Force SDR mode)"))
        else:
            self.status_row.set_subtitle(_("Disabled (SDR Content or Unsupported)"))

        supported = check_hdr_support()
        if supported:
            self.display_hdr_row.set_subtitle(_("Yes (Wayland + GTK 4.16+)"))
        else:
            self.display_hdr_row.set_subtitle(_("No (Fallback to SDR / 8-bit)"))

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

        try:
            codec = get_mpv_prop(mpv, "video-format") or get_mpv_prop(mpv, "video-codec")
            self.codec_row.set_subtitle(str(codec) if codec else _("No video loaded"))
        except Exception:
            self.codec_row.set_subtitle(_("Unknown"))

        try:
            params = get_mpv_prop(mpv, "video-params") or {}
            prim = params.get("primaries", _("Unknown"))
            self.primaries_row.set_subtitle(str(prim))

            gamma = params.get("gamma", _("Unknown"))
            self.trc_row.set_subtitle(str(gamma))

            sig_peak = params.get("sig-peak", 0.0)
            if sig_peak and float(sig_peak) > 0:
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
            t_hint = get_mpv_prop(mpv, "target-colorspace-hint", "no")
            self.target_row.set_subtitle(f"TRC: {t_trc} | Prim: {t_prim} | Peak: {t_peak} | Hint: {t_hint}")
        except Exception:
            self.target_row.set_subtitle(_("Unknown"))
