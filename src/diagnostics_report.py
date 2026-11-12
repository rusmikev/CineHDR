# diagnostics_report.py
#
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure formatting helpers and single-source-of-truth diagnostics collector."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from gettext import gettext as _
import logging
import math
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .utils import mpv_prop

logger = logging.getLogger(__name__)

_MEDIA_PATH_FIELDS = {"media path", "media_path", "file", "filename"}

DIAGNOSTICS_EXPORT_PATH_ENV = "CINEHDR_DIAGNOSTICS_EXPORT_PATH"
DIAGNOSTICS_EXPORT_DIR_ENV = "CINEHDR_DIAGNOSTICS_EXPORT_DIR"
EXPECTED_FLATPAK_INSTANCE_ENV = "CINEHDR_EXPECTED_FLATPAK_INSTANCE"
EXPECTED_APP_COMMIT_ENV = "CINEHDR_EXPECTED_APP_COMMIT"
EXPECTED_RUNTIME_COMMIT_ENV = "CINEHDR_EXPECTED_RUNTIME_COMMIT"
DIAGNOSTICS_SESSION_ID_ENV = "CINEHDR_DIAGNOSTICS_SESSION_ID"


@dataclass
class DiagnosticSnapshot:
    """Unified snapshot holding report dictionaries, timestamp, and UI presentation data."""

    sample_time_utc: str
    renderer: dict[str, object]
    output: dict[str, object]
    video: dict[str, object]
    performance: dict[str, object]
    ui_subtitles: dict[str, str]
    ui_visibilities: dict[str, bool]

    def __iter__(self):
        # Allow unpacking as (renderer, output, video, performance)
        return iter((self.renderer, self.output, self.video, self.performance))


def _single_line(value: object) -> str:
    if value is None or value == "":
        return "unknown"
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def _positive_float(value: object) -> float | None:
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


def get_mpv_prop(mpv: Any, name: str, default: Any = None) -> Any:
    """Safely query an mpv property via utils.mpv_prop."""
    return mpv_prop(mpv, name, default)


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


def get_dovi_rpu_warning_state(win: Any, _mpv: Any = None) -> tuple[int | None, str | None]:
    """Retrieve window-tracked RPU warning state cleanly."""
    if win is None:
        return None, None
    count = getattr(win, "dovi_rpu_warning_count", None)
    text = getattr(win, "dovi_rpu_warning_text", None)
    return count, text


def _hdr_status_text(
    *,
    is_active: bool,
    supported: bool,
    mode: str,
    is_content: bool,
    source_peak_nits: int | None,
    target_peak: object,
) -> str:
    """Describe HDR transport separately from optional HDR-to-HDR mapping."""
    if is_active and supported:
        evidence = _("compositor submission unverified")
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


def read_flatpak_info(path: Path | None = None) -> dict[str, str]:
    """Read Flatpak sandbox instance metadata if running within Flatpak."""
    target = path or Path("/.flatpak-info")
    if not target.is_file():
        return {}
    import configparser
    cp = configparser.ConfigParser()
    try:
        cp.read(target)
        res = {}
        for key, sec, opt in [
            ("instance_id", "Instance", "instance-id"),
            ("app_commit", "Instance", "app-commit"),
            ("runtime_commit", "Instance", "runtime-commit"),
            ("app_name", "Application", "name"),
            ("arch", "Instance", "arch"),
            ("flatpak_version", "Instance", "flatpak-version"),
        ]:
            val = cp.get(sec, opt, fallback="").strip()
            if val:
                res[key] = val
        return res
    except Exception as exc:
        logger.warning("Failed to parse %s: %s", target, exc)
        return {}


