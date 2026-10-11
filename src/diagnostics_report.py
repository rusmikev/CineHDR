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
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    """Safely query an mpv property via method, item, or attribute access."""
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


def get_dovi_rpu_warning_state(win: Any, mpv: Any = None) -> tuple[int | None, str | None]:
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
        try:
            from .hdr_detection import is_surface_submission_proven
            override = is_surface_submission_proven()
        except Exception:
            override = False
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

    output: dict[str, object] = {
        "HDR Status": hdr_status_str,
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


class DiagnosticsExporter:
    """Autonomous one-shot diagnostic snapshot exporter for Gate 2W validation."""

    def __init__(
        self,
        export_path: Path,
        export_dir: Path | None = None,
        expected_instance: str | None = None,
        expected_app_commit: str | None = None,
        expected_runtime_commit: str | None = None,
        session_id: str | None = None,
    ):
        self.export_path = export_path
        self.export_dir = export_dir or export_path.parent
        self.expected_instance = expected_instance
        self.expected_app_commit = expected_app_commit
        self.expected_runtime_commit = expected_runtime_commit
        self.session_id = session_id

        self.first_frame_confirmed = False
        self.confirmed_media_frame: Any = None
        self.export_completed = False
        self.export_status = "PENDING"
        self.last_write_error: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DiagnosticsExporter | None:
        env = os.environ if environ is None else environ
        raw_path = env.get(DIAGNOSTICS_EXPORT_PATH_ENV, "").strip()
        raw_dir = env.get(DIAGNOSTICS_EXPORT_DIR_ENV, "").strip()
        if not raw_path and not raw_dir:
            return None

        export_path = Path(raw_path).expanduser() if raw_path else None
        export_dir = Path(raw_dir).expanduser() if raw_dir else None

        if export_dir and not export_path:
            export_path = export_dir / "video_output_diagnostics.txt"
        elif export_path and not export_dir:
            export_dir = export_path.parent

        if not export_path or not export_dir:
            return None

        expected_instance = env.get(EXPECTED_FLATPAK_INSTANCE_ENV, "").strip() or None
        expected_app = env.get(EXPECTED_APP_COMMIT_ENV, "").strip() or None
        expected_rt = env.get(EXPECTED_RUNTIME_COMMIT_ENV, "").strip() or None
        session_id = env.get(DIAGNOSTICS_SESSION_ID_ENV, "").strip() or None

        return cls(
            export_path=export_path,
            export_dir=export_dir,
            expected_instance=expected_instance,
            expected_app_commit=expected_app,
            expected_runtime_commit=expected_rt,
            session_id=session_id,
        )

    def check_identity(
        self, flatpak_info: dict[str, str] | None = None
    ) -> tuple[bool, str, list[str], dict[str, Any]]:
        info = flatpak_info if flatpak_info is not None else read_flatpak_info()
        actual_instance = info.get("instance_id") or None
        actual_app_commit = info.get("app_commit") or None
        actual_runtime_commit = info.get("runtime_commit") or None

        reasons: list[str] = []

        # Real container evidence requires instance-id and commits present in /.flatpak-info
        is_container = bool(actual_instance and actual_app_commit and actual_runtime_commit)
        if not is_container:
            missing_parts = []
            if not actual_instance:
                missing_parts.append("instance-id")
            if not actual_app_commit:
                missing_parts.append("app-commit")
            if not actual_runtime_commit:
                missing_parts.append("runtime-commit")
            reasons.append(
                f"container_metadata_missing: running outside Flatpak container or /.flatpak-info incomplete (missing: {', '.join(missing_parts)})"
            )

        mismatches: list[str] = []
        if self.expected_instance and actual_instance != self.expected_instance:
            mismatches.append(
                f"instance mismatch: expected '{self.expected_instance}', actual '{actual_instance}'"
            )
        if self.expected_app_commit and actual_app_commit != self.expected_app_commit:
            mismatches.append(
                f"app-commit mismatch: expected '{self.expected_app_commit}', actual '{actual_app_commit}'"
            )
        if self.expected_runtime_commit and actual_runtime_commit != self.expected_runtime_commit:
            mismatches.append(
                f"runtime-commit mismatch: expected '{self.expected_runtime_commit}', actual '{actual_runtime_commit}'"
            )

        if mismatches:
            reasons.extend(mismatches)

        has_expected_app = bool(self.expected_app_commit)
        has_expected_runtime = bool(self.expected_runtime_commit)
        has_expected_instance = bool(self.expected_instance)

        if not is_container:
            identity_status = "CONTAINER_METADATA_MISSING"
            verified = False
        elif mismatches:
            identity_status = "IDENTITY_MISMATCH"
            verified = False
        elif not has_expected_app and not has_expected_runtime and not has_expected_instance:
            identity_status = "UNVERIFIED"
            verified = False
            reasons.append(
                "no_expected_identity_configured: container metadata present but no expected identity commits configured for verification"
            )
        elif not (has_expected_app and has_expected_runtime):
            # Partial verification: both expected app and runtime commits must be configured for complete build verification
            identity_status = "UNVERIFIED"
            verified = False
            missing_expected = []
            if not has_expected_app:
                missing_expected.append("expected_app_commit")
            if not has_expected_runtime:
                missing_expected.append("expected_runtime_commit")
            reasons.append(
                f"partial_identity_check: complete build verification requires both expected app and runtime commits (missing: {', '.join(missing_expected)})"
            )
        else:
            identity_status = "VERIFIED"
            verified = True

        identity_record = {
            "process_id": os.getpid(),
            "flatpak_instance_id": actual_instance,
            "app_commit": actual_app_commit,
            "runtime_commit": actual_runtime_commit,
            "expected_instance_id": self.expected_instance,
            "expected_app_commit": self.expected_app_commit,
            "expected_runtime_commit": self.expected_runtime_commit,
            "session_id": self.session_id,
            "identity_status": identity_status,
            "identity_verified": verified,
            "identity_reasons": reasons,
        }
        return verified, identity_status, reasons, identity_record

    def export_snapshot(
        self,
        win: Any,
        trigger: str = "first-media-frame-confirmed",
        incomplete_reasons: list[str] | None = None,
        media_frame: Any = None,
        flatpak_info: dict[str, str] | None = None,
        snapshot: DiagnosticSnapshot | None = None,
        context_generation: int | None = None,
        load_generation: int | None = None,
    ) -> tuple[bool, str | None]:
        """Perform atomic write of diagnostics text report and companion JSON manifest."""
        if self.export_completed:
            return True, None

        initial_shutting_down = (getattr(win, "_shutting_down", False) is True) if win is not None else False
        initial_video_area = (getattr(win, "_video_area", None) or getattr(win, "gl_area", None)) if win is not None else None
        raw_ctx = getattr(initial_video_area, "render_context_generation", None) if initial_video_area else None
        expected_ctx = context_generation if isinstance(context_generation, int) else (
            raw_ctx if isinstance(raw_ctx, int) else None
        )
        raw_load = getattr(win, "_media_load_generation", None) if win is not None else None
        expected_load = load_generation if isinstance(load_generation, int) else (
            raw_load if isinstance(raw_load, int) else None
        )

        if snapshot is None and win is not None:
            snapshot = collect_video_output_facts(win)

        id_verified, id_status, id_reasons, id_record = self.check_identity(flatpak_info=flatpak_info)

        failure_reasons: list[str] = list(incomplete_reasons or [])
        if not id_verified:
            failure_reasons.extend(id_reasons)

        generation_changed = False
        if win is not None:
            latest_shutting_down = getattr(win, "_shutting_down", False) is True
            latest_video_area = getattr(win, "_video_area", None) or getattr(win, "gl_area", None)
            raw_latest_ctx = getattr(latest_video_area, "render_context_generation", None) if latest_video_area else None
            latest_ctx = raw_latest_ctx if isinstance(raw_latest_ctx, int) else None
            raw_latest_load = getattr(win, "_media_load_generation", None)
            latest_load = raw_latest_load if isinstance(raw_latest_load, int) else None

            if not initial_shutting_down and latest_shutting_down:
                failure_reasons.append("shutdown_during_collection: window started shutting down during diagnostics collection")
                generation_changed = True
            elif initial_shutting_down and trigger != "shutdown-before-first-frame":
                failure_reasons.append("window_shutting_down: window was shutting down when export was triggered")
                generation_changed = True

            if expected_load is not None and latest_load is not None and latest_load != expected_load:
                failure_reasons.append(
                    f"generation_mismatch_during_collection: media load generation changed from {expected_load} to {latest_load}"
                )
                generation_changed = True

            if expected_ctx is not None and latest_ctx is not None and latest_ctx != expected_ctx:
                failure_reasons.append(
                    f"context_mismatch_during_collection: render context generation changed from {expected_ctx} to {latest_ctx}"
                )
                generation_changed = True

            if initial_video_area is not None and latest_video_area is not None and latest_video_area is not initial_video_area:
                failure_reasons.append("video_area_changed_during_collection: video area widget instance changed")
                generation_changed = True

        if generation_changed:
            self.first_frame_confirmed = False
            self.confirmed_media_frame = None

        if not self.first_frame_confirmed:
            if not any("first_frame_unconfirmed" in r for r in failure_reasons):
                failure_reasons.append("first_frame_unconfirmed: no video frame was published before shutdown")

        if media_frame is None and self.first_frame_confirmed:
            failure_reasons.append("media_frame_unconfirmed: published texture lacked valid media frame evidence")
        elif media_frame is not None:
            if getattr(media_frame, "source_width", 0) <= 0 or getattr(media_frame, "source_height", 0) <= 0:
                failure_reasons.append("media_frame_invalid: non-positive video frame dimensions")

        if snapshot is None:
            failure_reasons.append("snapshot_missing: video output diagnostics snapshot could not be collected")
        else:
            active_api = snapshot.renderer.get("Active API")
            if active_api != "opengl-next":
                failure_reasons.append(f"backend_fallback: active API is '{active_api}', expected 'opengl-next'")

            session_status = snapshot.renderer.get("Session status")
            if session_status != "active":
                failure_reasons.append(f"session_not_active: renderer session status is '{session_status}', expected 'active'")

            startup_fallback = snapshot.renderer.get("Startup fallback reason")
            if startup_fallback and str(startup_fallback).lower() not in ("none", "unknown"):
                failure_reasons.append(f"startup_fallback: {startup_fallback}")

            renderer_failure = snapshot.renderer.get("Renderer failure reason")
            if renderer_failure and str(renderer_failure).lower() not in ("none", "unknown"):
                failure_reasons.append(f"renderer_failure: {renderer_failure}")

            restart_required = snapshot.renderer.get("Render restart required")
            if restart_required and str(restart_required).lower() not in ("none", "unknown", "no"):
                failure_reasons.append(f"restart_required: {restart_required}")

            target_format = snapshot.renderer.get("Render target format")
            target_depth = snapshot.renderer.get("Render target depth")
            color_state = str(snapshot.output.get("Gdk.ColorState", ""))

            if target_format == "GL_RGBA8" or target_depth == 8:
                failure_reasons.append("target_format_insufficient: render target format is 8-bit GL_RGBA8, GL_RGBA16F required for HDR/PQ")
            elif target_format != "GL_RGBA16F" or target_depth != 16:
                failure_reasons.append(f"target_format_unverified: target format is '{target_format}' ({target_depth}-bit), expected GL_RGBA16F (16-bit)")

            color_state_lower = color_state.lower()
            if "pq" not in color_state_lower or "2100" not in color_state_lower:
                failure_reasons.append(f"color_state_unverified: published color state is '{color_state}', expected Rec.2100 PQ")

            if snapshot.video.get("Video Codec / Format") in ("No video loaded", "Unknown", None):
                failure_reasons.append("video_stream_unconfirmed: no video stream loaded in snapshot")

        if len(failure_reasons) == 0:
            evidence_status = "CONFIRMED"
        else:
            evidence_status = "INCOMPLETE"

        sample_time_utc = snapshot.sample_time_utc if snapshot else datetime.now(timezone.utc).isoformat()
        id_record["sample_time_utc"] = sample_time_utc
        id_record["evidence_status"] = evidence_status
        id_record["evidence_gaps"] = failure_reasons
        id_record["trigger"] = trigger
        id_record["first_frame_confirmed"] = self.first_frame_confirmed

        session_identity_fields: dict[str, object] = {
            "Evidence status": evidence_status,
            "Evidence gaps": "; ".join(failure_reasons) if failure_reasons else "none",
            "Trigger": trigger,
            "First frame confirmed": self.first_frame_confirmed,
            "Media frame": (
                f"{media_frame.source_width}x{media_frame.source_height} at {media_frame.time_pos:.6f}s "
                f"(path token: {media_frame.path_token})"
                if media_frame
                else "none"
            ),
            "Sample time (UTC)": sample_time_utc,
            "Process ID": id_record["process_id"],
            "Flatpak instance ID": id_record["flatpak_instance_id"] or "Unknown (not in Flatpak container)",
            "Flatpak app commit": id_record["app_commit"] or "Unknown",
            "Flatpak runtime commit": id_record["runtime_commit"] or "Unknown",
            "Identity verified": id_verified,
            "Identity status": id_status,
            "Claim boundary": (
                "CONFIRMED certifies only verified client-side texture publication and GdkColorState tagging; "
                "Gate 2W (Wayland protocol surface submission) and physical display photon output remain separate and uncertified."
            ),
        }

        report_text = build_video_output_report(
            snapshot.renderer,
            snapshot.output,
            snapshot.video,
            performance=snapshot.performance,
            session_identity=session_identity_fields,
        )

        report_sha256 = hashlib.sha256(report_text.encode("utf-8")).hexdigest()

        json_manifest = {
            "schema": "cinehdr.diagnostics.export.v1",
            "sample_time_utc": sample_time_utc,
            "trigger": trigger,
            "evidence_status": evidence_status,
            "evidence_gaps": failure_reasons,
            "failure_reason": "; ".join(failure_reasons) if failure_reasons else None,
            "first_frame_confirmed": self.first_frame_confirmed,
            "client_evidence_boundary": (
                "CONFIRMED certifies only verified client-side texture publication and GdkColorState tagging; "
                "Gate 2W (Wayland protocol surface submission) and physical display photon output remain separate and uncertified."
            ),
            "media_frame": {
                "source_width": media_frame.source_width,
                "source_height": media_frame.source_height,
                "time_pos": media_frame.time_pos,
                "path_token": media_frame.path_token,
            } if media_frame else None,
            "identity": id_record,
            "summary": {
                "requested_api": snapshot.renderer.get("Requested this session"),
                "active_api": snapshot.renderer.get("Active API"),
                "session_status": snapshot.renderer.get("Session status"),
                "startup_fallback_reason": snapshot.renderer.get("Startup fallback reason"),
                "renderer_failure_reason": snapshot.renderer.get("Renderer failure reason"),
                "configured_decoder": snapshot.video.get("Configured decoder"),
                "active_decoder": snapshot.video.get("Active decoder"),
                "render_target_format": snapshot.renderer.get("Render target format"),
                "render_target_depth": snapshot.renderer.get("Render target depth"),
                "gdk_color_state": snapshot.output.get("Gdk.ColorState"),
                "graphics_offload": snapshot.output.get("Graphics Offload"),
                "gl_renderer": snapshot.renderer.get("OpenGL renderer"),
                "gl_vendor": snapshot.renderer.get("OpenGL vendor"),
                "gl_version": snapshot.renderer.get("OpenGL version"),
            },
            "report_sha256": report_sha256,
            "target_file": str(self.export_path),
        }

        try:
            self.export_dir.mkdir(parents=True, exist_ok=True)
            txt_tmp = self.export_path.with_suffix(".tmp.txt")
            json_path = self.export_path.with_suffix(".json")
            json_tmp = self.export_path.with_suffix(".tmp.json")

            txt_tmp.write_text(report_text, encoding="utf-8")
            txt_tmp.replace(self.export_path)

            json_tmp.write_text(json.dumps(json_manifest, indent=2, default=str), encoding="utf-8")
            json_tmp.replace(json_path)

            if not generation_changed:
                self.export_completed = True
            self.export_status = evidence_status
            logger.info("Saved video output diagnostics to %s (evidence: %s)", self.export_path, evidence_status)
            return True, None
        except OSError as exc:
            err = f"write_error: {exc}"
            self.last_write_error = err
            logger.error("Failed to write diagnostics export: %s", exc)
            return False, err

    def on_first_media_frame(
        self,
        win: Any,
        media_frame: Any,
        context_generation: int | None = None,
        load_generation: int | None = None,
    ) -> tuple[bool, str | None]:
        """Triggered upon confirmation of the very first rendered and published media frame."""
        if self.export_completed:
            return True, None
        self.first_frame_confirmed = True
        self.confirmed_media_frame = media_frame
        ok, err = self.export_snapshot(
            win,
            trigger="first-media-frame-confirmed",
            media_frame=media_frame,
            context_generation=context_generation,
            load_generation=load_generation,
        )
        if not self.export_completed:
            self.first_frame_confirmed = False
            self.confirmed_media_frame = None
        return ok, err

    def on_first_frame(
        self,
        win: Any,
        media_frame: Any = None,
        context_generation: int | None = None,
        load_generation: int | None = None,
    ) -> tuple[bool, str | None]:
        """Backward-compatible entry point for first frame confirmation."""
        if self.export_completed:
            return True, None
        if media_frame is None and win is not None:
            mpv = getattr(win, "mpv", None)
            if mpv is None and hasattr(win, "player"):
                mpv = getattr(win.player, "mpv", None)
            if mpv is not None:
                try:
                    from .render_backend import media_frame_evidence
                    media_frame = media_frame_evidence(mpv)
                except Exception:
                    media_frame = None
        return self.on_first_media_frame(
            win,
            media_frame=media_frame,
            context_generation=context_generation,
            load_generation=load_generation,
        )

    def on_shutdown(self, win: Any) -> tuple[bool, str | None]:
        """Triggered on application or window close; records incomplete evidence if no media frame occurred."""
        if not self.export_completed:
            return self.export_snapshot(
                win,
                trigger="shutdown-before-first-frame",
                incomplete_reasons=["first_frame_unconfirmed: no video frame was published before shutdown"],
            )
        return True, None
