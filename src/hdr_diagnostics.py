# hdr_diagnostics.py
#
# Copyright 2026 Diego Povliuk / rusmikev
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gi
import logging
import math
import os
from datetime import datetime, timezone
from gettext import gettext as _
from typing import Any

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk

from .diagnostics_report import (
    build_video_output_report,
    collect_performance_facts,
    collect_video_output_facts,
    generate_video_output_report,
)
from .hdr_detection import (
    check_hdr_support,
    get_compositor_cm_support,
    get_hdr_unsupported_reason,
)
from .preferences import settings
from .render_backend import (
    RenderBackend,
    RenderSelectionSource,
    dolby_vision_render_capability,
    renderer_preference_requires_restart,
)


logger = logging.getLogger(__name__)


def _positive_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _source_peak_nits(params: dict) -> int | None:
    """Convert mpv's signal peak (relative to 203-nit white) to nits."""
    relative_peak = _positive_float(params.get("sig-peak"))
    if relative_peak is None or relative_peak == 1.0:
        return None
    return round(relative_peak * 203)


def _hdr_status_text(
    *,
    is_active: bool,
    supported: bool,
    mode: str,
    is_content: bool,
    source_peak_nits: int | None,
    target_peak,
) -> str:
    """Describe HDR transport separately from optional HDR-to-HDR mapping."""
    if is_active and supported:
        from .hdr_detection import is_surface_submission_proven
        # The current gate can be overridden for tests; it is not a live
        # protocol/presentation oracle and must never certify HDR output.
        override = is_surface_submission_proven()
        evidence = _("compositor submission unverified")
        if override:
            evidence += _("; test override enabled, not proof")
        numeric_target = _positive_float(target_peak)
        if (
            source_peak_nits is not None
            and numeric_target is not None
            and numeric_target < source_peak_nits * 0.9
        ):
            return _(
                "Active (Rec.2100 PQ target prepared · tone-mapped ~{source} → {target} nits; {evidence})"
            ).format(source=source_peak_nits, target=round(numeric_target), evidence=evidence)
        return _("Active (Rec.2100 PQ target prepared; {evidence})").format(evidence=evidence)
    if mode == "force-sdr":
        return _("Disabled (Force SDR mode)")
    if is_content:
        return _("Active (SDR Tone Mapping enabled)")
    return _("Disabled (SDR Content)")


def get_mpv_prop(mpv, name, default=None):
    if mpv is None:
        return default
    if hasattr(mpv, "get_property"):
        try:
            res = mpv.get_property(name)
            return res if res is not None else default
        except Exception:
            pass
    try:
        res = mpv[name]
        return res if res is not None else default
    except Exception:
        pass
    try:
        attr_name = name.replace("-", "_")
        if hasattr(mpv, attr_name):
            res = getattr(mpv, attr_name)
            return res if res is not None else default
    except Exception:
        pass
    return default


def format_dovi_rpu_warnings(count: int | None, text: str | None) -> str:
    """Format observed RPU log warnings without inferring frame/stream damage."""
    if count is None:
        return _("Unknown (warning observation unavailable)")
    if count == 0:
        return _("None (0 warning messages logged)")
    msg = _("1 warning message logged") if count == 1 else _("{count} warning messages logged").format(count=count)
    if text:
        return f"{msg} ({text})"
    return msg


def get_dovi_rpu_warning_state(win, mpv=None) -> tuple[int | None, str | None]:
    """Retrieve player-instance tracked RPU warning state safely."""
    count = None
    text = None
    if win is not None:
        count = getattr(win, "dovi_rpu_warning_count", None)
        text = getattr(win, "dovi_rpu_warning_text", None)
    if count is None and mpv is not None:
        count = getattr(mpv, "_dovi_rpu_warning_count", None)
        text = getattr(mpv, "_dovi_rpu_warning_text", None)
    return count, text


