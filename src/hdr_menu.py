# hdr_menu.py
#
# Copyright 2026 Diego Povliuk / rusmikev
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gi
from typing import cast
from gettext import gettext as _

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .video_widget import load_hdr_config, save_hdr_config

@Gtk.Template(resource_path="/io/github/rusmikev/CineHDR/hdr_menu.ui")
class HdrMenuButton(Gtk.MenuButton):
    __gtype_name__ = "HdrMenuButton"

    hdr_switch: Gtk.Switch = Gtk.Template.Child()
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
            from .video_widget import _get_hdr_settings
            self._gsettings = _get_hdr_settings()
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

        # Prevent dropdown popovers from stealing autohide
        popover_gamut = self.hdr_gamut_dropdown.get_first_child().get_next_sibling()  # type: ignore
        if popover_gamut:
            popover_gamut.set_autohide(False)  # type: ignore

        popover_peak = self.hdr_peak_dropdown.get_first_child().get_next_sibling()  # type: ignore
        if popover_peak:
            popover_peak.set_autohide(False)  # type: ignore

    def _on_active(self, *arg):
        if not self.get_active():
            return

        self._syncing_ui = True
        try:
            config = load_hdr_config()
            enabled = config.get("hdr_enabled", True)
            prim = config.get("hdr_target_prim", "auto")
            peak = config.get("hdr_target_peak", "auto")

            self.hdr_switch.set_active(enabled)
            self.hdr_gamut_row.set_sensitive(enabled)
            self.hdr_peak_row.set_sensitive(enabled)

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

    @Gtk.Template.Callback()
    def _on_hdr_reset(self, *args):
        self.hdr_switch.set_active(True)
        self.hdr_gamut_dropdown.set_selected(0)  # Default Auto (Recommended)
        self.hdr_peak_dropdown.set_selected(0)   # Default Auto
        self.win.gl_area.hdr_enabled = True
        self.win.gl_area.hdr_target_prim = "auto"
        self.win.gl_area.hdr_target_peak = "auto"
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_gamut_reset(self, *args):
        self.hdr_gamut_dropdown.set_selected(0)
        self.win.gl_area.hdr_target_prim = "auto"
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_peak_reset(self, *args):
        self.hdr_peak_dropdown.set_selected(0)
        self.win.gl_area.hdr_target_peak = "auto"
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_toggled(self, switch, gparam):
        if self._syncing_ui:
            return
        active = switch.get_active()
        self.win.gl_area.hdr_enabled = active
        self.hdr_gamut_row.set_sensitive(active)
        self.hdr_peak_row.set_sensitive(active)
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_gamut_changed(self, dropdown, gparam):
        if self._syncing_ui:
            return
        idx = dropdown.get_selected()
        gl_area = self.win.gl_area
        if idx == 0:
            gl_area.hdr_target_prim = "auto"
        elif idx == 1:
            gl_area.hdr_target_prim = "dci-p3"
        else:
            gl_area.hdr_target_prim = "bt.709"
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_peak_changed(self, dropdown, gparam):
        if self._syncing_ui:
            return
        idx = dropdown.get_selected()
        gl_area = self.win.gl_area
        peaks = ["auto", "200", "400", "600", "1000", "1600"]
        gl_area.hdr_target_peak = peaks[idx]
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    def _save_hdr_full_config(self):
        config = {
            "hdr_enabled": self.hdr_switch.get_active(),
            "hdr_target_prim": self.win.gl_area.hdr_target_prim,
            "hdr_target_peak": self.win.gl_area.hdr_target_peak
        }
        save_hdr_config(config)
