# hdr_menu.py
#
# Copyright 2026 Diego Povliuk / rusmikev
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gi
from typing import cast, Optional, Any
from gettext import gettext as _

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .hdr_controller import load_hdr_config, save_hdr_config, _get_hdr_settings


def _disable_dropdown_autohide(widget: Gtk.Widget):
    """Recursively find Gtk.Popover inside dropdown and disable autohide (Audit Finding 10)."""
    child = widget.get_first_child()
    while child:
        if isinstance(child, Gtk.Popover):
            child.set_autohide(False)
        else:
            _disable_dropdown_autohide(child)
        child = child.get_next_sibling()


@Gtk.Template(resource_path="/io/github/rusmikev/CineHDR/hdr_menu.ui")
class HdrMenuButton(Gtk.MenuButton):
    __gtype_name__ = "HdrMenuButton"

    hdr_mode_dropdown: Gtk.DropDown = Gtk.Template.Child()
    hdr_gamut_row: Gtk.Box = Gtk.Template.Child()
    hdr_gamut_dropdown: Gtk.DropDown = Gtk.Template.Child()
    hdr_peak_row: Gtk.Box = Gtk.Template.Child()
    hdr_peak_dropdown: Gtk.DropDown = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._syncing_ui = False
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
        self.connect("notify::active", self._on_active)
        try:
            self._gsettings = _get_hdr_settings()
            if self._gsettings:
                self._gsettings.connect("changed", self._on_gsettings_changed)
        except Exception:
            self._gsettings = None

    def _on_unrealize(self, *arg):
        if getattr(self, "_gsettings", None):
            try:
                self._gsettings.disconnect_by_func(self._on_gsettings_changed)
            except Exception:
                pass
            self._gsettings = None

    def _on_gsettings_changed(self, settings, key):
        if self.get_active() and not self._syncing_ui:
            self._on_active()

    def _on_realize(self, *arg):
        from .window import CineWindow
        self.win = cast(CineWindow, self.get_root())

        # Prevent dropdown popovers from stealing autohide safely (Audit Finding 10)
        _disable_dropdown_autohide(self.hdr_mode_dropdown)
        _disable_dropdown_autohide(self.hdr_gamut_dropdown)
        _disable_dropdown_autohide(self.hdr_peak_dropdown)

    def _on_active(self, *arg):
        if not self.get_active():
            return

        self._syncing_ui = True
        try:
            config = load_hdr_config()
            mode = config.get("hdr_mode", "auto")
            prim = config.get("hdr_target_prim", "auto")
            peak = config.get("hdr_target_peak", "auto")

            if mode == "auto":
                self.hdr_mode_dropdown.set_selected(0)
            elif mode == "force-hdr":
                self.hdr_mode_dropdown.set_selected(1)
            else:
                self.hdr_mode_dropdown.set_selected(2)

            is_sdr_forced = (mode == "force-sdr")
            self.hdr_gamut_row.set_sensitive(not is_sdr_forced)
            self.hdr_peak_row.set_sensitive(not is_sdr_forced)

            if prim == "auto":
                self.hdr_gamut_dropdown.set_selected(0)
            elif prim == "dci-p3":
                self.hdr_gamut_dropdown.set_selected(1)
            else:
                self.hdr_gamut_dropdown.set_selected(2)

            peak_map = {"auto": 0, "200": 1, "400": 2, "600": 3, "1000": 4, "1600": 5}
            self.hdr_peak_dropdown.set_selected(peak_map.get(str(peak), 0))
        except Exception as e:
            print(f"Error syncing HDR UI: {e}")
        finally:
            self._syncing_ui = False

    @property
    def _controller(self) -> Any:
        return getattr(self.win.gl_area, "hdr_controller", self.win.gl_area)

    @Gtk.Template.Callback()
    def _on_hdr_reset(self, *args):
        self.hdr_mode_dropdown.set_selected(0)   # Default Auto
        self.hdr_gamut_dropdown.set_selected(0)  # Default Auto (Recommended)
        self.hdr_peak_dropdown.set_selected(0)   # Default Auto
        ctrl = self._controller
        ctrl.hdr_mode = "auto"
        ctrl.hdr_target_prim = "auto"
        ctrl.hdr_target_peak = "auto"
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_gamut_reset(self, *args):
        self.hdr_gamut_dropdown.set_selected(0)
        self._controller.hdr_target_prim = "auto"
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_peak_reset(self, *args):
        self.hdr_peak_dropdown.set_selected(0)
        self._controller.hdr_target_peak = "auto"
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_mode_changed(self, dropdown, gparam):
        if self._syncing_ui:
            return
        idx = dropdown.get_selected()
        if idx == 0:
            mode = "auto"
        elif idx == 1:
            mode = "force-hdr"
        else:
            mode = "force-sdr"
        self._controller.hdr_mode = mode
        is_sdr_forced = (mode == "force-sdr")
        self.hdr_gamut_row.set_sensitive(not is_sdr_forced)
        self.hdr_peak_row.set_sensitive(not is_sdr_forced)
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_gamut_changed(self, dropdown, gparam):
        if self._syncing_ui:
            return
        idx = dropdown.get_selected()
        ctrl = self._controller
        if idx == 0:
            ctrl.hdr_target_prim = "auto"
        elif idx == 1:
            ctrl.hdr_target_prim = "dci-p3"
        else:
            ctrl.hdr_target_prim = "bt.709"
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_peak_changed(self, dropdown, gparam):
        if self._syncing_ui:
            return
        idx = dropdown.get_selected()
        ctrl = self._controller
        peaks = ["auto", "200", "400", "600", "1000", "1600"]
        ctrl.hdr_target_peak = peaks[idx]
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_diagnostics(self, *args):
        from .hdr_diagnostics import HdrDiagnosticsDialog
        if hasattr(self, "popdown"):
            self.popdown()
        elif self.get_popover():
            self.get_popover().popdown()
        diag = HdrDiagnosticsDialog(self.win)
        diag.present(self.win)

    def _save_hdr_full_config(self):
        ctrl = self._controller
        config = {
            "hdr_mode": ctrl.hdr_mode,
            "hdr_enabled": (ctrl.hdr_mode != "force-sdr"),
            "hdr_target_prim": ctrl.hdr_target_prim,
            "hdr_target_peak": ctrl.hdr_target_peak
        }
        save_hdr_config(config)