@Gtk.Template(resource_path="/io/github/rusmikev/CineHDR/hdr_diagnostics.ui")
class HdrDiagnosticsDialog(Adw.Dialog):
    __gtype_name__ = "HdrDiagnosticsDialog"

    copy_btn: Gtk.Button = Gtk.Template.Child()

    status_row: Adw.ActionRow = Gtk.Template.Child()
    display_hdr_row: Adw.ActionRow = Gtk.Template.Child()
    compositor_cm_row: Adw.ActionRow = Gtk.Template.Child()
    monitor_hdr_row: Adw.ActionRow = Gtk.Template.Child()
    offload_row: Adw.ActionRow = Gtk.Template.Child()
    unsupported_reason_row: Adw.ActionRow = Gtk.Template.Child()
    color_state_row: Adw.ActionRow = Gtk.Template.Child()
    texture_format_row: Adw.ActionRow = Gtk.Template.Child()

    renderer_configured_row: Adw.ActionRow = Gtk.Template.Child()
    renderer_requested_row: Adw.ActionRow = Gtk.Template.Child()
    renderer_active_row: Adw.ActionRow = Gtk.Template.Child()
    renderer_reason_row: Adw.ActionRow = Gtk.Template.Child()
    renderer_dependency_row: Adw.ActionRow = Gtk.Template.Child()
    renderer_target_row: Adw.ActionRow = Gtk.Template.Child()
    gpu_next_capability_row: Adw.ActionRow = Gtk.Template.Child()
    dovi_capability_row: Adw.ActionRow = Gtk.Template.Child()

    codec_row: Adw.ActionRow = Gtk.Template.Child()
    resolution_row: Adw.ActionRow = Gtk.Template.Child()
    hwdec_row: Adw.ActionRow = Gtk.Template.Child()
    dovi_profile_row: Adw.ActionRow = Gtk.Template.Child()
    dovi_rpu_row: Adw.ActionRow = Gtk.Template.Child()
    primaries_row: Adw.ActionRow = Gtk.Template.Child()
    trc_row: Adw.ActionRow = Gtk.Template.Child()
    peak_luma_row: Adw.ActionRow = Gtk.Template.Child()
    target_row: Adw.ActionRow = Gtk.Template.Child()

    perf_fps_row: Adw.ActionRow = Gtk.Template.Child()
    perf_dropped_row: Adw.ActionRow = Gtk.Template.Child()
    perf_delayed_row: Adw.ActionRow = Gtk.Template.Child()
    perf_rendered_row: Adw.ActionRow = Gtk.Template.Child()
    perf_avsync_row: Adw.ActionRow = Gtk.Template.Child()
    perf_pipeline_row: Adw.ActionRow = Gtk.Template.Child()

    def __init__(self, window, **kwargs):
        super().__init__(**kwargs)
        self._win = window
        self._timer_id = None
        self._copy_feedback_timer_id = None
        self._renderer_report_fields: dict[str, object] = {}
        self._performance_report_fields: dict[str, object] = {}
        self._sampled_dovi_rpu_fact: str | None = None
        self._sampled_configured_decoder: str | None = None
        self._sampled_active_decoder: str | None = None
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
        self._stop_copy_feedback()

    def _on_closed(self, *args):
        self._stop_timer()
        self._stop_copy_feedback()

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

    def _stop_copy_feedback(self):
        if self._copy_feedback_timer_id is not None:
            try:
                GLib.source_remove(self._copy_feedback_timer_id)
            except Exception:
                pass
            self._copy_feedback_timer_id = None
        button = getattr(self, "copy_btn", None)
        if button is not None and hasattr(button, "set_icon_name"):
            button.set_icon_name("edit-copy-symbolic")
            button.set_tooltip_text(_("Copy diagnostics"))

    def _reset_copy_feedback(self):
        self._copy_feedback_timer_id = None
        self._stop_copy_feedback()
        return GLib.SOURCE_REMOVE

    def _visible_row_values(self, rows):
        values = {}
        for label, attribute in rows:
            row = getattr(self, attribute, None)
            if row is None or not hasattr(row, "get_subtitle"):
                continue
            if hasattr(row, "get_visible") and not row.get_visible():
                continue
            values[label] = row.get_subtitle()
        return values

    @classmethod
    def generate_report(cls, win: Any) -> str:
        """Single source of truth convenience entry point for generating the text report."""
        return generate_video_output_report(win)

    def _copy_report(self) -> str:
        snapshot = getattr(self, "_current_snapshot", None)
        if snapshot is not None:
            return build_video_output_report(
                snapshot.renderer,
                snapshot.output,
                snapshot.video,
                performance=snapshot.performance,
            )

        if not getattr(self, "_renderer_report_fields", None) and not hasattr(self, "status_row"):
            return generate_video_output_report(self._win)
        output = self._visible_row_values(
            (
                ("HDR Status", "status_row"),
                ("Display HDR Supported", "display_hdr_row"),
                ("Compositor Color Management", "compositor_cm_row"),
                ("Monitor HDR State", "monitor_hdr_row"),
                ("Graphics Offload", "offload_row"),
                ("System HDR Limitation", "unsupported_reason_row"),
                ("Gdk.ColorState", "color_state_row"),
                ("FBO Format", "texture_format_row"),
            )
        )
        video = self._visible_row_values(
            (
                ("Video Codec / Format", "codec_row"),
                ("Resolution & Pixel Format", "resolution_row"),
                ("Hardware Acceleration", "hwdec_row"),
                ("Dolby Vision Profile", "dovi_profile_row"),
                ("Dolby Vision RPU Warnings", "dovi_rpu_row"),
                ("Color Primaries", "primaries_row"),
                ("Transfer Characteristics", "trc_row"),
                ("Peak Luminance", "peak_luma_row"),
                ("Target Tone Mapping", "target_row"),
            )
        )
        video["Configured decoder"] = (
            getattr(self, "_sampled_configured_decoder", None) or "Unknown"
        )
        video["Active decoder"] = (
            getattr(self, "_sampled_active_decoder", None) or "Unknown"
        )
        video["Hardware decoding device"] = "Unknown (not reported by libmpv)"
        rpu_fact = getattr(self, "_sampled_dovi_rpu_fact", None)
        if rpu_fact is None:
            if hasattr(self, "dovi_rpu_row") and hasattr(self.dovi_rpu_row, "get_subtitle"):
                sub = self.dovi_rpu_row.get_subtitle()
                if sub and sub != "Checking...":
                    rpu_fact = sub
            if rpu_fact is None:
                mpv = getattr(self._win, "mpv", None)
                if mpv is None and hasattr(self._win, "player"):
                    mpv = getattr(self._win.player, "mpv", None)
                rpu_count, rpu_text = get_dovi_rpu_warning_state(self._win, mpv)
                rpu_fact = format_dovi_rpu_warnings(rpu_count, rpu_text)
            self._sampled_dovi_rpu_fact = rpu_fact
        video["Dolby Vision RPU Warnings"] = rpu_fact
        performance = dict(self._performance_report_fields) if getattr(self, "_performance_report_fields", None) else None
        renderer = getattr(self, "_renderer_report_fields", None) or {}
        return build_video_output_report(
            renderer, output, video, performance=performance
        )

    @Gtk.Template.Callback()
    def _on_copy(self, *args):
        self.update_diagnostics()
        report = self._copy_report()
        try:
            display = self.get_display()
            clipboard = display.get_clipboard()
            clipboard.set(report)
        except Exception:
            logger.exception("Failed to copy video output diagnostics")
            return

        self._stop_copy_feedback()
        self.copy_btn.set_icon_name("object-select-symbolic")
        self.copy_btn.set_tooltip_text(_("Copied"))
        self._copy_feedback_timer_id = GLib.timeout_add(
            1500, self._reset_copy_feedback
        )

    def update_diagnostics(self):
        snapshot = collect_video_output_facts(self._win)
        self._current_snapshot = snapshot
        self._renderer_report_fields = dict(snapshot.renderer)
        self._performance_report_fields = dict(snapshot.performance)
        self._sampled_configured_decoder = str(snapshot.video.get("Configured decoder", "unknown"))
        self._sampled_active_decoder = str(snapshot.video.get("Active decoder", "unknown"))
        self._sampled_dovi_rpu_fact = str(snapshot.video.get("Dolby Vision RPU Warnings", ""))

        for row_name, subtitle in snapshot.ui_subtitles.items():
            row = getattr(self, row_name, None)
            if row is not None and hasattr(row, "set_subtitle"):
                row.set_subtitle(subtitle)

        for row_name, visible in snapshot.ui_visibilities.items():
            row = getattr(self, row_name, None)
            if row is not None and hasattr(row, "set_visible"):
                row.set_visible(visible)

        gl_area = getattr(self._win, "_video_area", None) or getattr(
            self._win, "gl_area", None
        )
        mpv = getattr(self._win, "mpv", None)
        if mpv is None and hasattr(self._win, "player"):
            mpv = getattr(self._win.player, "mpv", None)
        params = (
            get_mpv_prop(mpv, "video-params")
            or get_mpv_prop(mpv, "video-out-params")
            or {}
        )
        if not isinstance(params, dict):
            params = {}

        self._update_stream_info(mpv, params)
        self._update_performance_info(mpv, gl_area)

    def _update_stream_info(self, mpv=None, params=None):
        snapshot = getattr(self, "_current_snapshot", None)
        if snapshot is not None:
            self._sampled_configured_decoder = str(snapshot.video.get("Configured decoder", "unknown"))
            self._sampled_active_decoder = str(snapshot.video.get("Active decoder", "unknown"))
            self._sampled_dovi_rpu_fact = str(snapshot.video.get("Dolby Vision RPU Warnings", ""))
            for row_name in ("codec_row", "resolution_row", "hwdec_row", "dovi_profile_row", "dovi_rpu_row"):
                row = getattr(self, row_name, None)
                if row is not None and hasattr(row, "set_subtitle") and row_name in snapshot.ui_subtitles:
                    row.set_subtitle(snapshot.ui_subtitles[row_name])
            return

        if mpv is None:
            mpv = getattr(self._win, "mpv", None)
            if mpv is None and hasattr(self._win, "player"):
                mpv = getattr(self._win.player, "mpv", None)
        if not mpv:
            return

        if params is None:
            params = (
                get_mpv_prop(mpv, "video-params")
                or get_mpv_prop(mpv, "video-out-params")
                or {}
            )
        if not isinstance(params, dict):
            params = {}

        if hasattr(self, "codec_row") and hasattr(self.codec_row, "set_subtitle"):
            try:
                codec = get_mpv_prop(mpv, "video-format") or get_mpv_prop(mpv, "video-codec")
                self.codec_row.set_subtitle(str(codec) if codec else _("No video loaded"))
            except Exception:
                self.codec_row.set_subtitle(_("Unknown"))

        if hasattr(self, "resolution_row") and hasattr(self.resolution_row, "set_subtitle"):
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

        hw_current = None
        hw_config = None
        if mpv is not None:
            try:
                hw_current = get_mpv_prop(mpv, "hwdec-current")
            except Exception:
                pass
            try:
                hw_config = get_mpv_prop(mpv, "hwdec")
            except Exception:
                pass

        hw_config_str = str(hw_config) if hw_config else "unknown"
        hw_current_str = str(hw_current) if hw_current else "unknown"
        self._sampled_configured_decoder = hw_config_str
        self._sampled_active_decoder = hw_current_str

        if hasattr(self, "hwdec_row") and hasattr(self.hwdec_row, "set_subtitle"):
            try:
                if hw_current and str(hw_current).lower() not in ("no", "none", ""):
                    self.hwdec_row.set_subtitle(
                        f"{hw_current} ({_('GPU Acceleration active')}; configured: {hw_config_str})"
                    )
                elif hw_current and str(hw_current).lower() in ("no", "none"):
                    self.hwdec_row.set_subtitle(
                        f"{_('Software / CPU Decoding')} (active: no; configured: {hw_config_str})"
                    )
                elif hw_current is None or str(hw_current).strip() == "":
                    if hw_config and str(hw_config).lower() not in ("no", "none", ""):
                        self.hwdec_row.set_subtitle(f"{_('Unknown (configured: ')}{hw_config_str})")
                    else:
                        self.hwdec_row.set_subtitle(_("Unknown"))
                else:
                    self.hwdec_row.set_subtitle(
                        f"{_('Software / CPU Decoding')} (active: {hw_current}; configured: {hw_config_str})"
                    )
            except Exception:
                self.hwdec_row.set_subtitle(_("Unknown"))

    def _update_performance_info(self, mpv=None, gl_area=None):
        snapshot = getattr(self, "_current_snapshot", None)
        if snapshot is not None:
            self._performance_report_fields = dict(snapshot.performance)
            for row_name in (
                "perf_fps_row",
                "perf_dropped_row",
                "perf_delayed_row",
                "perf_rendered_row",
                "perf_avsync_row",
                "perf_pipeline_row",
            ):
                row = getattr(self, row_name, None)
                if row is not None and hasattr(row, "set_subtitle") and row_name in snapshot.ui_subtitles:
                    row.set_subtitle(snapshot.ui_subtitles[row_name])
            return

        if mpv is None:
            mpv = getattr(self._win, "mpv", None)
            if mpv is None and hasattr(self._win, "player"):
                mpv = getattr(self._win.player, "mpv", None)
        if gl_area is None:
            gl_area = getattr(self._win, "_video_area", None) or getattr(
                self._win, "gl_area", None
            )

        snapshot = getattr(self, "_current_snapshot", None)
        sample_time = snapshot.sample_time_utc if snapshot else None
        performance, perf_ui = collect_performance_facts(mpv, gl_area, sample_time_utc=sample_time)
        self._performance_report_fields = performance

        for row_name in (
            "perf_fps_row",
            "perf_dropped_row",
            "perf_delayed_row",
            "perf_rendered_row",
            "perf_avsync_row",
            "perf_pipeline_row",
        ):
            row = getattr(self, row_name, None)
            if row is not None and hasattr(row, "set_subtitle") and row_name in perf_ui:
                row.set_subtitle(perf_ui[row_name])