def collect_performance_facts(
    mpv: Any = None,
    gl_area: Any = None,
    sample_time_utc: str | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    """Gather playback performance & drop facts and their UI presentation subtitles."""
    sample_time = sample_time_utc or datetime.now(timezone.utc).isoformat()

    vf_fps = _positive_float(get_mpv_prop(mpv, "estimated-vf-fps"))
    c_fps = _positive_float(get_mpv_prop(mpv, "container-fps"))
    d_fps = _positive_float(get_mpv_prop(mpv, "display-fps")) or _positive_float(
        get_mpv_prop(mpv, "estimated-display-fps")
    )

    fps_parts = []
    if vf_fps:
        fps_parts.append(f"{vf_fps:.2f} fps (filter estimate, not presentation)")
    elif c_fps:
        fps_parts.append(f"{c_fps:.2f} fps (container)")
    else:
        fps_parts.append(_("Unknown (frame-rate properties unavailable)"))

    extra_fps = []
    if c_fps and vf_fps:
        extra_fps.append(f"container: {c_fps:.2f} fps")
    if d_fps:
        extra_fps.append(f"display: {d_fps:.1f} Hz")
    fps_subtitle = f"{fps_parts[0]} ({' · '.join(extra_fps)})" if extra_fps else fps_parts[0]

    def _count_or_unknown(val: object) -> object:
        try:
            number = float(val)
            if not isinstance(val, bool) and math.isfinite(number) and number >= 0 and number.is_integer():
                return int(number)
        except (TypeError, ValueError, OverflowError):
            pass
        return _("unknown")

    vo_drops = _count_or_unknown(get_mpv_prop(mpv, "frame-drop-count"))
    dec_drops = _count_or_unknown(get_mpv_prop(mpv, "decoder-frame-drop-count"))
    fbo_pool = getattr(gl_area, "fbo_pool", None) if gl_area else None
    fbo_drops = _count_or_unknown(getattr(fbo_pool, "dropped_frames", None))
    fbo_alloc_failures = _count_or_unknown(getattr(fbo_pool, "allocation_failures", None))

    dropped_subtitle = (
        f"VO: {vo_drops} · Decoder: {dec_drops} · Pipeline (FBO): {fbo_drops} "
        f"(baseline: Unknown (not sampled); interval: Unknown; observed growth: Unknown)"
    )

    delayed = _count_or_unknown(get_mpv_prop(mpv, "vo-delayed-frame-count"))
    mistimed = _count_or_unknown(get_mpv_prop(mpv, "mistimed-frame-count"))
    delayed_subtitle = (
        f"Delayed: {delayed} · Mistimed: {mistimed} "
        f"(baseline: Unknown (not sampled); interval: Unknown; observed growth: Unknown)"
    )

    published = _count_or_unknown(getattr(gl_area, "render_frame_generation", None))
    pool_size = _count_or_unknown(getattr(fbo_pool, "size", None))
    rendered_subtitle = (
        f"{published} texture publications (FBO ring: {pool_size}; "
        f"baseline: Unknown (not sampled); interval: Unknown; presentations unmeasured)"
    )

    avsync = None
    try:
        raw_avsync = get_mpv_prop(mpv, "avsync")
        if raw_avsync is not None:
            avsync = float(raw_avsync)
    except (TypeError, ValueError, OverflowError):
        avsync = None
    if avsync is not None and not math.isfinite(avsync):
        avsync = None

    total_drift = None
    try:
        raw_drift = get_mpv_prop(mpv, "total-avsync-change")
        if raw_drift is not None:
            total_drift = float(raw_drift)
    except (TypeError, ValueError, OverflowError):
        total_drift = None
    if total_drift is not None and not math.isfinite(total_drift):
        total_drift = None

    if avsync is not None:
        avsync_ms = avsync * 1000.0
        drift_str = f"drift: {total_drift:+.3f} s" if total_drift is not None else "drift unknown"
        avsync_subtitle = f"{avsync_ms:+.1f} ms ({drift_str})"
    else:
        avsync_subtitle = _("Unknown (A/V sync property unavailable)")

    scale = _positive_float(getattr(gl_area, "_cached_scale", None)) if gl_area else None
    scale_text = f"{scale:.2f}x" if scale is not None else _("unknown")
    target_fmt = getattr(gl_area, "render_target_format", None) or _("unknown")
    target_depth_str = getattr(gl_area, "render_target_depth", None) or _("unknown")

    pipeline_status = getattr(gl_area, "render_session_status", "not-initialized") if gl_area else "unknown"
    failure_reason = getattr(gl_area, "render_failure_reason", None) if gl_area else None
    restart_reason = getattr(gl_area, "_render_restart_required_reason", None) if gl_area else None

    if failure_reason or restart_reason:
        pipeline_subtitle = f"Failed ({failure_reason or restart_reason})"
    elif isinstance(fbo_alloc_failures, int) and fbo_alloc_failures > 0:
        pipeline_subtitle = f"Warning: {fbo_alloc_failures} FBO allocation failures"
    elif pipeline_status == "active":
        max_w = getattr(gl_area, "_cached_max_width", 0) if gl_area else 0
        max_h = getattr(gl_area, "_cached_max_height", 0) if gl_area else 0
        limit_str = f" · max {max_w}x{max_h}" if max_w > 0 else ""
        pipeline_subtitle = f"Active · {target_fmt} ({target_depth_str}-bit) · Scale {scale_text}{limit_str}"
    elif pipeline_status == "startup-fallback":
        pipeline_subtitle = f"Fallback active · {target_fmt} ({target_depth_str}-bit) · Scale {scale_text}"
    else:
        pipeline_subtitle = pipeline_status

    performance: dict[str, object] = {
        "Sample time (UTC)": sample_time,
        "Process ID": os.getpid(),
        "Filter FPS estimate (estimated-vf-fps)": f"{vf_fps:.3f}" if vf_fps is not None else "unknown",
        "Container FPS": f"{c_fps:.3f}" if c_fps is not None else "unknown",
        "Display refresh estimate (Hz)": f"{d_fps:.3f}" if d_fps is not None else "unknown",
        "Measured presentation FPS": "unavailable (no presentation timing capture)",
        "Counter scope": "cumulative; measurement interval/reset identity unknown; not a drop rate",
        "Counter baseline": "Unknown (not sampled)",
        "Counter measurement interval": "Unknown (point-in-time sample)",
        "Counter observed growth": "Unknown (single sample; delta unmeasured)",
        "VO frame drops": vo_drops,
        "Decoder frame drops": dec_drops,
        "Pipeline (FBO) drops": fbo_drops,
        "FBO allocation failures": fbo_alloc_failures,
        "VO delayed frames": delayed,
        "Mistimed frames": mistimed,
        "Published textures (includes redraws)": published,
        "Presented frames": "unavailable (texture publication is not presentation)",
        "A/V sync offset": avsync_subtitle,
        "Total A/V sync change": f"{total_drift:+.3f} s" if total_drift is not None else "unknown",
        "Pipeline status": pipeline_subtitle,
        "FBO render target (not Wayland surface)": f"{target_fmt} ({target_depth_str}-bit)",
        "Effective scale": scale_text,
    }

    ui_subtitles = {
        "perf_fps_row": fps_subtitle,
        "perf_dropped_row": dropped_subtitle,
        "perf_delayed_row": delayed_subtitle,
        "perf_rendered_row": rendered_subtitle,
        "perf_avsync_row": avsync_subtitle,
        "perf_pipeline_row": pipeline_subtitle,
    }

    return performance, ui_subtitles


def collect_video_output_facts(
    win: Any,
    gl_area: Any = None,
    mpv: Any = None,
    sample_time_utc: str | None = None,
) -> DiagnosticSnapshot:
    """Gather diagnostic facts across renderer, output, video, and performance.

    Single source of truth used identically by the UI dialog, Copy Report,
    and automated file exporter. Never opens dialogs or creates UI widgets.
    """
    sample_time = sample_time_utc or datetime.now(timezone.utc).isoformat()

    if gl_area is None and win is not None:
        gl_area = getattr(win, "_video_area", None) or getattr(win, "gl_area", None)

    if mpv is None and win is not None:
        mpv = getattr(win, "mpv", None)
        if mpv is None and hasattr(win, "player"):
            mpv = getattr(win.player, "mpv", None)

    controller = getattr(gl_area, "hdr_controller", None) if gl_area else None
    params = (
        get_mpv_prop(mpv, "video-params")
        or get_mpv_prop(mpv, "video-out-params")
        or {}
    ) if mpv else {}
    if not isinstance(params, dict):
        params = {}

    # 1. Video Renderer
    try:
        from .preferences import settings
        configured = settings.get_string("render-backend")
    except Exception:
        configured = "unknown"

    selection = getattr(gl_area, "render_backend_selection", None) if gl_area else None
    startup_configured = getattr(getattr(selection, "configured", None), "value", "unknown") if selection else "unknown"
    requested = getattr(gl_area, "render_backend_requested", None) if gl_area else None
    requested_value = getattr(requested, "value", requested) or "unknown"
    source = getattr(gl_area, "render_backend_source", None) if gl_area else None
    source_value = getattr(source, "value", source) or "unknown"
    active = getattr(gl_area, "render_backend_active", None) if gl_area else None
    fallback_reason = getattr(gl_area, "render_backend_fallback_reason", None) if gl_area else None
    restart_reason = getattr(gl_area, "_render_restart_required_reason", None) if gl_area else None
    session_status = getattr(gl_area, "render_session_status", "not-initialized") if gl_area else "not-initialized"
    failure_reason = getattr(gl_area, "render_failure_reason", None) if gl_area else None
    runtime = (getattr(gl_area, "render_runtime", {}) or {}) if gl_area else {}
    target_format = getattr(gl_area, "render_target_format", None) if gl_area else None
    target_depth = getattr(gl_area, "render_target_depth", None) if gl_area else None

    try:
        from .render_backend import renderer_preference_requires_restart
        preference_restart = bool(selection and renderer_preference_requires_restart(selection, configured))
    except Exception:
        preference_restart = False

    try:
        from .render_backend import RenderBackend
        requested_is_next = requested in (RenderBackend.GPU_NEXT, RenderBackend.AUTO)
    except Exception:
        requested_is_next = str(requested_value) in ("gpu-next", "auto")

    if active == "opengl-next" and session_status == "active":
        gpu_next_status = _("Available and active (experimental API)")
    elif active == "opengl-next":
        gpu_next_status = _("API initialized, but the renderer is stopped")
    elif fallback_reason:
        gpu_next_status = _("Unavailable in this session; standard renderer active")
    elif requested_is_next and session_status == "initialization-failure":
        gpu_next_status = _("Requested, but initialization failed")
    elif requested_is_next and session_status in ("runtime-failure", "lifecycle-failure"):
        gpu_next_status = _("Requested, but the renderer is stopped")
    elif requested_is_next:
        gpu_next_status = _("Requested; waiting for initialization")
    else:
        gpu_next_status = _("Not requested in this session")

    try:
        from .render_backend import dolby_vision_render_capability
        dovi_capability = dolby_vision_render_capability(active, session_status, runtime)
    except Exception:
        dovi_capability = SimpleNamespace(rpu_path_available=False, reason="unavailable")

    if active == "opengl" and session_status not in ("runtime-failure", "lifecycle-failure"):
        dovi_status = _("Unsupported by the active standard renderer")
    elif getattr(dovi_capability, "rpu_path_available", False):
        dovi_status = _("RPU render path available; color validation pending")
    elif active == "opengl-next" and session_status == "active":
        dovi_status = _("Experimental renderer active; dependency stack unvalidated")
    else:
        dovi_status = _("Unavailable; renderer capability unknown")

    runtime_configuration = runtime.get("mpv_configuration")
    if not runtime_configuration or runtime_configuration == "unknown":
        runtime_configuration = get_mpv_prop(mpv, "mpv-configuration", "unknown")

    renderer: dict[str, object] = {
        "Selected for next launch": configured,
        "Configured at process start": startup_configured,
        "Requested this session": requested_value,
        "Request source": source_value,
        "Creation fallback allowed": getattr(selection, "allow_creation_fallback", False) if selection else False,
        "Active API": active or "not-initialized",
        "Session status": session_status,
        "Preference restart pending": preference_restart,
        "Startup fallback reason": fallback_reason or "none",
        "Renderer failure reason": failure_reason or "none",
        "Lifecycle restart reason": restart_reason or "none",
        "GPU Next capability": gpu_next_status,
        "Dolby Vision capability": dovi_status,
        "Dolby Vision RPU path evidence": getattr(dovi_capability, "reason", "unavailable"),
        "Dolby Vision color validation": (
            "pending" if getattr(dovi_capability, "rpu_path_available", False) else "unavailable"
        ),
        "Dolby Vision reference A/B": (
            "rendered-pixel effect observed with software decode; "
            "hardware and color validation pending"
            if getattr(dovi_capability, "rpu_path_available", False)
            else "unavailable"
        ),
        "Render target format": target_format or "not-rendered",
        "Render target depth": target_depth or "not-rendered",
        "OpenGL device": runtime.get("gl_renderer") or "unknown",
        "OpenGL vendor": runtime.get("gl_vendor") or "unknown",
        "OpenGL renderer": runtime.get("gl_renderer") or "unknown",
        "OpenGL version": runtime.get("gl_version") or "unknown",
        "GLSL version": runtime.get("glsl_version") or "unknown",
        "libmpv path": runtime.get("libmpv_path", "unknown"),
        "mpv version": runtime.get("mpv_version", "unknown"),
        "libplacebo version": runtime.get("libplacebo_version", "unknown"),
        "FFmpeg version": runtime.get("ffmpeg_version", "unknown"),
        "mpv configuration": runtime_configuration,
    }

    # UI presentation strings for Renderer section
    configured_label = _("GPU Next · Experimental") if configured == "gpu-next" else _("Standard · OpenGL")
    requested_label = _("{renderer} (source: {source})").format(renderer=requested_value, source=source_value)
    active_label = {
        "opengl": _("Standard · OpenGL"),
        "opengl-next": _("GPU Next · Experimental"),
    }.get(active, _("Not initialized"))
    if session_status in ("initialization-failure", "runtime-failure", "lifecycle-failure"):
        active_label = _("{renderer} — stopped; restart required").format(renderer=active_label)
    gl_device = runtime.get("gl_renderer")
    if gl_device:
        active_label = f"{active_label} ({gl_device})"

    try:
        from .render_backend import RenderSelectionSource
        is_env_source = selection and getattr(selection, "source", None) is RenderSelectionSource.ENVIRONMENT
    except Exception:
        is_env_source = False

    if failure_reason or restart_reason:
        visible_status = _("Stopped · Restart required")
    elif fallback_reason:
        visible_status = _("Fallback to Standard")
    elif preference_restart:
        visible_status = _("Restart required")
    elif is_env_source:
        visible_status = _("Environment override active")
    else:
        visible_status = _("Running normally")

    dependency_summary = _("Not initialized")
    if runtime:
        dependency_summary = (
            f"{runtime.get('mpv_version', 'unknown')} · "
            f"libplacebo {runtime.get('libplacebo_version', 'unknown')} · "
            f"FFmpeg {runtime.get('ffmpeg_version', 'unknown')} · "
            f"{runtime.get('libmpv_path', 'unknown')}"
        )

    if target_format == "GL_RGBA16F" and target_depth == 16:
        target_summary = _("RGBA16F · 16-bit float")
    elif target_format == "GL_RGBA8" and target_depth == 8:
        target_summary = _("RGBA8 · 8-bit")
    elif target_format and target_depth:
        target_summary = f"{str(target_format).removeprefix('GL_')} · {target_depth}-bit"
    else:
        target_summary = _("Waiting for first frame")

    # 2. Output & Color State
    is_active = getattr(controller, "is_hdr_active", False) if controller else False
    is_content = getattr(controller, "is_hdr_content", False) if controller else False
    mode = getattr(controller, "hdr_mode", "auto") if controller else "auto"
    try:
        import sys
        hdr_diag = sys.modules.get("src.hdr_diagnostics")
        check_fn = getattr(hdr_diag, "check_hdr_support", None)
        if check_fn is None:
            from .hdr_detection import check_hdr_support as check_fn
        supported = check_fn()
    except Exception:
        supported = False

    target_peak = get_mpv_prop(mpv, "target-peak", "auto")
    hdr_status_str = _hdr_status_text(
        is_active=is_active,
        supported=supported,
        mode=mode,
        is_content=is_content,
        source_peak_nits=_source_peak_nits(params),
        target_peak=target_peak,
    )

    if supported:
        display_hdr_str = _("Yes (Wayland + GTK 4.16+) — final output depends on the compositor/monitor HDR mode")
        unsupported_reason_str = None
    else:
        display_hdr_str = _("No (Fallback to SDR / 8-bit)")
        try:
            import sys
            hdr_diag = sys.modules.get("src.hdr_diagnostics")
            reason_fn = getattr(hdr_diag, "get_hdr_unsupported_reason", None)
            if reason_fn is None:
                from .hdr_detection import get_hdr_unsupported_reason as reason_fn
            unsupported_reason_str = reason_fn()
        except Exception:
            unsupported_reason_str = "HDR not supported"

    try:
        import sys
        hdr_diag = sys.modules.get("src.hdr_diagnostics")
        cm_fn = getattr(hdr_diag, "get_compositor_cm_support", None)
        if cm_fn is None:
            from .hdr_detection import get_compositor_cm_support as cm_fn
        cm = cm_fn()
    except Exception:
        cm = None

    if cm is True:
        compositor_cm_str = _("Yes (wp_color_manager_v1 advertised)")
    elif cm is False:
        compositor_cm_str = _("No — compositor lacks wp_color_manager_v1, HDR pass-through impossible")
    else:
        compositor_cm_str = _("Unknown (probe unavailable)")

    states = None
    try:
        from . import wayland_output_hdr
        states = wayland_output_hdr.get_output_hdr_states()
    except Exception:
        states = None

    hint = getattr(controller, "output_hint", None) if controller else None
    if states is None:
        monitor_hdr_str = _("Unknown (probe unavailable)")
    elif hint and hint in states:
        info = states[hint]
        if getattr(info, "hdr", False):
            maximum = _positive_float(getattr(info, "max_lum", None))
            peak = (
                f", reported color-volume max ~{maximum:g} nits; panel luminance not measured"
                if maximum is not None else ", reported luminance unknown; panel luminance not measured"
            )
            for label, attribute in (("target max", "target_max_lum"), ("target MaxCLL", "target_max_cll")):
                value = _positive_float(getattr(info, attribute, None))
                if value is not None:
                    peak += f"; reported {label} {value:g} nits"
            monitor_hdr_str = _("Compositor reports HDR on {c} ({tf}{peak})").format(c=hint, tf=info.tf_name, peak=peak)
        else:
            monitor_hdr_str = _("SDR on {c} — enable HDR in display settings for pass-through").format(c=hint)
    else:
        hdr_outputs = [c for c, i in states.items() if getattr(i, "hdr", False)] if states else []
        if hdr_outputs:
            monitor_hdr_str = _("Compositor reports HDR on: {list}; current output unknown").format(list=", ".join(sorted(hdr_outputs)))
        else:
            monitor_hdr_str = _("SDR on all outputs — enable HDR in display settings for pass-through")

    # Graphics Offload evaluation: Gtk.GraphicsOffloadEnabled.ENABLED is 0, DISABLED is 1
    offload_obj = getattr(win, "offload", None) if win else None
    try:
        enabled = offload_obj.get_enabled() if offload_obj else None
    except Exception:
        enabled = None

    try:
        from gi.repository import Gtk
        has_offload_enum = hasattr(Gtk, "GraphicsOffloadEnabled")
        offload_disabled = Gtk.GraphicsOffloadEnabled.DISABLED if has_offload_enum else None
        offload_enabled = Gtk.GraphicsOffloadEnabled.ENABLED if has_offload_enum else None
    except Exception:
        offload_disabled = None
        offload_enabled = None

    if enabled is None:
        offload_str = _("Unknown")
    elif (offload_disabled is not None and enabled == offload_disabled) or str(enabled).endswith("DISABLED"):
        offload_str = _("Disabled — output goes through GTK compositing")
    elif (offload_enabled is not None and enabled == offload_enabled) or str(enabled).endswith("ENABLED"):
        offload_str = _("Enabled (subsurface / direct scanout possible)")
    else:
        offload_str = _("Unknown")

    color_state = getattr(gl_area, "render_color_state", None) if gl_area else None
    color_state_str = {
        "rec2100-pq": "Rec.2100 PQ (published texture)",
        "rec2100-linear": "Rec.2100 Linear (published texture)",
        "srgb": "sRGB (published texture)",
    }.get(color_state, _("Unknown / no published color state"))

    fbo_format_str = (
        f"{target_format} ({target_depth}-bit render target; not the Wayland surface format)"
        if target_format and target_depth else _("Unknown / target not rendered")
    )

    try:
        from .hdr_detection import hdr_output_allowed
        auto_allowed, auto_reason = hdr_output_allowed(hint, allow_probe=False)
        auto_gate_str = "allowed" if auto_allowed else f"blocked: {auto_reason}"
    except Exception as e:
        auto_gate_str = f"blocked: error ({e})"

    output: dict[str, object] = {
        "HDR Status": hdr_status_str,
        "Auto gate": auto_gate_str,
        "Display HDR Supported": display_hdr_str,
        "Compositor Color Management": compositor_cm_str,
        "Monitor HDR State": monitor_hdr_str,
        "Graphics Offload": offload_str,
    }
    if unsupported_reason_str:
        output["System HDR Limitation"] = unsupported_reason_str
    output["Gdk.ColorState"] = color_state_str
    output["FBO Format"] = fbo_format_str
    gap_reason = getattr(controller, "state_gap_reason", None) if controller else None
    if gap_reason:
        output["Publication State Gap"] = (
            f"Publication suppressed: {gap_reason}"
            if getattr(controller, "is_publication_suppressed", False)
            else gap_reason
        )

    # 3. Video Signal
    codec = get_mpv_prop(mpv, "video-format") or get_mpv_prop(mpv, "video-codec")
    codec_str = str(codec) if codec else _("No video loaded")

    w = params.get("w") or get_mpv_prop(mpv, "video-params/w") or get_mpv_prop(mpv, "width")
    h = params.get("h") or get_mpv_prop(mpv, "video-params/h") or get_mpv_prop(mpv, "height")
    pix = params.get("pixelformat") or get_mpv_prop(mpv, "video-params/pixelformat")
    if w and h:
        res_str = f"{w}x{h}"
        if pix:
            res_str += f" ({pix})"
    else:
        res_str = _("Unknown")

    hw_current = get_mpv_prop(mpv, "hwdec-current")
    hw_config = get_mpv_prop(mpv, "hwdec")
    hw_config_str = str(hw_config) if hw_config else "unknown"
    hw_current_str = str(hw_current) if hw_current else "unknown"

    if hw_current and str(hw_current).lower() not in ("no", "none", ""):
        hwdec_str = f"{hw_current} ({_('GPU Acceleration active')}; configured: {hw_config_str})"
    elif hw_current and str(hw_current).lower() in ("no", "none"):
        hwdec_str = f"{_('Software / CPU Decoding')} (active: no; configured: {hw_config_str})"
    elif hw_current is None or str(hw_current).strip() == "":
        if hw_config and str(hw_config).lower() not in ("no", "none", ""):
            hwdec_str = f"{_('Unknown (configured: ')}{hw_config_str})"
        else:
            hwdec_str = _("Unknown")
    else:
        hwdec_str = f"{_('Software / CPU Decoding')} (active: {hw_current}; configured: {hw_config_str})"

    dovi_profile_str = None
    try:
        from .hdr_detection import get_dovi_info
        dovi = get_dovi_info(params, mpv)
        if dovi:
            profile = dovi.get("profile")
            level = dovi.get("level")
            mapped_to_next = (
                getattr(dovi_capability, "rpu_path_available", False)
                and "dolbyvision" in str(params.get("colormatrix", "")).lower()
            )
            if profile is None:
                if mapped_to_next:
                    desc = _("Detected — RPU metadata mapped to GPU Next (profile unknown; validation pending)")
                elif active == "opengl-next":
                    desc = _("Detected — profile unknown; RPU mapping is not verified for this build")
                else:
                    desc = _("Detected — profile unknown (RPU not processed)")
            elif dovi.get("unsupported"):
                if mapped_to_next:
                    desc = _("Profile {p} — RPU path observed, but blocked pending color validation; forced to SDR").format(p=profile)
                elif active == "opengl-next" and session_status == "active":
                    desc = _("Profile {p} — blocked pending GPU Next validation; forced to SDR").format(p=profile)
                else:
                    desc = _("Profile {p} — unsupported by the current renderer; forced to SDR").format(p=profile)
            elif mapped_to_next and profile == 7:
                desc = _("Profile 7 — RPU metadata mapped to GPU Next; enhancement layer unsupported; validation pending")
            elif mapped_to_next:
                desc = _("Profile {p} — RPU metadata mapped to GPU Next (validation pending)").format(p=profile)
            elif profile == 7:
                desc = _("Profile 7 — base layer only (RPU / enhancement layer not processed)")
            elif profile == 8:
                desc = _("Profile 8 — base-layer fallback (RPU not processed; compatibility ID unknown)")
            else:
                desc = _("Profile {p} (RPU not processed)").format(p=profile)
            if level:
                desc = f"{desc} · Level {level}"
            dovi_profile_str = desc
            renderer["Dolby Vision frame metadata"] = (
                "mapped to GPU Next; color validation pending"
                if mapped_to_next
                else "detected; RPU render mapping not verified"
            )
            if mapped_to_next:
                renderer["Dolby Vision capability"] = "RPU render path active for this frame; color validation pending"
            renderer["Dolby Vision base-layer compatibility ID"] = "not exposed by mpv"
        elif is_content:
            dovi_profile_str = _("No (Standard HDR10 / HLG)")
            renderer["Dolby Vision frame metadata"] = "none"
        else:
            renderer["Dolby Vision frame metadata"] = "none"
    except Exception:
        dovi_profile_str = None

    rpu_count, rpu_text = get_dovi_rpu_warning_state(win, mpv)
    rpu_fact = format_dovi_rpu_warnings(rpu_count, rpu_text)

    prim = params.get("primaries") or get_mpv_prop(mpv, "video-params/primaries") or _("Unknown")
    gamma = params.get("gamma") or get_mpv_prop(mpv, "video-params/gamma") or _("Unknown")
    sig_peak = params.get("sig-peak") or get_mpv_prop(mpv, "video-params/sig-peak") or 0.0
    try:
        if sig_peak and float(sig_peak) > 0 and float(sig_peak) != 1.0:
            nits = int(float(sig_peak) * 203)
            peak_luma_str = f"{float(sig_peak):.2f} (~{nits} nits)"
        else:
            peak_luma_str = _("Standard / SDR (1.00)")
    except Exception:
        peak_luma_str = _("Unknown")

    t_trc = get_mpv_prop(mpv, "target-trc", "auto")
    t_prim = get_mpv_prop(mpv, "target-prim", "auto")
    t_peak = target_peak
    peak_src = getattr(controller, "effective_peak_source", "auto") if controller else "auto"
    target_str = f"TRC: {t_trc} | Prim: {t_prim} | Peak: {t_peak} ({peak_src})"

    video: dict[str, object] = {
        "Video Codec / Format": codec_str,
        "Resolution & Pixel Format": res_str,
        "Hardware Acceleration": hwdec_str,
    }
    if dovi_profile_str:
        video["Dolby Vision Profile"] = dovi_profile_str
    video["Dolby Vision RPU Warnings"] = rpu_fact
    video["Color Primaries"] = str(prim)
    video["Transfer Characteristics"] = str(gamma)
    video["Peak Luminance"] = peak_luma_str
    video["Target Tone Mapping"] = target_str
    video["Configured decoder"] = hw_config_str
    video["Active decoder"] = hw_current_str
    video["Hardware decoding device"] = "Unknown (not reported by libmpv)"

    # 4. Performance & Drops
    performance, perf_ui_subtitles = collect_performance_facts(mpv, gl_area, sample_time_utc=sample_time)

    # 5. UI presentation subtitles and visibilities for HdrDiagnosticsDialog
    ui_subtitles: dict[str, str] = {
        "renderer_configured_row": configured_label,
        "renderer_requested_row": requested_label,
        "renderer_active_row": active_label,
        "renderer_reason_row": visible_status,
        "renderer_dependency_row": dependency_summary,
        "renderer_target_row": target_summary,
        "gpu_next_capability_row": gpu_next_status,
        "dovi_capability_row": dovi_status,
        "status_row": hdr_status_str,
        "auto_gate_row": auto_gate_str,
        "display_hdr_row": display_hdr_str,
        "compositor_cm_row": compositor_cm_str,
        "monitor_hdr_row": monitor_hdr_str,
        "offload_row": offload_str,
        "unsupported_reason_row": unsupported_reason_str or "",
        "color_state_row": color_state_str,
        "texture_format_row": fbo_format_str,
        "codec_row": codec_str,
        "resolution_row": res_str,
        "hwdec_row": hwdec_str,
        "dovi_profile_row": dovi_profile_str or "",
        "dovi_rpu_row": rpu_fact,
        "primaries_row": str(prim),
        "trc_row": str(gamma),
        "peak_luma_row": peak_luma_str,
        "target_row": target_str,
        **perf_ui_subtitles,
    }

    ui_visibilities: dict[str, bool] = {
        "unsupported_reason_row": bool(unsupported_reason_str),
        "dovi_profile_row": bool(dovi_profile_str),
        "dovi_rpu_row": bool((isinstance(rpu_count, (int, float)) and rpu_count > 0) or dovi_profile_str or is_content),
    }

    return DiagnosticSnapshot(
        sample_time_utc=sample_time,
        renderer=renderer,
        output=output,
        video=video,
        performance=performance,
        ui_subtitles=ui_subtitles,
        ui_visibilities=ui_visibilities,
    )


def build_video_output_report(
    renderer: Mapping[str, object],
    output: Mapping[str, object],
    video: Mapping[str, object],
    performance: Mapping[str, object] | None = None,
    session_identity: Mapping[str, object] | None = None,
) -> str:
    """Build a stable plain-text report without exposing the media path."""
    lines = ["CineHDR Video Output Diagnostics"]
    sections: list[tuple[str, Mapping[str, object]]] = []
    if session_identity:
        sections.append(("Diagnostics Evidence & Session Identity", session_identity))
    sections.extend([
        ("Video Renderer", renderer),
        ("Output & Color State", output),
        ("Video Signal", video),
    ])
    if performance:
        sections.append(("Playback Performance & Drops", performance))
    for title, fields in sections:
        lines.extend(("", f"[{title}]"))
        for name, value in fields.items():
            if name.strip().lower() in _MEDIA_PATH_FIELDS:
                continue
            lines.append(f"{name}: {_single_line(value)}")
    return "\n".join(lines) + "\n"


def generate_video_output_report(
    win: Any,
    gl_area: Any = None,
    mpv: Any = None,
    session_identity: Mapping[str, object] | None = None,
    sample_time_utc: str | None = None,
) -> str:
    """Convenience helper: collect facts and format the complete text report."""
    snapshot = collect_video_output_facts(win, gl_area, mpv, sample_time_utc=sample_time_utc)
    return build_video_output_report(
        snapshot.renderer,
        snapshot.output,
        snapshot.video,
        performance=snapshot.performance,
        session_identity=session_identity,
    )
