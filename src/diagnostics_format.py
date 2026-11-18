# diagnostics_format.py
#
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure Copy Report and status string formatters for CineHDR diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from gettext import gettext as _
import math
from typing import Any

_MEDIA_PATH_FIELDS = {"media path", "media_path", "file", "filename"}

DIAGNOSTICS_EXPORT_PATH_ENV = "CINEHDR_DIAGNOSTICS_EXPORT_PATH"
DIAGNOSTICS_EXPORT_DIR_ENV = "CINEHDR_DIAGNOSTICS_EXPORT_DIR"
EXPECTED_FLATPAK_INSTANCE_ENV = "CINEHDR_EXPECTED_FLATPAK_INSTANCE"
EXPECTED_APP_COMMIT_ENV = "CINEHDR_EXPECTED_APP_COMMIT"
EXPECTED_RUNTIME_COMMIT_ENV = "CINEHDR_EXPECTED_RUNTIME_COMMIT"
DIAGNOSTICS_SESSION_ID_ENV = "CINEHDR_DIAGNOSTICS_SESSION_ID"


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


def format_renderer_ui_labels(
    configured: str,
    requested_value: object,
    source_value: object,
    active: Any,
    session_status: str,
    fallback_reason: Any,
    failure_reason: Any,
    restart_reason: Any,
    preference_restart: bool,
    is_env_source: bool,
    runtime: dict[str, Any],
    target_format: Any,
    target_depth: Any,
    gpu_next_status: str,
    dovi_status: str,
) -> dict[str, str]:
    """Format UI presentation labels for the renderer section."""
    configured_label = _("GPU Next · Experimental") if configured == "gpu-next" else _("Standard · OpenGL")
    requested_label = _("{renderer} (source: {source})").format(renderer=requested_value, source=source_value)
    active_label = {
        "opengl": _("Standard · OpenGL"),
        "opengl-next": _("GPU Next · Experimental"),
    }.get(active, _("Not initialized"))
    if session_status in ("initialization-failure", "runtime-failure", "lifecycle-failure"):
        active_label = _("{renderer} — stopped; restart required").format(renderer=active_label)
    if gl_device := runtime.get("gl_renderer"):
        active_label = f"{active_label} ({gl_device})"

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

    return {
        "renderer_configured_row": configured_label,
        "renderer_requested_row": requested_label,
        "renderer_active_row": active_label,
        "renderer_reason_row": visible_status,
        "renderer_dependency_row": dependency_summary,
        "renderer_target_row": target_summary,
        "gpu_next_capability_row": gpu_next_status,
        "dovi_capability_row": dovi_status,
    }


def format_hwdec_str(hw_current: Any, hw_config_str: str) -> str:
    """Format hardware/software decoding status for diagnostics."""
    if hw_current and str(hw_current).lower() not in ("no", "none", ""):
        return f"{hw_current} ({_('GPU Acceleration active')}; configured: {hw_config_str})"
    if hw_current and str(hw_current).lower() in ("no", "none"):
        return f"{_('Software / CPU Decoding')} (active: no; configured: {hw_config_str})"
    if hw_current is None or str(hw_current).strip() == "":
        if hw_config_str != "unknown" and hw_config_str.lower() not in ("no", "none", ""):
            return f"{_('Unknown (configured: ')}{hw_config_str})"
        return _("Unknown")
    return f"{_('Software / CPU Decoding')} (active: {hw_current}; configured: {hw_config_str})"


def format_dovi_profile_desc(
    dovi: dict[str, Any],
    mapped_to_next: bool,
    active: Any,
    session_status: str,
) -> str:
    """Format Dolby Vision profile/level and RPU processing status string."""
    profile = dovi.get("profile")
    level = dovi.get("level")
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
    return f"{desc} · Level {level}" if level else desc
