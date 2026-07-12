# hdr_menu.py
#
# Copyright 2026 Diego Povliuk / rusmikev
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gi
import logging
from typing import cast, Optional, Any
from gettext import gettext as _

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .hdr_controller import (
    HDR_MODES,
    HDR_PEAK_PRESETS,
    load_hdr_config,
    save_hdr_config,
    _get_hdr_settings,
)


@Gtk.Template(resource_path="/io/github/rusmikev/CineHDR/hdr_menu.ui")
class HdrMenuButton(Gtk.MenuButton):
    __gtype_name__ = "HdrMenuButton"

    hdr_mode_dropdown: Gtk.DropDown = Gtk.Template.Child()
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

        for dropdown in [self.hdr_mode_dropdown, self.hdr_peak_dropdown]:
            self._disable_dropdown_autohide(dropdown)

    def _disable_dropdown_autohide(self, dropdown: Gtk.DropDown):
        # Workaround for GTK4 bug: opening a DropDown inside a Popover breaks parent Popover autohide/close.
        # This workaround is required because GTK4 lacks a public API to access the dropdown's popover,
        # and removing this workaround breaks closing the HDR menu.
        try:
            child = dropdown.get_first_child()
            while child:
                if isinstance(child, Gtk.Popover):
                    child.set_autohide(False)
                    break
                child = child.get_next_sibling()
        except Exception as e:
            import logging
            logging.warning(f"Could not disable autohide for dropdown: {e}")

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

    def _on_active(self, *arg):
        if not self.get_active():
            return

        self._syncing_ui = True
        try:
            config = load_hdr_config()
            mode = config.get("hdr_mode", "auto")
            peak = config.get("hdr_target_peak", "auto")

            mode_idx = HDR_MODES.index(mode) if mode in HDR_MODES else 0
            self.hdr_mode_dropdown.set_selected(mode_idx)

            is_sdr_forced = (mode == "force-sdr")
            self.hdr_peak_row.set_sensitive(not is_sdr_forced)

            peak_idx = (
                HDR_PEAK_PRESETS.index(str(peak))
                if str(peak) in HDR_PEAK_PRESETS
                else 0
            )
            self.hdr_peak_dropdown.set_selected(peak_idx)
        except Exception as e:
            logger.error(f"Error syncing HDR UI: {e}")
        finally:
            self._syncing_ui = False

    @property
    def _controller(self) -> Any:
        return getattr(self.win.gl_area, "hdr_controller", self.win.gl_area)

    @Gtk.Template.Callback()
    def _on_hdr_reset(self, *args):
        self.hdr_mode_dropdown.set_selected(0)   # Default Auto
        self.hdr_peak_dropdown.set_selected(0)   # Default Auto
        ctrl = self._controller
        ctrl.hdr_mode = "auto"
        ctrl.hdr_target_peak = "auto"
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
        mode = HDR_MODES[idx] if 0 <= idx < len(HDR_MODES) else "auto"
        self._controller.hdr_mode = mode
        is_sdr_forced = (mode == "force-sdr")
        self.hdr_peak_row.set_sensitive(not is_sdr_forced)
        self.win.gl_area.queue_draw()
        self._save_hdr_full_config()

    @Gtk.Template.Callback()
    def _on_hdr_peak_changed(self, dropdown, gparam):
        if self._syncing_ui:
            return
        idx = dropdown.get_selected()
        ctrl = self._controller
        peak = HDR_PEAK_PRESETS[idx] if 0 <= idx < len(HDR_PEAK_PRESETS) else "auto"
        ctrl.hdr_target_peak = peak
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
            "hdr_target_peak": ctrl.hdr_target_peak
        }
        self._syncing_ui = True
        try:
            save_hdr_config(config)
        finally:
            self._syncing_ui = False

