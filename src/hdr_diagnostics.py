# hdr_diagnostics.py
#
# Copyright 2026 Diego Povliuk / rusmikev
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gi
import logging
from gettext import gettext as _

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk

from .diagnostics_report import build_video_output_report
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
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


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
        numeric_target = _positive_float(target_peak)
        if (
            source_peak_nits is not None
            and numeric_target is not None
            and numeric_target < source_peak_nits * 0.9
        ):
            return _(
                "Active (HDR output · tone-mapped ~{source} → {target} nits)"
            ).format(source=source_peak_nits, target=round(numeric_target))
        return _("Active (Rec.2100 PQ HDR output)")
    if mode == "force-sdr":
        return _("Disabled (Force SDR mode)")
    if is_content:
        return _("Active (SDR Tone Mapping enabled)")
    return _("Disabled (SDR Content)")


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
    primaries_row: Adw.ActionRow = Gtk.Template.Child()
    trc_row: Adw.ActionRow = Gtk.Template.Child()
    peak_luma_row: Adw.ActionRow = Gtk.Template.Child()
    target_row: Adw.ActionRow = Gtk.Template.Child()

    def __init__(self, window, **kwargs):
        super().__init__(**kwargs)
        self._win = window
        self._timer_id = None
        self._copy_feedback_timer_id = None
        self._renderer_report_fields: dict[str, object] = {}
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

    def _copy_report(self) -> str:
        output = self._visible_row_values(
            (
                ("HDR Status", "status_row"),
                ("Display HDR Supported", "display_hdr_row"),
                ("Compositor Color Management", "compositor_cm_row"),
                ("Monitor HDR State", "monitor_hdr_row"),
                ("Graphics Offload", "offload_row"),
                ("System HDR Limitation", "unsupported_reason_row"),
                ("Gdk.ColorState", "color_state_row"),
                ("Surface Format", "texture_format_row"),
            )
        )
        video = self._visible_row_values(
            (
                ("Video Codec / Format", "codec_row"),
                ("Resolution & Pixel Format", "resolution_row"),
                ("Hardware Acceleration", "hwdec_row"),
                ("Dolby Vision Profile", "dovi_profile_row"),
                ("Color Primaries", "primaries_row"),
                ("Transfer Characteristics", "trc_row"),
                ("Peak Luminance", "peak_luma_row"),
                ("Target Tone Mapping", "target_row"),
            )
        )
        return build_video_output_report(
            self._renderer_report_fields, output, video
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
        gl_area = getattr(self._win, "_video_area", None) or getattr(
            self._win, "gl_area", None
        )
        controller = getattr(gl_area, "hdr_controller", None) if gl_area else None
        mpv = getattr(self._win, "mpv", None)
        params = (
            get_mpv_prop(mpv, "video-params")
            or get_mpv_prop(mpv, "video-out-params")
            or {}
        )
        if not isinstance(params, dict):
            params = {}

        # 0. Process-immutable renderer selection and current session state.
        selection = getattr(gl_area, "render_backend_selection", None)
        configured = settings.get_string("render-backend")
        requested = getattr(gl_area, "render_backend_requested", None)
        source = getattr(gl_area, "render_backend_source", None)
        active = getattr(gl_area, "render_backend_active", None)
        fallback_reason = getattr(
            gl_area, "render_backend_fallback_reason", None
        )
        restart_reason = getattr(
            gl_area, "_render_restart_required_reason", None
        )
        session_status = getattr(
            gl_area, "render_session_status", "not-initialized"
        )
        failure_reason = getattr(gl_area, "render_failure_reason", None)

        def set_renderer_row(name, subtitle):
            row = getattr(self, name, None)
            if row is not None and hasattr(row, "set_subtitle"):
                row.set_subtitle(subtitle)

        configured_label = (
            _("GPU Next · Experimental")
            if configured == "gpu-next"
            else _("Standard · OpenGL")
        )
        set_renderer_row("renderer_configured_row", configured_label)

        requested_value = getattr(requested, "value", requested) or "unknown"
        source_value = getattr(source, "value", source) or "unknown"
        set_renderer_row(
            "renderer_requested_row",
            _("{renderer} (source: {source})").format(
                renderer=requested_value, source=source_value
            ),
        )
        active_label = {
            "opengl": _("Standard · OpenGL"),
            "opengl-next": _("GPU Next · Experimental"),
        }.get(active, _("Not initialized"))
        if session_status in (
            "initialization-failure",
            "runtime-failure",
            "lifecycle-failure",
        ):
            active_label = _("{renderer} — stopped; restart required").format(
                renderer=active_label
            )
        set_renderer_row("renderer_active_row", active_label)

        preference_restart = bool(
            selection
            and renderer_preference_requires_restart(selection, configured)
        )
        if failure_reason or restart_reason:
            visible_status = _("Stopped · Restart required")
        elif fallback_reason:
            visible_status = _("Fallback to Standard")
        elif preference_restart:
            visible_status = _("Restart required")
        elif selection and selection.source is RenderSelectionSource.ENVIRONMENT:
            visible_status = _("Environment override active")
        else:
            visible_status = _("Running normally")
        set_renderer_row("renderer_reason_row", visible_status)

        runtime = getattr(gl_area, "render_runtime", {}) or {}
        dependency_summary = _("Not initialized")
        if runtime:
            dependency_summary = (
                f"{runtime.get('mpv_version', 'unknown')} · "
                f"libplacebo {runtime.get('libplacebo_version', 'unknown')} · "
                f"FFmpeg {runtime.get('ffmpeg_version', 'unknown')} · "
                f"{runtime.get('libmpv_path', 'unknown')}"
            )
        set_renderer_row("renderer_dependency_row", dependency_summary)

        target_format = getattr(gl_area, "render_target_format", None)
        target_depth = getattr(gl_area, "render_target_depth", None)
        if target_format == "GL_RGBA16F" and target_depth == 16:
            target_summary = _("RGBA16F · 16-bit float")
        elif target_format == "GL_RGBA8" and target_depth == 8:
            target_summary = _("RGBA8 · 8-bit")
        elif target_format and target_depth:
            target_summary = f"{str(target_format).removeprefix('GL_')} · {target_depth}-bit"
        else:
            target_summary = _("Waiting for first frame")
        set_renderer_row("renderer_target_row", target_summary)

        requested_is_next = requested in (
            RenderBackend.GPU_NEXT,
            RenderBackend.AUTO,
        )
        if active == "opengl-next" and session_status == "active":
            gpu_next_status = _("Available and active (experimental API)")
        elif active == "opengl-next":
            gpu_next_status = _("API initialized, but the renderer is stopped")
        elif fallback_reason:
            gpu_next_status = _("Unavailable in this session; standard renderer active")
        elif requested_is_next and session_status == "initialization-failure":
            gpu_next_status = _("Requested, but initialization failed")
        elif requested_is_next and session_status in (
            "runtime-failure",
            "lifecycle-failure",
        ):
            gpu_next_status = _("Requested, but the renderer is stopped")
        elif requested_is_next:
            gpu_next_status = _("Requested; waiting for initialization")
        else:
            gpu_next_status = _("Not requested in this session")
        set_renderer_row("gpu_next_capability_row", gpu_next_status)

        dovi_capability = dolby_vision_render_capability(
            active, session_status, runtime
        )
        if active == "opengl" and session_status not in (
            "runtime-failure",
            "lifecycle-failure",
        ):
            dovi_status = _("Unsupported by the active standard renderer")
        elif dovi_capability.rpu_path_available:
            dovi_status = _(
                "RPU render path available; color validation pending"
            )
        elif active == "opengl-next" and session_status == "active":
            dovi_status = _(
                "Experimental renderer active; dependency stack unvalidated"
            )
        else:
            dovi_status = _("Unavailable; renderer capability unknown")
        set_renderer_row(
            "dovi_capability_row",
            dovi_status,
        )

        startup_configured = getattr(
            getattr(selection, "configured", None), "value", "unknown"
        )
        runtime_configuration = runtime.get("mpv_configuration")
        if not runtime_configuration or runtime_configuration == "unknown":
            runtime_configuration = get_mpv_prop(
                mpv, "mpv-configuration", "unknown"
            )
        self._renderer_report_fields = {
            "Selected for next launch": configured,
            "Configured at process start": startup_configured,
            "Requested this session": requested_value,
            "Request source": source_value,
            "Creation fallback allowed": getattr(
                selection, "allow_creation_fallback", False
            ),
            "Active API": active or "not-initialized",
            "Session status": session_status,
            "Preference restart pending": preference_restart,
            "Startup fallback reason": fallback_reason or "none",
            "Renderer failure reason": failure_reason or "none",
            "Lifecycle restart reason": restart_reason or "none",
            "GPU Next capability": gpu_next_status,
            "Dolby Vision capability": dovi_status,
            "Dolby Vision RPU path evidence": dovi_capability.reason,
            "Dolby Vision color validation": (
                "pending" if dovi_capability.rpu_path_available else "unavailable"
            ),
            "Dolby Vision reference A/B": (
                "rendered-pixel effect observed with software decode; "
                "hardware and color validation pending"
                if dovi_capability.rpu_path_available
                else "unavailable"
            ),
            "Render target format": target_format or "not-rendered",
            "Render target depth": target_depth or "not-rendered",
            "libmpv path": runtime.get("libmpv_path", "unknown"),
            "mpv version": runtime.get("mpv_version", "unknown"),
            "libplacebo version": runtime.get("libplacebo_version", "unknown"),
            "FFmpeg version": runtime.get("ffmpeg_version", "unknown"),
            "mpv configuration": runtime_configuration,
        }

        # 1. Output & Color State
        is_active = getattr(controller, "is_hdr_active", False) if controller else False
        is_content = getattr(controller, "is_hdr_content", False) if controller else False
        mode = getattr(controller, "hdr_mode", "auto") if controller else "auto"
        supported = check_hdr_support()
        target_peak = get_mpv_prop(mpv, "target-peak", "auto")
        self.status_row.set_subtitle(
            _hdr_status_text(
                is_active=is_active,
                supported=supported,
                mode=mode,
                is_content=is_content,
                source_peak_nits=_source_peak_nits(params),
                target_peak=target_peak,
            )
        )

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
                _("Disabled — output goes through GTK compositing")
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
                mapped_to_next = (
                    dovi_capability.rpu_path_available
                    and "dolbyvision"
                    in str(params.get("colormatrix", "")).lower()
                )
                if profile is None:
                    # colormatrix=dolbyvision is set for profile 5 and 8 alike,
                    # so it proves presence but not which profile.
                    if mapped_to_next:
                        desc = _(
                            "Detected — RPU metadata mapped to GPU Next "
                            "(profile unknown; validation pending)"
                        )
                    elif active == "opengl-next":
                        desc = _(
                            "Detected — profile unknown; RPU mapping is not "
                            "verified for this build"
                        )
                    else:
                        desc = _("Detected — profile unknown (RPU not processed)")
                elif dovi.get("unsupported"):
                    if mapped_to_next:
                        desc = _(
                            "Profile {p} — RPU path observed, but blocked pending "
                            "color validation; forced to SDR"
                        ).format(p=profile)
                    elif active == "opengl-next" and session_status == "active":
                        desc = _(
                            "Profile {p} — blocked pending GPU Next validation; "
                            "forced to SDR"
                        ).format(p=profile)
                    else:
                        desc = _(
                            "Profile {p} — unsupported by the current renderer; "
                            "forced to SDR"
                        ).format(p=profile)
                elif mapped_to_next and profile == 7:
                    desc = _(
                        "Profile 7 — RPU metadata mapped to GPU Next; "
                        "enhancement layer unsupported; validation pending"
                    )
                elif mapped_to_next:
                    desc = _(
                        "Profile {p} — RPU metadata mapped to GPU Next "
                        "(validation pending)"
                    ).format(p=profile)
                elif profile == 7:
                    desc = _(
                        "Profile 7 — base layer only "
                        "(RPU / enhancement layer not processed)"
                    )
                elif profile == 8:
                    desc = _(
                        "Profile 8 — base-layer fallback "
                        "(RPU not processed; compatibility ID unknown)"
                    )
                else:
                    desc = _("Profile {p} (RPU not processed)").format(p=profile)
                if level:
                    desc = f"{desc} · Level {level}"
                self.dovi_profile_row.set_subtitle(desc)
                self.dovi_profile_row.set_visible(True)
                self._renderer_report_fields["Dolby Vision frame metadata"] = (
                    "mapped to GPU Next; color validation pending"
                    if mapped_to_next
                    else "detected; RPU render mapping not verified"
                )
                if mapped_to_next:
                    self._renderer_report_fields["Dolby Vision capability"] = (
                        "RPU render path active for this frame; "
                        "color validation pending"
                    )
                self._renderer_report_fields[
                    "Dolby Vision base-layer compatibility ID"
                ] = "not exposed by mpv"
            elif is_content:
                self.dovi_profile_row.set_subtitle(_("No (Standard HDR10 / HLG)"))
                self.dovi_profile_row.set_visible(True)
                self._renderer_report_fields["Dolby Vision frame metadata"] = "none"
            else:
                self.dovi_profile_row.set_visible(False)
                self._renderer_report_fields["Dolby Vision frame metadata"] = "none"
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
            t_peak = target_peak
            peak_src = getattr(controller, "effective_peak_source", "auto") if controller else "auto"
            self.target_row.set_subtitle(f"TRC: {t_trc} | Prim: {t_prim} | Peak: {t_peak} ({peak_src})")
        except Exception:
            self.target_row.set_subtitle(_("Unknown"))
