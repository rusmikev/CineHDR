# diagnostics_format.py
#
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure Copy Report and status string formatters for CineHDR diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from gettext import gettext as _
import math

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
